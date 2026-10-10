"""Run inside changedetection container: real Apprise, mocked remote HTTP only."""
from unittest.mock import Mock, patch
import apprise


def main():
    response = Mock(status_code=200, content=b'{}')
    response.json.return_value = {'ok': True, 'result': {'message_id': 1}, 'errcode': 0, 'errmsg': 'ok'}
    for url in ('wecombot://dummy_test_key', 'tgram://123456789:dummy_test_token/123456'):
        client = apprise.Apprise()
        assert client.add(url), 'Native plugin URL rejected'
        with patch('requests.post', return_value=response) as post:
            assert client.notify(title='Catalog', body='added SKU-90; removed SKU-0'), 'Native notify failed'
            assert post.called, 'No delivery request generated'
            call = post.call_args
            target = call.args[0] if call.args else call.kwargs.get('url', '')
            assert ('qyapi.weixin.qq.com' in target if url.startswith('wecombot:') else 'api.telegram.org' in target)
        print(url.split(':')[0] + ': native Apprise request passed (mock HTTP, no real delivery)')


if __name__ == '__main__':
    main()
