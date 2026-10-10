"""Host-only Agent Mail outbox worker. Credentials remain in the official CLI keychain."""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from monitor.mail_templates import message, render_html


def cli_command():
    override = os.getenv('AGENTMAIL_CLI_PATH','')
    if override:
        path = Path(override)
        if not path.is_file() or path.suffix.lower() in ('.cmd','.bat'):
            raise ValueError('AGENTMAIL_CLI_PATH 请填写 CLI 原生可执行文件绝对路径')
        return [str(path.resolve())]
    if os.name == 'nt':
        # npm installs a native executable; invoke it directly without a shell.
        base=Path(os.getenv('APPDATA',''))/'npm/node_modules/@tencent-qqmail/agently-cli/node_modules'
        files=list(base.glob('@tencent-qqmail/agently-cli-win32-*/bin/agently-cli.exe'))
        if len(files)==1: return [str(files[0])]
    path=shutil.which('agently-cli')
    if not path: raise ValueError('未安装 agently-cli，请先按官方文档安装')
    return [path]


def invoke(command, args, cwd=None):
    return subprocess.run(command+args,cwd=cwd,capture_output=True,text=True,encoding='utf-8',timeout=45)


def status(conn, account='', text='工作进程运行中'):
    conn.execute("INSERT OR REPLACE INTO monitor_meta(key,value) VALUES('agentmail_worker',?)",
                 (json.dumps({'heartbeat':time.time(),'account':account,'message':text},ensure_ascii=False),))
    conn.commit()


def deliver_one(conn, command, runner=invoke):
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("UPDATE monitor_outbox SET status='uncertain',last_error='发送进程中断，需人工核对' WHERE status='sending' AND claimed_at<?",(time.time()-60,))
    row=conn.execute("""SELECT o.*,s.name,s.email_to FROM monitor_outbox o JOIN monitor_sites s ON s.id=o.site_id
        WHERE o.status='pending' AND o.next_at<=? AND s.notification_channel='agentmail' ORDER BY o.id LIMIT 1""",(time.time(),)).fetchone()
    if not row: conn.commit(); return False
    row=dict(row)
    site=dict(conn.execute('SELECT * FROM monitor_sites WHERE id=?',(row['site_id'],)).fetchone())
    event=dict(conn.execute('SELECT * FROM monitor_events WHERE id=?',(row['event_id'],)).fetchone())
    event['payload']=json.loads(event['payload'])
    conn.execute("UPDATE monitor_outbox SET status='sending',claimed_at=?,attempts=attempts+1 WHERE id=?",(time.time(),row['id']))
    conn.commit()
    state,reason,next_at='uncertain','Agent Mail 发送结果不确定，请核对邮箱后重试',0
    try:
        if not site['notify_enabled'] or not site['email_to']:
            state,reason='held','通知已关闭或未配置收件邮箱'
        else:
            import re
            addresses=[x.strip() for x in re.split('[,;；，]',site['email_to'])]
            if not addresses or any('\n' in x or '\r' in x or '@' not in x for x in addresses):
                raise ValueError('收件邮箱无效')
            # Only enabled site configuration constitutes authorization for automatic sending.
            with tempfile.TemporaryDirectory(prefix='product-monitor-mail-') as directory:
                Path(directory,'body.html').write_text(render_html(site,event),encoding='utf-8')
                args=['message','+send','--subject',message(site,event).splitlines()[0],
                      '--body-file','./body.html','--body-format','html','--confirmed']
                for address in dict.fromkeys(addresses): args+=['--to',address]
                result=runner(command,args,cwd=directory)
            try: body=json.loads(result.stdout)
            except ValueError: body={}
            if result.returncode==0 and body.get('ok') is True and (body.get('queued') is True or body.get('data',{}).get('queued') is True):
                state,reason='sent','Agent Mail 已接受并排队；最终收件状态请在邮箱中核对'
            elif result.returncode==7:
                attempts=row['attempts']+1
                state='pending' if attempts<5 else 'held'
                next_at=time.time()+min(3600,60*2**attempts)
                reason='Agent Mail 限频或额度用尽'
            elif result.returncode in (2,3,6):
                state,reason='held',{2:'Agent Mail 参数不合规',3:'Agent Mail 授权失效，请重新登录',6:'Agent Mail 业务拒绝'}[result.returncode]
            # Never log CLI output: it can contain credentials or private message metadata.
    except ValueError:
        state,reason='held','Agent Mail 收件邮箱配置无效'
    except Exception:
        pass
    conn.execute('UPDATE monitor_outbox SET status=?,last_error=?,next_at=?,sent_at=? WHERE id=?',
        (state,reason,next_at,datetime.now(timezone.utc).isoformat() if state=='sent' else None,row['id']))
    conn.commit()
    return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default=str(ROOT/'data/snapshots.db'))
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    command=cli_command()
    result=invoke(command,['+me'])
    if result.returncode:
        print('Agent Mail 尚未授权或连接失败；请运行 agently-cli auth login',file=sys.stderr);return 1
    body=json.loads(result.stdout)
    account=next((a['email'] for a in body.get('data',{}).get('aliases',[]) if a.get('is_primary')),'')
    conn=sqlite3.connect(args.db,timeout=30);conn.row_factory=sqlite3.Row
    print('Agent Mail 工作进程已启动；只处理已启用的 Agent Mail 通知。')
    try:
        while True:
            status(conn,account)
            deliver_one(conn,command)
            if args.once: return 0
            time.sleep(10)
    except KeyboardInterrupt: return 0
    finally:
        status(conn,account,'工作进程已停止')
        conn.execute("UPDATE monitor_meta SET value=json_set(value,'$.heartbeat',0) WHERE key='agentmail_worker'")
        conn.commit();conn.close()


if __name__=='__main__': raise SystemExit(main())
