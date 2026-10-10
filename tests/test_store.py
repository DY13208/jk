import copy
import pytest
from monitor.store import Incomplete, Store


def product(i, price='10.00'):
    return {'id': str(i), 'sku': f'SKU-{i}', 'name': f'Product {i}', 'price': price,
            'original_price': '20', 'url': f'https://example.test/p/{i}', 'stock': 'in_stock'}


def commit(db, products, expected=None, site='one'):
    return db.commit(site, products, {'complete': True, 'expected': len(products) if expected is None else expected},
                     ['added', 'removed', 'relisted', 'price_changed', 'stock_changed'])


@pytest.fixture
def db(tmp_path):
    return Store(str(tmp_path / 'test.sqlite'))


def test_ninety_two_removed_two_added(db):
    first = [product(i) for i in range(90)]
    assert commit(db, first) == []
    assert db.feed('one') == 'catalog-events-v1\n'
    changes = commit(db, [product(i) for i in range(2, 92)])
    assert {(kind, p['id']) for kind, p, _ in changes} == {
        ('removed', '0'), ('removed', '1'), ('added', '90'), ('added', '91')}
    assert len(db.latest('one')) == 90
    assert len(db.feed('one').splitlines()) == 5


def test_relist_and_price_return(db):
    commit(db, [product(1), product(2)])
    commit(db, [product(2)])
    changes = commit(db, [product(1), product(2, '9')])
    assert {x[0] for x in changes} == {'relisted', 'price_changed'}
    assert commit(db, [product(1), product(2)])[0][0] == 'price_changed'
    assert len(db.feed('one').splitlines()) == 5


def test_reorder_and_price_format_no_change(db):
    commit(db, [product(1), product(2)])
    assert commit(db, [product(2, '10'), product(1, '10.0')]) == []
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 2


@pytest.mark.parametrize('invalid', ['total', 'duplicate', 'missing', 'nan', 'incomplete'])
def test_bad_run_retains_snapshot(db, invalid):
    commit(db, [product(1), product(2)])
    previous = copy.deepcopy(db.latest('one'))
    data = [product(1)]
    expected, complete = 1, True
    if invalid == 'total': expected = 2
    if invalid == 'duplicate': data, expected = [product(1), product(1)], 2
    if invalid == 'missing': data[0]['id'] = None
    if invalid == 'nan': data[0]['price'] = 'NaN'
    if invalid == 'incomplete': complete = False
    with pytest.raises(Incomplete):
        db.commit('one', data, {'complete': complete, 'expected': expected}, ['removed'])
    assert db.latest('one') == previous
    assert len(db.feed('one').splitlines()) == 1


def test_isolation_rules_and_empty(db):
    commit(db, [product(1)])
    commit(db, [product(1)], site='two')
    changes = db.commit('one', [], {'complete': True, 'expected': 0}, ['added'])
    assert changes[0][0] == 'removed'
    assert db.latest('two')[0]['id'] == '1'
    assert db.feed('one') == 'catalog-events-v1\n'


def test_stock_and_discount(db):
    commit(db, [product(1)])
    p = product(1)
    p['stock'] = 'out_of_stock'
    assert commit(db, [p])[0][0] == 'stock_changed'
    assert db.latest('one')[0]['discount'] == '50.00'
