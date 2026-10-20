"""Bounded daily aggregates; never transfer full history to the browser."""
from datetime import datetime, timedelta, timezone


def trends(db, days=90, offset_minutes=480):
    local = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    end = local.date().isoformat()
    start = (local.date() - timedelta(days=days-1)).isoformat()
    modifier = f'{offset_minutes:+d} minutes'
    result = {}
    with db.connect() as conn:
        for key, table, column, condition in (
            ('snap', 'monitor_logs', 'started', "status='SUCCESS' AND run_id IS NOT NULL"),
            ('chg', 'monitor_events', 'at', '1=1')):
            result[key] = dict(conn.execute(
                f'''SELECT date({column},?),COUNT(*) FROM {table}
                    WHERE {condition} AND julianday({column})>=julianday(?,?)
                    AND julianday({column})<julianday(?,?,'+1 day')
                    GROUP BY date({column},?)''',
                (modifier, start, f'{-offset_minutes:+d} minutes', end,
                 f'{-offset_minutes:+d} minutes', modifier)).fetchall())
        result['latest'] = [dict(row) for row in conn.execute('''SELECT l.* FROM monitor_logs l
            JOIN (SELECT site_id,MAX(id) id FROM monitor_logs WHERE site_id IS NOT NULL GROUP BY site_id) m ON l.id=m.id''')]
        row = conn.execute("SELECT * FROM monitor_logs WHERE status='SUCCESS' ORDER BY id DESC LIMIT 1").fetchone()
        result['last_success'] = dict(row) if row else None
    return result
