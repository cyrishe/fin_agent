from scripts.optimize_finance_date_indexes import has_prefix


def index(columns, name='idx', **kwargs):
    return [dict(Key_name=name, Seq_in_index=i+1, Column_name=column,
                 Sub_part=None, Visible='YES') | kwargs for i, column in enumerate(columns)]


def test_existing_index_leading_columns_are_reused():
    rows = index(['code', 'date', 'id'])
    assert has_prefix(rows, ['code', 'date'])
    assert has_prefix(rows, ['code'])
    assert not has_prefix(rows, ['date'])
    assert not has_prefix(rows, ['code', 'other'])


def test_prefix_or_invisible_index_does_not_claim_full_coverage():
    rows = index(['code', 'date'])
    rows[0]['Sub_part'] = 20
    assert not has_prefix(rows, ['code', 'date'])
    assert not has_prefix(rows, ['date'])
    assert not has_prefix(index(['date'], Visible='NO'), ['date'])
    assert has_prefix(rows + index(['date'], name='date_idx'), ['date'])
