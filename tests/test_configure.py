from scripts.configure import payload


def test_each_site_has_interval_and_native_notifications(monkeypatch):
    monkeypatch.setenv('TEST_WECOM', 'wecombot://dummykey')
    monkeypatch.setenv('TEST_TELEGRAM', 'tgram://123:dummy/456')
    data = payload('one', {'enabled': True, 'interval_minutes': 7,
                          'notification_env': ['TEST_WECOM', 'TEST_TELEGRAM']}, 'http://catalog:8080')
    assert data['notification_urls'] == ['wecombot://dummykey', 'tgram://123:dummy/456']
    assert data['time_between_check'] == {'minutes': 7}
    assert data['notification_body'] == '{{ diff_added }}'
    assert not data['paused']
    assert not data['notification_muted']


def test_default_fifteen_and_paused():
    data = payload('two', {}, 'http://catalog:8080')
    assert data['paused'] and data['time_between_check'] == {'minutes': 15}
    assert data['notification_muted']
