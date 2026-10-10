import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from adapters import REGISTRY
from crawl4ai_run.run_crawl4ai import CollectionError
from .notifications import Notifier


class MonitorService:
    def __init__(self, db, adapters=None):
        self.db = db
        self.adapters = adapters if adapters is not None else REGISTRY
        self.scheduler = AsyncIOScheduler(timezone='UTC')
        self.notifier = Notifier(db)
        self.browser_slot = asyncio.Semaphore(1)
        self.active = set()
        self.tasks = set()

    def start(self):
        with self.db.connect() as db:
            db.execute("UPDATE monitor_logs SET status='INTERRUPTED',ended=datetime('now'),message='服务重启；未完成任务未提交快照' WHERE status='RUNNING'")
        self.scheduler.start()
        self.scheduler.add_job(self.notifier.dispatch,'interval',seconds=10,id='notifications',max_instances=1,coalesce=True)
        self.scheduler.add_job(self.backup,'cron',hour=2,minute=0,id='backup',max_instances=1,coalesce=True)
        self.sync_jobs()

    def sync_jobs(self):
        sites = self.db.sites()
        enabled = {'site:'+s['id'] for s in sites if s['enabled'] and s['permission_confirmed']}
        for job in self.scheduler.get_jobs():
            if job.id.startswith('site:') and job.id not in enabled: self.scheduler.remove_job(job.id)
        for site in sites:
            if site['enabled'] and site['permission_confirmed']:
                job = self.scheduler.get_job('site:'+site['id'])
                if job:
                    if job.trigger.interval != timedelta(minutes=site['interval_minutes']):
                        self.scheduler.reschedule_job(job.id,trigger='interval',minutes=site['interval_minutes'])
                    continue
                self.scheduler.add_job(self.run,'interval',minutes=site['interval_minutes'],args=[site['id']],
                    id='site:'+site['id'],max_instances=1,coalesce=True,misfire_grace_time=60,
                    next_run_time=datetime.now(timezone.utc)+timedelta(seconds=5))

    async def backup(self):
        try: await asyncio.to_thread(self.db.backup)
        except Exception as exc:
            log = self.db.log_start(None)
            self.db.log_finish(log,'FAILED','备份失败：'+type(exc).__name__)

    def launch(self, site_id):
        task = asyncio.create_task(self.run(site_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def run(self, site_id):
        site = self.db.site(site_id)
        if not site or not site['enabled'] or not site['permission_confirmed']:
            return {'status':'DISABLED'}
        if site_id in self.active: return {'status':'BUSY'}
        self.active.add(site_id)
        owner, log = uuid.uuid4().hex, None
        acquired = False
        try:
            async with self.browser_slot:
                acquired = self.db.acquire(site_id,owner,360)
                if not acquired: return {'status':'BUSY'}
                # Recheck after waiting for the shared browser slot.
                site = self.db.site(site_id)
                if not site['enabled']: return {'status':'DISABLED'}
                log = self.db.log_start(site_id)
                document = await asyncio.wait_for(self.adapters[site['adapter']](site),timeout=300)
                # Administrative pause while collection is in flight also prevents commit.
                if not self.db.site(site_id)['enabled']:
                    self.db.log_finish(log,'CANCELLED','采集期间网站被暂停，未提交快照')
                    return {'status':'CANCELLED'}
                rid, changes = self.db.commit(site_id,document)
                self.db.log_finish(log,'SUCCESS',f"完整采集 {len(document['products'])} 件；变化 {len(changes)} 条",rid)
                return {'status':'SUCCESS','run_id':rid,'events':changes}
        except asyncio.CancelledError:
            if log: self.db.log_finish(log,'INTERRUPTED','任务取消；未提交快照')
            raise
        except Exception as exc:
            reason = str(exc) if isinstance(exc,CollectionError) else '采集失败：'+type(exc).__name__
            if log:
                self.db.log_finish(log,'FAILED',reason)
                if isinstance(exc,CollectionError) and exc.status in (403,429):
                    self.db.pause(site_id,reason,log)
                    job = self.scheduler.get_job('site:'+site_id)
                    if job: self.scheduler.remove_job(job.id)
            return {'status':'FAILED','message':reason}
        finally:
            if acquired: self.db.release(site_id,owner)
            self.active.discard(site_id)

    async def stop(self):
        self.scheduler.shutdown(wait=False)
        for task in self.tasks: task.cancel()
        if self.tasks: await asyncio.gather(*self.tasks,return_exceptions=True)
