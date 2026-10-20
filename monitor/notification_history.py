"""Non-secret delivery evidence and read-only outbox previews."""
import json
import re
from datetime import datetime, timezone
from .database import run_summary
from .mail_templates import message, subject, render_html

CHANNELS={'email':'邮件（SMTP / Gmail）','agentmail':'Agent Mail','weixin':'微信扫码直连','wecom':'企业微信机器人','pushplus':'个人微信（pushplus）','unknown':'历史未留存'}


def snapshot(site, text, *, channel=None, recipients=None, recipient_label='', sender='', title='', html=''):
    channel=channel or site.get('notification_channel','wecom')
    variable=site.get('webhook_env','')
    variable=variable if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',variable) else ''
    if not recipient_label:
        recipient_label=', '.join(recipients or []) or (
            '扫码绑定的微信（未上报用户 ID）' if channel=='weixin' else
            '机器人对应群聊'+(' · '+variable if variable else '（未提供群名）') if channel=='wecom' else
            'Token 绑定的微信'+(' · '+variable if variable else '（未提供用户名称）') if channel=='pushplus' else '未记录接收人')
    return {'channel':channel,'recipients':recipients or [],'recipient_label':recipient_label,'sender':sender,
            'subject':title or text.splitlines()[0][:180] if text else title,'text':text,'html':html,
            'captured_at':datetime.now(timezone.utc).isoformat()}


def record(db,row_id,value):
    with db.connect() as conn:
        conn.execute('UPDATE monitor_outbox SET delivery_snapshot=? WHERE id=?',(json.dumps(value,ensure_ascii=False),row_id))


def planned(site):
    channel=site.get('notification_channel','wecom')
    return snapshot(site,'',recipients=[a.strip() for a in re.split('[,;；，]',site.get('email_to','')) if a.strip()] if channel in ('email','agentmail') else [])


def logged_channel(reason):
    for prefix,channel in [('Agent Mail','agentmail'),('SMTP','email'),('企业微信','wecom'),('pushplus','pushplus'),('腾讯微信','weixin'),('微信','weixin')]:
        if (reason or '').startswith(prefix): return channel
    return 'unknown'


CHANNEL_EXPR="""COALESCE(json_extract(o.delivery_snapshot,'$.channel'),
    CASE WHEN o.status='pending' AND o.attempts=0 THEN s.notification_channel
    WHEN o.last_error LIKE 'Agent Mail%' THEN 'agentmail'
    WHEN o.last_error LIKE 'SMTP%' THEN 'email'
    WHEN o.last_error LIKE '企业微信%' THEN 'wecom'
    WHEN o.last_error LIKE 'pushplus%' THEN 'pushplus'
    WHEN o.last_error LIKE '腾讯微信%' OR o.last_error LIKE '微信%' THEN 'weixin'
    ELSE 'unknown' END)"""


def _row(row):
    row=dict(row)
    captured=json.loads(row.pop('delivery_snapshot')) if row.get('delivery_snapshot') else None
    event=json.loads(row.pop('event_payload'))
    current=planned({'notification_channel':row.pop('current_channel') or 'wecom','email_to':row.pop('current_to') or '', 'webhook_env':row.pop('current_variable') or ''})
    # A current site setting is not proof of any historical delivery.
    basis='captured' if captured else 'planned' if row['status']=='pending' and row['attempts']==0 else 'legacy'
    evidence=captured or current if basis!='legacy' else None
    row.update(channel=evidence['channel'] if evidence else logged_channel(row['last_error']),channel_name=CHANNELS.get(evidence['channel'],'未知渠道') if evidence else CHANNELS[logged_channel(row['last_error'])],
               recipient=evidence['recipient_label'] if evidence else '历史记录未留存接收人',
               sender=evidence.get('sender','') if evidence else '',basis=basis,
               subject=(captured or {}).get('subject') or event.get('product',{}).get('name') or event.get('message',''),
               captured_at=(captured or {}).get('captured_at'),current_configuration={k:current[k] for k in ('channel','recipient_label')})
    return row,captured,event


BASE=''' FROM monitor_outbox o JOIN monitor_events e ON e.id=o.event_id
    LEFT JOIN monitor_sites s ON s.id=o.site_id'''
SELECT='''SELECT o.*, e.at AS event_at,e.run_id,e.kind,e.payload AS event_payload,s.name AS site_name,
    s.notification_channel AS current_channel,s.email_to AS current_to,s.webhook_env AS current_variable'''


def listing(db, *, site_id='',status='',channel='',limit=100,offset=0):
    clauses,args=[],[]
    for field,value in [('o.site_id',site_id),('o.status',status)]:
        if value: clauses.append(field+'=?');args.append(value)
    if channel:
        clauses.append(CHANNEL_EXPR+'= ?')
        args.append(channel)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    with db.connect() as conn:
        total=conn.execute('SELECT COUNT(*)'+BASE+where,args).fetchone()[0]
        rows=[_row(r)[0] for r in conn.execute(SELECT+BASE+where+' ORDER BY o.id DESC LIMIT ? OFFSET ?',[*args,limit,offset])]
    return {'items':rows,'total':total}


def detail(db,row_id):
    with db.connect() as conn:
        result=conn.execute(SELECT+BASE+' WHERE o.id=?',(row_id,)).fetchone()
        if not result: raise LookupError('通知不存在')
        row,captured,payload=_row(result)
        site={'id':row['site_id'],'name':row['site_name'] or row['site_id']}
        event={'id':row['event_id'],'kind':row['kind'],'at':row['event_at'],'payload':payload}
        summary=run_summary(conn,result['run_id']) if 'run_id' in result.keys() else None
    row['preview']={'source':'captured' if captured else 'reconstructed',
                    'text':captured['text'] if captured else message(site,event),
                    'html':captured.get('html','') if captured else render_html(site,event,summary),
                    'subject':captured.get('subject','') if captured else subject(site,event)}
    return row
