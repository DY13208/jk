"""A site's notification channels: a JSON list of {channel, env, to}, with the legacy single-channel
fields (notification_channel / webhook_env / email_to) kept as the first entry and as the fallback.

Only environment-variable *names* and recipient addresses are stored here; secrets stay in .env."""
import json

IDS = ('wecom', 'email', 'agentmail', 'pushplus', 'weixin', 'bark', 'ntfy')
USES_ENV = ('wecom', 'pushplus', 'bark', 'ntfy')
USES_TO = ('email', 'agentmail')


def normalize(items):
    """Clean a list of entries: known channel, env only where used, recipients only where used, one per channel."""
    result, seen = [], set()
    for item in items or []:
        if not isinstance(item, dict): continue
        channel = str(item.get('channel') or '').strip()
        if channel not in IDS or channel in seen: continue
        seen.add(channel)
        result.append({'channel': channel,
                       'env': str(item.get('env', item.get('webhook_env')) or '').strip() if channel in USES_ENV else '',
                       'to': str(item.get('to', item.get('email_to')) or '').strip() if channel in USES_TO else ''})
    return result


def legacy(site):
    channel = site.get('notification_channel') or 'wecom'
    return normalize([{'channel': channel, 'env': site.get('webhook_env') or '', 'to': site.get('email_to') or ''}])


def parse(site):
    """Entries for a site row/dict; an empty or unreadable list falls back to the legacy single fields."""
    raw = site.get('notification_channels')
    if isinstance(raw, str):
        try: raw = json.loads(raw) if raw.strip() else []
        except ValueError: raw = []
    return normalize(raw) or legacy(site)


def mirror(site, entries):
    """Legacy fields that mirror the primary (first) entry, for old readers and old API clients."""
    first = entries[0]
    return {'notification_channel': first['channel'],
            'webhook_env': first['env'] if first['channel'] in USES_ENV else site.get('webhook_env') or '',
            'email_to': first['to'] if first['channel'] in USES_TO else site.get('email_to') or ''}


def effective(site, entry):
    """The site as seen by one channel's handler: that channel's env name / recipients in the legacy slots."""
    return {**site, 'notification_channel': entry['channel'], 'webhook_env': entry['env'], 'email_to': entry['to']}


def entry(site, channel):
    return next((e for e in parse(site) if e['channel'] == channel), None)


def channel_ids(site): return [e['channel'] for e in parse(site)]



def reconcile(site, stored=None):
    """Entries to store for a saved site. When the list and the legacy single fields agree, or the legacy fields
    are unchanged from the stored site, the list wins. When an old client/script edited the legacy fields (they
    differ from the stored ones and from the list's primary entry), that edit replaces the primary entry and the
    other selected channels are kept."""
    raw = site.get('notification_channels')
    if not raw: return legacy(site)
    entries = parse(site)
    old = legacy(site)[0]
    if stored is not None and entries and old != entries[0] and old != legacy(stored)[0]:
        primary = entries[0]['channel']
        entries = normalize([old] + [e for e in entries if e['channel'] not in (old['channel'], primary)])
    return entries
