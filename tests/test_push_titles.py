from monitor import push

SITE = {'name': 'Bergzeit FR', 'url': 'https://www.bergzeit.fr/'}


def prod(name="Arc'teryx Beta SL Jacket", price='240', cur='EUR', orig=None):
    return {'name': name, 'sale_price': price, 'original_price': orig, 'currency': cur, 'url': 'https://www.bergzeit.fr/p/x/1/'}


def ev(kind, p=None, old=None, i=1):
    payload = {'product': p or prod()}
    if old is not None: payload['old_price'] = old
    return {'id': i, 'kind': kind, 'payload': payload}


def test_price_drop_title_leads_with_type_and_percent_and_strips_brand():
    n = push.build(SITE, [ev('PRICE_DROP', old='300')])
    assert n['title'] == '降价 -20% · Beta SL Jacket'
    assert n['body'].splitlines() == ['€300.00 → €240.00', 'Bergzeit FR', "Arc'teryx Beta SL Jacket"]
    assert n['tags'] == ['chart_with_downwards_trend'] and n['urgent'] and n['url'].endswith('/p/x/1/')


def test_each_kind_has_short_label_and_emoji_tag():
    for kind, word, tag in [('NEW', '上新', 'new'), ('REMOVED', '下架', 'x'), ('RETURNED', '补货', 'recycle'),
                            ('PRICE_RISE', '涨价 +25%', 'chart_with_upwards_trend')]:
        n = push.build(SITE, [ev(kind, old='192' if kind == 'PRICE_RISE' else None)])
        assert n['title'] == f'{word} · Beta SL Jacket' and n['tags'] == [tag]


def test_brand_variants_and_chinese_brand_are_stripped():
    for name in ["ARC’TERYX Atom Hoody", 'Arcteryx - Atom Hoody', 'arc\'teryx Atom Hoody']:
        assert push.short_name(name) == 'Atom Hoody'
    assert push.short_name('始祖鸟Atom 连帽衫') == 'Atom 连帽衫'
    assert push.short_name("Arc'teryx") == "Arc'teryx"  # nothing left: keep the full name


def test_long_and_chinese_names_are_clamped_with_ellipsis_and_full_name_in_body():
    long = '始祖鸟 Atom 轻量保暖连帽夹克 男款 超长名称测试用例一二三四五六七八'
    n = push.build(SITE, [ev('NEW', prod(long, orig='300'))])
    assert len(n['title']) <= push.TITLE_MAX and n['title'].endswith('…') and n['title'].startswith('上新 · Atom 轻量')
    assert n['body'].splitlines() == ['€240.00（原价 €300.00）', 'Bergzeit FR', long]
    short = push.build(SITE, [ev('NEW', prod('Atom Hoody'))])
    assert short['title'] == '上新 · Atom Hoody' and short['body'].splitlines() == ['€240.00', 'Bergzeit FR']


def test_digest_title_counts_changes_and_lists_items():
    events = [ev('NEW', i=1), ev('PRICE_DROP', prod('始祖鸟 Gamma 软壳裤'), old='300', i=2), ev('REMOVED', i=3)]
    n = push.build(SITE, events)
    assert n['title'] == '3 个变化 · Bergzeit FR'
    assert n['body'].splitlines()[1] == '降价 -20% · Gamma 软壳裤 · €300.00 → €240.00'
    digest = push.build(SITE, [{'id': 9, 'kind': 'DIGEST', 'payload': {'events': events[:2]}}])
    assert digest['title'] == '2 个变化 · Bergzeit FR' and len(digest['body'].splitlines()) == 2


def test_bark_group_is_site_name_and_ntfy_carries_tags_and_click():
    n = push.build(SITE, [ev('PRICE_DROP', old='300')])
    assert push.payload('bark', n, {'device_key': 'k'}, 'Bergzeit FR')['group'] == 'Bergzeit FR'
    body = push.payload('ntfy', n, {'topic': 't'})
    assert body['tags'] == ['chart_with_downwards_trend'] and body['click'] == 'https://www.bergzeit.fr/p/x/1/'


def test_digest_title_drops_brand_suffix_from_site_name_but_body_keeps_it():
    site = {'name': "Bergzeit FR - Arc'teryx", 'url': 'https://www.bergzeit.fr/'}
    assert push.short_site("Bergzeit FR - Arc'teryx") == 'Bergzeit FR' and push.short_site('Lecomte') == 'Lecomte'
    assert push.build(site, [ev('NEW', i=1), ev('REMOVED', i=2), ev('NEW', i=3)])['title'] == '3 个变化 · Bergzeit FR'
    assert "Bergzeit FR - Arc'teryx" in push.build(site, [ev('NEW')])['body']
