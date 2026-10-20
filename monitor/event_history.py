"""Server-side event filters and deterministic ordering before pagination."""
import json

KINDS = ('NEW', 'REMOVED', 'RETURNED', 'PRICE_DROP', 'PRICE_RISE', 'ACCESS_DENIED')
SORTS = {
    'time_desc': 'julianday(e.at) DESC', 'time_asc': 'julianday(e.at) ASC',
    'site_asc': 'COALESCE(s.name,e.site_id) COLLATE NOCASE ASC',
    'site_desc': 'COALESCE(s.name,e.site_id) COLLATE NOCASE DESC',
    'name_asc': "json_extract(e.payload,'$.product.name') COLLATE NOCASE ASC",
    'name_desc': "json_extract(e.payload,'$.product.name') COLLATE NOCASE DESC",
    'price_asc': 'price ASC', 'price_desc': 'price DESC',
    'change_asc': 'price_change ASC', 'change_desc': 'price_change DESC',
}
PRICE = "CAST(NULLIF(json_extract(e.payload,'$.product.sale_price'),'') AS REAL)"
CHANGE = f"CASE WHEN e.kind IN ('PRICE_DROP','PRICE_RISE') THEN {PRICE}-CAST(NULLIF(json_extract(e.payload,'$.old_price'),'') AS REAL) END"


def query_events(db, *, site_id=None, kind=None, q='', start=None, end=None, currency='', sort='time_desc', limit=100, offset=0):
    if sort not in SORTS: raise ValueError('无效排序')
    if kind and kind not in KINDS: raise ValueError('无效事件分类')
    clauses, args = [], []
    if site_id:
        clauses.append('e.site_id=?'); args.append(site_id)
    if q:
        clauses.append("instr(casefold(COALESCE(json_extract(e.payload,'$.product.name'),'') || ' ' || e.product_id || ' ' || COALESCE(json_extract(e.payload,'$.message'),'')),?)>0")
        args.append(q.casefold())
    if start:
        clauses.append('julianday(e.at)>=julianday(?)'); args.append(start)
    if end:
        clauses.append('julianday(e.at)<julianday(?)'); args.append(end)
    if currency:
        clauses.append("UPPER(json_extract(e.payload,'$.product.currency'))=?"); args.append(currency.upper())
    base = ' FROM monitor_events e LEFT JOIN monitor_sites s ON s.id=e.site_id'
    where = (' WHERE '+' AND '.join(clauses)) if clauses else ''
    with db.connect() as conn:
        conn.create_function('casefold',1,lambda value:str(value or '').casefold(),deterministic=True)
        counts = dict(conn.execute('SELECT e.kind, COUNT(*)'+base+where+' GROUP BY e.kind',args).fetchall())
        if kind:
            where += (' AND ' if where else ' WHERE ')+'e.kind=?'; args.append(kind)
        total = conn.execute('SELECT COUNT(*)'+base+where,args).fetchone()[0]
        # Missing product/price values always follow meaningful values in either direction.
        null_order = ('price IS NULL, ' if sort.startswith('price_') else
                      'price_change IS NULL, ' if sort.startswith('change_') else
                      "json_extract(e.payload,'$.product.name') IS NULL, " if sort.startswith('name_') else '')
        order = null_order+SORTS[sort]+', e.id '+('ASC' if sort.endswith('_asc') else 'DESC')
        rows = [dict(r) for r in conn.execute(
            f'SELECT e.*,s.name AS site_name,{PRICE} AS price,{CHANGE} AS price_change'+base+where+
            ' ORDER BY '+order+' LIMIT ? OFFSET ?', [*args,limit,offset])]
        currencies = [r[0] for r in conn.execute("SELECT DISTINCT UPPER(json_extract(payload,'$.product.currency')) AS c FROM monitor_events WHERE c IS NOT NULL AND c!='' ORDER BY c")]
    for row in rows: row['payload'] = json.loads(row['payload'])
    return {'items':rows,'total':total,'kind_counts':counts,'currencies':currencies}
