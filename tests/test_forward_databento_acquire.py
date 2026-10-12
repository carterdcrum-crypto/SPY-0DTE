"""Historical OPRA acquisition must be explicit and budget-fail-closed."""
from datetime import date,datetime,timedelta,timezone
import csv
import pytest

from engine.forward_databento_acquire import acquire_one_day

D=date(2026,10,12)
T=datetime(2026,10,12,13,30,tzinfo=timezone.utc)
OCC="SPY261012C00600000"


class MockMeta:
    def __init__(self,option_cost=.2):
        self.option_cost=option_cost
        self.calls=[]
    def get_cost(self,**args):
        self.calls.append(args)
        if args["schema"]=="cmbp-1":
            return self.option_cost
        return .05


class MockTimeseries:
    def __init__(self):
        self.calls=[]
    def get_range(self,**kwargs):
        self.calls.append(kwargs)
        if kwargs["schema"]=="ohlcv-1m":
            return [{"ts_event":T+timedelta(minutes=i),
                     "open":600,"high":600,"low":600,"close":600,"volume":1000}
                    for i in range(50)]
        if kwargs["schema"]=="definition":
            return [{"raw_symbol":"SPY  261012C00600000",
                     "expiration":D.isoformat(),"instrument_class":"C",
                     "strike_price":600}]
        if kwargs["schema"]=="cmbp-1":
            return [{"symbol":OCC,"ts_recv":T+timedelta(minutes=40,seconds=17),
                     "publisher_id":30,"bid_px_00":1.0,
                     "ask_px_00":1.02,"bid_sz_00":40,"ask_sz_00":40}]
        raise AssertionError("unexpected paid data request")


class MockClient:
    def __init__(self,option_cost=.2):
        self.metadata=MockMeta(option_cost)
        self.timeseries=MockTimeseries()


def test_capped_event_acquisition_creates_vendor_verified_raw_files(tmp_path):
    c=MockClient()
    receipt=acquire_one_day(c,D,tmp_path/"raw",max_usd=1.0)
    assert receipt["estimated_cost_usd"]==.3
    assert receipt["usable_event_quotes"]==1
    assert len(c.timeseries.calls)==3
    with (tmp_path/"raw"/f"raw_opra_events_{D}.csv").open() as f:
        rows=list(csv.DictReader(f))
    assert rows[0]["schema"]=="cmbp-1"
    assert rows[0]["publisher_id"]=="30"
    with pytest.raises(FileExistsError):
        acquire_one_day(c,D,tmp_path/"raw",max_usd=1.0)


def test_option_estimate_gate_never_downloads_opra_if_over_budget(tmp_path):
    c=MockClient(option_cost=10)
    with pytest.raises(ValueError,match="OPRA CMBP-1 cost exceeds"):
        acquire_one_day(c,D,tmp_path/"raw",max_usd=1.0)
    assert [x["schema"] for x in c.timeseries.calls]==["ohlcv-1m","definition"]
    assert not (tmp_path/"raw"/f"raw_opra_events_{D}.csv").exists()


def test_base_estimate_gate_no_provider_data_requests(tmp_path):
    c=MockClient()
    with pytest.raises(ValueError,match="nothing downloaded"):
        acquire_one_day(c,D,tmp_path/"raw",max_usd=.01)
    assert c.timeseries.calls==[]
    with pytest.raises(ValueError,match="positive explicitly"):
        acquire_one_day(c,D,tmp_path/"raw",max_usd=0)
