"""Read-only business status; process liveness remains a separate endpoint."""
import time
from datetime import datetime, timezone

from . import host_collector


def business_health(db, service, weixin=None):
    """weixin: bridge status dict from the async caller (None = bridge not checked)."""
    from .channels import site_issue, NAMES
    from . import agentmail
    clock = time.time()
    agent = None
    workers = {mode: host_collector.status(db, mode) for mode in ('host', 'extension')}
    sites = []
    with db.connect() as conn:
        for site in db.sites():
            success = conn.execute(
                "SELECT ended FROM monitor_logs WHERE site_id=? AND status='SUCCESS' ORDER BY id DESC LIMIT 1",
                (site['id'],)).fetchone()
            latest = conn.execute(
                'SELECT status FROM monitor_logs WHERE site_id=? ORDER BY id DESC LIMIT 1',
                (site['id'],)).fetchone()
            mode = site['adapter_config'].get('execution_mode', 'docker')
            progress = dict(service.progress.get(site['id'], {}))
            reasons = []
            age = None
            if success and success['ended']:
                stamp = datetime.fromisoformat(success['ended'].replace('Z', '+00:00'))
                age = max(0, clock - stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp())
            if site['enabled']:
                if not site['permission_confirmed']: reasons.append('待核对访问许可')
                if mode in workers and not workers[mode]['running']: reasons.append('采集端未连接')
                if age is None: reasons.append('尚无成功采集记录')
                elif age > max(site['interval_minutes'] * 120, 1800): reasons.append('采集数据已过期')
                if latest and latest['status'] in ('FAILED', 'INTERRUPTED'): reasons.append('最近采集未成功')
                if site['notify_enabled']:
                    channel = site.get('notification_channel') or 'wecom'
                    if channel == 'agentmail' and agent is None: agent = agentmail.status(db)
                    issue = site_issue(db, site, weixin, agent)
                    if issue: reasons.append(f"通知未配置（{NAMES.get(channel, channel)}）：{issue}")
            elif site.get('paused_reason'):
                reasons.append(site['paused_reason'])
            if progress:
                progress['waiting_seconds'] = round(max(0, progress.get('started_at', clock) - progress['queued_at']))
            sites.append({'site_id':site['id'], 'state':'attention' if reasons else 'paused' if not site['enabled'] else 'ok',
                          'reasons':reasons, 'last_success':success['ended'] if success else None,
                          'age_seconds':round(age) if age is not None else None, 'task':progress})
        counts = dict(conn.execute('SELECT status,COUNT(*) FROM monitor_outbox GROUP BY status').fetchall())
    attention = sum(counts.get(key, 0) for key in ('held', 'uncertain'))
    return {'ok':not attention and not any(s['reasons'] for s in sites), 'sites':sites,
            'workers':workers, 'notifications':{'counts':counts, 'attention':attention}, 'checked_at':clock}
