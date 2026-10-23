"""Claim only due notifications from the same site and completed collection."""
import json
import time


def claim_digest(conn, row, event, claimed=None):
    rows, events = [dict(row)], [event]
    if event.get('run_id') is not None:
        others = conn.execute("""SELECT o.* FROM monitor_outbox o
            JOIN monitor_events e ON e.id=o.event_id
            WHERE o.site_id=? AND o.channel=? AND e.run_id=? AND o.status='pending'
            AND o.next_at<=? AND o.id!=? AND e.kind IN
            ('NEW','REMOVED','RETURNED','PRICE_DROP','PRICE_RISE') ORDER BY o.id""",
            (row['site_id'], row['channel'], event['run_id'], time.time(), row['id'])).fetchall()
        for other in others:
            item = dict(conn.execute('SELECT * FROM monitor_events WHERE id=?', (other['event_id'],)).fetchone())
            item['payload'] = json.loads(item['payload'])
            events.append(item)
            rows.append(dict(other))
            conn.execute("UPDATE monitor_outbox SET status='sending',claimed_at=?,attempts=attempts+1 WHERE id=?",
                         (claimed if claimed is not None else time.time(), other['id']))
    if len(events) > 1:
        event = {**event, 'kind': 'DIGEST', 'payload': {'events': events}}
    return rows, event
