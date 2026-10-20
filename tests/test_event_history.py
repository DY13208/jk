import json
import pytest
from fastapi.testclient import TestClient
from monitor.web import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/'events.db',scheduled=False)) as client:
        with client.app.state.db.connect() as conn:
            conn.execute('DELETE FROM monitor_events')
            for site,name in [('filter_a','Alpha'),('filter_b','Beta')]:
                conn.execute("INSERT INTO monitor_sites(id,name,url,adapter,event_rules,created_at) VALUES(?,?,'','css_catalog','[]','2026-01-01')",(site,name))
            # More than a page; prices differ numerically from lexical order.
            for i in range(120):
                kind='PRICE_DROP' if i%2 else 'NEW'
                payload={'product':{'name':f'Jacket {i:03}', 'sale_price':str(i+1), 'currency':'EUR' if i%3 else 'USD'},'old_price':str(i+11) if kind=='PRICE_DROP' else None}
                conn.execute('INSERT INTO monitor_events(site_id,product_id,kind,at,payload) VALUES(?,?,?,?,?)',('filter_a' if i<110 else 'filter_b',str(i),kind,'2026-10-18T16:00:00+00:00',json.dumps(payload)))
            for kind in ['REMOVED','RETURNED','PRICE_RISE','ACCESS_DENIED']:
                payload={'message':'blocked'} if kind=='ACCESS_DENIED' else {'product':{'name':'中文商品','sale_price':'200','currency':'EUR'},'old_price':'190'}
                conn.execute('INSERT INTO monitor_events(site_id,product_id,kind,at,payload) VALUES(?,?,?,?,?)',('filter_b',kind,kind,'2026-10-19T16:00:00+00:00',json.dumps(payload)))
        yield client


def test_filters_apply_before_pagination_and_counts(client):
    data=client.get('/api/events',params={'site_id':'filter_a','kind':'NEW','currency':'EUR','q':'jAcKeT','limit':5,'offset':5}).json()
    assert data['total']==36
    assert len(data['items'])==5
    assert all(r['site_id']=='filter_a' and r['kind']=='NEW' and r['payload']['product']['currency']=='EUR' for r in data['items'])
    assert data['kind_counts']['PRICE_DROP']==37
    assert data['currencies']==['EUR','USD']
    assert client.get('/api/events',params={'q':'中文'}).json()['total']==3
    assert client.get('/api/events',params={'q':"%' OR 1=1 --"}).json()['total']==0


def test_numeric_sort_pagination_ties_and_missing_prices(client):
    data=client.get('/api/events',params={'sort':'price_asc','limit':5}).json()
    assert [r['price'] for r in data['items']]==[1,2,3,4,5]
    data=client.get('/api/events',params={'sort':'price_desc','limit':500}).json()
    assert data['items'][0]['price']==200
    assert data['items'][-1]['kind']=='ACCESS_DENIED'
    first=client.get('/api/events',params={'sort':'time_asc','limit':100}).json()['items']
    second=client.get('/api/events',params={'sort':'time_asc','offset':100}).json()['items']
    assert len({r['id'] for r in first+second})==124
    data=client.get('/api/events',params={'sort':'change_asc','limit':500}).json()['items']
    assert data[0]['price_change']==-10
    assert data[60]['price_change']==10
    assert data[-1]['price_change'] is None
    for sort in ['site_asc','site_desc','name_asc','name_desc','change_desc']:
        assert client.get('/api/events',params={'sort':sort}).status_code==200


def test_date_boundaries_timezones_and_rejection(client):
    data=client.get('/api/events',params={'start':'2026-10-19T00:00:00+08:00','end':'2026-10-20T00:00:00+08:00'}).json()
    assert data['total']==120
    assert client.get('/api/events',params={'kind':'REMOVED'}).json()['total']==1
    for params in [{'start':'bad'},{'end':'2026-10-20'},{'start':'2026-10-21T00:00:00Z','end':'2026-10-20T00:00:00Z'}]:
        assert client.get('/api/events',params=params).status_code==400
    for params in [{'sort':'id;DROP TABLE monitor_events'},{'kind':'UNKNOWN'},{'offset':-1}]:
        assert client.get('/api/events',params=params).status_code==422
    assert client.get('/api/events').json()['total']==124
