"""Production market reader reuses trusted collector cycles, never repolls Webull."""
from datetime import date, datetime, timedelta, timezone

from engine.collector import SnapshotStore
from engine.providers.webull_free import FreeOptionSnapshot
from engine.shared_live_quotes import collector_live_quotes
from engine.live_market import LiveMarketReader
from engine.greeks import ModelGreeks


def snapshot(now, *, source="webull_production", timestamp_quality="option_quote_timestamp"):
    return FreeOptionSnapshot(
        timestamp=now-timedelta(milliseconds=200),
        received_at=now-timedelta(milliseconds=100),
        timestamp_quality=timestamp_quality,
        feed_delay_seconds=.1,
        option_symbol="SPY261019C00660000",
        expiration=date(2026, 10, 19), right="call",
        strike=660., bid=1.05, ask=1.07, bid_size=8, ask_size=10,
        underlying_price=661., minutes_to_expiry=200,
        volume=300, open_interest=400,
        greeks=ModelGreeks(implied_volatility=.2, delta=.5, gamma=.01,
                           theta_per_day=-.1, vega_per_vol_point=.03),
        source=source, underlying_timestamp=now-timedelta(milliseconds=200),
    )


def test_sole_collector_live_reader_preserves_verified_bid_depth_and_greeks(tmp_path):
    now=datetime(2026,10,19,14,30,2,tzinfo=timezone.utc)
    path=tmp_path/"market.sqlite"
    store=SnapshotStore(path)
    store.insert([snapshot(now)])
    store.close()
    received=collector_live_quotes(path,now)
    assert len(received)==1
    assert received[0].option_symbol=="SPY261019C00660000"
    assert received[0].bid_size==8 and received[0].ask_size==10
    assert received[0].greeks.delta == .5
    market=LiveMarketReader()
    market.ingest(received,now)
    assert market.fresh_quote("SPY261019C00660000",now).bid==1.05
    assert collector_live_quotes(path, now+timedelta(seconds=4))==()


def test_collector_live_reader_rejects_sandbox_and_untrusted_timestamps(tmp_path):
    now=datetime(2026,10,19,14,30,2,tzinfo=timezone.utc)
    for source, quality in [("webull_sandbox_delayed","option_quote_timestamp"),
                             ("webull_production","receive_time_only")]:
        path=tmp_path/(source+"_"+quality+".sqlite")
        store=SnapshotStore(path)
        store.insert([snapshot(now,source=source,timestamp_quality=quality)])
        store.close()
        assert collector_live_quotes(path,now)==()


def test_collector_pins_open_option_even_when_strike_far_from_spot():
    from engine.providers.webull_free import WebullFreeDataProvider
    provider=WebullFreeDataProvider(data_client=object(),active_contract_limit=2)
    contracts=tuple({"symbol":f"SPY261019C{int(strike*1000):08d}",
                     "strike_price":strike} for strike in [600,660,661,662])
    provider.pinned_symbols={contracts[0]["symbol"]}
    result=provider.select_contracts(contracts,spot=661)
    assert len(result)==2
    assert result[0]["symbol"]==contracts[0]["symbol"]
    assert result[1]["symbol"]==contracts[2]["symbol"]
