import importlib
from datetime import datetime
import pandas as pd
import pytest

from scripts.backfill_b1_history import CLOCKS, raw_summary, training_window, price


def minute_frame():
    return pd.DataFrame([dict(symbol='002636.SZ',time='2026-10-15 '+clock,open=10.,high=11.,low=9.,close=10.,volume=100.,turnover=1000.) for clock in CLOCKS])


def test_signal_uses_only_complete_prefix_and_future_does_not_change_it():
    b=minute_frame();signal,morning=raw_summary(b,'2026-10-15')
    later=pd.DataFrame([dict(symbol='002636.SZ',time='2026-10-15 15:00:00',open=1,high=1,low=1,close=1,volume=999999,turnover=999999)])
    signal2,morning2=raw_summary(pd.concat([b,later]),'2026-10-15')
    pd.testing.assert_frame_equal(signal,signal2);pd.testing.assert_frame_equal(morning,morning2)
    assert signal.iloc[0].minute_volume_shares==22000
    assert signal.iloc[0].amount==220000
    assert morning.iloc[0].second_high==11 and morning.iloc[0].close_40==10


def test_signal_gap_does_not_remove_observed_morning_exit():
    b=minute_frame().iloc[:-1]
    signal,morning=raw_summary(b,'2026-10-15')
    assert signal.empty
    assert morning.iloc[0].second_high==11 and morning.iloc[0].close_40==10
    b=b[b.time!='2026-10-15 09:31:00']
    _,morning=raw_summary(b,'2026-10-15')
    assert pd.isna(morning.iloc[0].second_high) and morning.iloc[0].close_40==10


def test_missing_exit_stays_unknown_and_invalid_price_cannot_be_a_signal():
    b=minute_frame();b.loc[b.time.str.endswith('09:40:00'),'close']=0
    signal,morning=raw_summary(b,'2026-10-15')
    assert signal.empty and morning.empty


def test_duplicate_or_wrong_date_rejected():
    b=minute_frame()
    with pytest.raises(ValueError):raw_summary(pd.concat([b,b.iloc[:1]]),'2026-10-15')
    with pytest.raises(ValueError):raw_summary(b,'2026-10-14')


def test_training_uses_exact_past20_and_only_mature_labels():
    days=pd.bdate_range('2026-08-01',periods=25).strftime('%Y-%m-%d').tolist()
    c=pd.DataFrame([dict(signal_date=day,next_date=days[i+1],second_high=11) for i,day in enumerate(days[:-1])])
    train,window=training_window(c,days,days[-1])
    assert window==days[-21:-1] and set(train.signal_date)==set(window)
    c.loc[c.signal_date==window[0],'next_date']='2027-01-01'
    with pytest.raises(ValueError):training_window(c,days,days[-1])


def test_database_price_precision_and_unknown():
    assert str(price(12.34567))=='12.3457'
    assert price(float('nan')) is None and price(None) is None


@pytest.mark.parametrize('previous_sell',[None,10.5,99])
def test_history_writer_records_d0_buy_d1_sell_and_preserves_existing_exit(tmp_path,monkeypatch,previous_sell):
    from contextlib import contextmanager
    from decimal import Decimal
    from types import SimpleNamespace
    from scripts import backfill_b1_history as m
    from scripts import run_automl_1440_strategy as runner
    m.save_json(tmp_path/'folds.json',[dict(day='2026-01-05',candidate_count=20,model_sha256='m',input_sha256='i',training_sha256='t',training_dates=['2025-12-31'])])
    pd.DataFrame([dict(signal_date='2026-01-05',next_date='2026-01-06',symbol6='002636',name='金安国纪',selection_rank=1,signal_price=10,close_40=10.5,actionable=True),
                  dict(signal_date='2026-01-05',next_date='2026-01-06',symbol6='002131',name='利欧股份',selection_rank=2,signal_price=11,close_40=12,actionable=False)]).to_csv(tmp_path/'picks.csv',index=False)
    calls=[];saved=[];commits=[];rollbacks=[]
    class Cursor:
        def execute(self,sql,args):calls.append((sql,args))
        def fetchone(self):return dict(actionable=1,buy_price=Decimal('10'),sell_price=Decimal(str(previous_sell)) if previous_sell else None,sell_at='2026-01-06 09:40:00' if previous_sell else None)
    class DB:
        @contextmanager
        def cursor(self):yield Cursor()
        def commit(self):commits.append(1)
        def rollback(self):rollbacks.append(1)
    @contextmanager
    def connection(_):yield DB()
    monkeypatch.setattr(runner,'write_connection',connection)
    monkeypatch.setattr(m,'record_daily_run',lambda *a,**kw:None)
    monkeypatch.setattr(m,'record_selection',lambda *a,**kw:saved.append(kw))
    args=SimpleNamespace(output=tmp_path,env_file=None,user_id='imac',version='v1.1')
    if previous_sell==99:
        with pytest.raises(ValueError,match='exit differs'):m.write_results(args)
        assert rollbacks and not commits
        return
    m.write_results(args)
    assert saved[0]['buy_at']=='2026-01-05 14:40:00' and saved[0]['buy_price']==Decimal('10')
    assert saved[1]['buy_price'] is None and saved[1]['buy_at'] is None
    updates=[args for sql,args in calls if sql.startswith('UPDATE')]
    assert len(updates)==(previous_sell is None)
    if updates:assert updates[0][:4]==('2026-01-06 09:40:00',Decimal('10.5'),'09:40_minute_close_proxy',Decimal('5'))
    assert commits and not rollbacks


def test_legacy_csv_boundary_preserves_archived_float_contract_only_for_legacy_period():
    from scripts.backfill_b1_history import legacy_csv_floats, FEATURES
    # Raw recomputed return vs. the value consumed by the archived B1 fitter.
    raw=0.05193482688391038
    frame=pd.DataFrame([{**{c:1. for c in FEATURES},'signal_date':day,
                         'signal_return':raw,'signal_price':10.33,'second_high':10.4}
                        for day in ['2025-12-30','2026-01-13','2026-08-03']])
    actual=legacy_csv_floats(frame)
    assert actual.loc[1,'signal_return']==0.0519348268839103
    assert actual.loc[0,'signal_return']==raw and actual.loc[2,'signal_return']==raw
    assert actual.signal_date.tolist()==frame.signal_date.tolist()
    assert actual.signal_price.tolist()==frame.signal_price.tolist()


def test_empty_current_morning_uses_api_later_but_missing_history_is_an_error(monkeypatch):
    from scripts import backfill_b1_history as m
    class Today(datetime):
        @classmethod
        def now(cls,zone):return cls(2026,10,16,12,0,tzinfo=zone)
    monkeypatch.setattr(m,'datetime',Today)
    monkeypatch.setattr(m,'query',lambda *a:pd.DataFrame())
    signal,morning=m.database_summary(None,'2026-10-16',signal=False)
    assert signal.empty and list(morning.columns)==['symbol6','second_high','close_40']
    with pytest.raises(ValueError,match='no morning'):m.database_summary(None,'2026-10-15',signal=False)
    with pytest.raises(ValueError,match='no morning'):m.database_summary(None,'2026-10-16',signal=True)


def test_api_exit_backfill_is_only_for_selected_current_morning(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from decimal import Decimal
    from types import SimpleNamespace
    from scripts import backfill_b1_history as m
    from scripts import readable_strategy_execution as api
    class Today(datetime):
        @classmethod
        def now(cls,zone):return cls(2026,10,16,12,0,tzinfo=zone)
    monkeypatch.setattr(m,'datetime',Today)
    rows=[dict(signal_date='2026-10-15',next_date='2026-10-16',symbol6='002636',actionable=True,close_40=float('nan')),
          dict(signal_date='2026-10-15',next_date='2026-10-16',symbol6='002131',actionable=False,close_40=float('nan')),
          dict(signal_date='2026-10-14',next_date='2026-10-15',symbol6='002698',actionable=True,close_40=float('nan'))]
    pd.DataFrame(rows).to_csv(tmp_path/'picks.csv',index=False)
    (tmp_path/'models/2026-10-15').mkdir(parents=True)
    calls=[]
    class DB:
        @contextmanager
        def cursor(self):yield None
    @contextmanager
    def connection(_):yield DB()
    monkeypatch.setattr(m,'db_connection',connection)
    def close(cur,day,code,**kwargs):calls.append((day,code));return Decimal('10.55')
    monkeypatch.setattr(api,'api_0940_close',close)
    m.refresh_api_exits(SimpleNamespace(output=tmp_path,env_file=None))
    actual=m.read(tmp_path/'picks.csv')
    assert calls==[('2026-10-16','002636')]
    assert actual.iloc[0].close_40==10.55 and actual.iloc[0].exit_price_basis=='09:40_minute_api_close_proxy'
    assert actual.iloc[1:].close_40.isna().all()
    m.refresh_api_exits(SimpleNamespace(output=tmp_path,env_file=None))
    assert len(calls)==1
