from fastapi.testclient import TestClient
from monitor.web import create_app
from monitor.email_delivery import configuration


def test_gmail_private_settings_and_delivery(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr('monitor.gmail.send_email',lambda config,mail: calls.append(mail) or {})
    with TestClient(create_app(tmp_path/'test.db',scheduled=False)) as client:
        assert not client.get('/api/gmail/status').json()['configured']
        secret='abcdefghijklmnop'
        assert client.put('/api/gmail/settings',json={'account':'sender@gmail.com','password':secret}).status_code==200
        assert secret not in client.put('/api/gmail/settings',json={'account':'sender@gmail.com','password':secret*10}).text
        assert secret not in client.get('/api/gmail/status').text
        assert secret.encode() not in (tmp_path/'gmail.enc').read_bytes()
        assert client.put('/api/gmail/settings',json={'account':'other@gmail.com'}).status_code==400
        assert client.put('/api/gmail/settings',json={'account':'sender@gmail.com'}).status_code==200
        assert configuration({'email_to':'to@example.com'},client.app.state.db)['host']=='smtp.gmail.com'
        draft=dict(to='to@example.com',subject='hello',body='正文',request_id='unique-request-id-1234',confirm=True,
                   attachments=[dict(name='test.txt',data='aGVsbG8=')])
        assert client.post('/api/gmail/send',json={**draft,'confirm':False}).status_code==400
        assert client.post('/api/gmail/send',json=draft).json()['status']=='sent'
        assert client.post('/api/gmail/send',json=draft).json()['status']=='sent'
        assert len(calls)==1
        assert list(calls[0].iter_attachments())[0].get_payload(decode=True)==b'hello'
        draft.update(request_id='unique-request-id-5678',subject='injected\nBcc: bad@example.com')
        assert client.post('/api/gmail/send',json=draft).status_code==400
        draft.update(subject='hello')
        def fail(*args): raise TimeoutError(secret)
        monkeypatch.setattr('monitor.gmail.send_email',fail)
        response=client.post('/api/gmail/send',json=draft)
        assert response.json()['status']=='uncertain'
        assert secret not in response.text
        assert secret not in client.get('/api/gmail/status').text
