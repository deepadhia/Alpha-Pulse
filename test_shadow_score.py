# -*- coding: utf-8 -*-
"""Tests for the LOGGED-ONLY shadow score (core/shadow_score.py, order_sheet.py hook).  No MongoDB, no network, no scikit-learn needed."""
import json
import sys
import types

import numpy as np
import pandas as pd
import pytest

import core.shadow_score as ss
import research_event_model as rem


def make_symbol(listing_date, n=60, event_idx=30, base=100.0, seed=0, with_event=True):
    dates = pd.bdate_range(listing_date, periods=n)
    rng = np.random.default_rng(seed)
    close = np.clip(base + rng.normal(0, 0.4, n), base - 2.0, base + 0.8)
    open_ = np.clip(close + rng.normal(0, 0.2, n), base - 2.0, base + 0.8)
    high = np.maximum(open_, close) + 0.3
    low = np.minimum(open_, close) - 0.3
    vol = rng.integers(200000, 400000, n).astype(float)
    open_[0], high[0], low[0], close[0], vol[0] = 96.0, 101.0, 94.0, 99.0, 5_000_000.0
    if with_event:
        open_[event_idx], high[event_idx], low[event_idx], close[event_idx], vol[event_idx] = 101.5, 105.0, 101.2, 104.0, 900_000.0
    df = pd.DataFrame(dict(DATE=dates, OPEN=open_, HIGH=high, LOW=low, CLOSE=close, VOLUME=vol))
    li = dict(symbol="X", listing_date=pd.Timestamp(dates[0], tz="UTC"), listing_day_high=101.0, listing_day_low=94.0,
              listing_day_volume=5_000_000.0)
    return df, li


@pytest.fixture()
def world():
    candle_map, listing_map = {}, {}
    d0 = pd.Timestamp("2026-01-05")
    for sym, (seed, ev) in {"AAA": (1, True), "BBB": (2, False), "CCC": (3, False)}.items():
        df, li = make_symbol(d0, seed=seed, with_event=ev)
        candle_map[sym], listing_map[sym] = df, dict(li, symbol=sym)
    event_date = candle_map["AAA"].DATE.iloc[30]
    d_list = (event_date - pd.Timedelta(days=20)).normalize()
    df, li = make_symbol(d_list, n=25, with_event=False, seed=4, base=60.0)
    df.loc[0, ["OPEN", "HIGH", "LOW", "CLOSE"]] = [57.0, 61.0, 56.0, 59.0]
    li.update(symbol="DDD", listing_day_high=61.0, listing_day_low=56.0)
    candle_map["DDD"], listing_map["DDD"] = df, li
    return candle_map, listing_map, event_date


def test_core_features_match_the_research_event_table(world):
    candle_map, listing_map, event_date = world
    S, mk, lds = rem.prep(listing_map, candle_map)
    E = rem.events(S, mk, lds)
    assert len(E) == 1 and E.symbol.iloc[0] == "AAA"
    row = E.iloc[0]
    df = candle_map["AAA"]
    before = df[df.DATE < event_date]
    mkt = ss.market_state(candle_map, listing_map, event_date)
    sup = ss.supply30([v["listing_date"] for v in listing_map.values()], event_date)
    f = ss.entry_features(before, listing_map["AAA"], event_date, mkt=mkt, supply=sup, open_price=float(df.OPEN.iloc[30]))
    for k in ss.FEATS:
        a, b = row[k], f[k]
        if a is None or (isinstance(a, float) and np.isnan(a)):
            assert b is None or np.isnan(b), k
        else:
            assert b is not None and np.isclose(float(a), float(b), rtol=1e-9, atol=1e-9), f"{k}: research {a} vs core {b}"
    assert sup >= 1          # the synthetic world includes a recent listing, so supply30 is exercised, not trivially zero


def test_pre_open_features_have_no_gap_and_no_score(world):
    candle_map, listing_map, event_date = world
    before = candle_map["AAA"][candle_map["AAA"].DATE < event_date]
    f = ss.entry_features(before, listing_map["AAA"], event_date, mkt={"breadth": 0.4, "med_r10": 0.01}, supply=3)
    assert f["gap"] is None and f["logp"] is None
    assert ss.score(f, {"model": object(), "feats": ss.FEATS, "medians": {}}) == (None, "awaiting_gap")


def test_score_never_raises_and_reports_why():
    f = dict(zip(ss.FEATS, [1.0] * len(ss.FEATS)))

    class Stub:
        def predict_proba(self, X):
            return np.array([[0.3, 0.7]])

    class Broken:
        def predict_proba(self, X):
            raise ImportError("sklearn gone")

    meta = dict(feats=ss.FEATS, medians={k: 0.0 for k in ss.FEATS})
    assert ss.score(f, None) == (None, "no_model")
    assert ss.score(None, dict(meta, model=Stub())) == (None, "no_features")
    assert ss.score(f, dict(meta, model=Stub())) == (0.7, "ok")
    s, status = ss.score(f, dict(meta, model=Broken()))
    assert s is None and status == "score_error:ImportError"
    f2 = dict(f, vdry3=None)                      # a missing feature is imputed with the training median, not an error
    assert ss.score(f2, dict(meta, model=Stub())) == (0.7, "ok")


def test_too_little_history_gives_no_features(world):
    candle_map, listing_map, event_date = world
    assert ss.entry_features(candle_map["AAA"].head(8), listing_map["AAA"], event_date) is None


def test_jsonl_helpers_are_idempotent(tmp_path):
    p = str(tmp_path / "scores.jsonl")
    assert ss.append_jsonl(p, [dict(date="2026-10-01", symbol="AAA", score=0.4), dict(date="2026-10-01", symbol="BBB", score=None)]) == 2
    assert ss.already_scored(p) == {("2026-10-01", "AAA"), ("2026-10-01", "BBB")}
    assert ss.append_jsonl(p, []) == 0


def test_order_sheet_hook_never_changes_the_orders(world, monkeypatch):
    import order_sheet as osheet
    from core.order_sheet import SheetConfig, build_resting_entries
    candle_map, listing_map, event_date = world
    listings = {"AAA": dict(listing_map["AAA"], symbol="AAA")}
    candles = {"AAA": candle_map["AAA"][candle_map["AAA"].DATE < event_date].reset_index(drop=True)}
    pv = dict(trigger=102.52, limit=103.53, fill=102.83, stop=92.0, risk_pct=10.5, rr=1.6, age=42, last_close=100.1, already_above_trigger=False)
    monkeypatch.setattr(osheet, "load_watchlist", lambda today, p: (listings, candles, {}))
    monkeypatch.setattr(osheet, "preview_resting_entry", lambda df, li, today, p: dict(pv))
    fake_db = types.SimpleNamespace(listing_data_col=types.SimpleNamespace(find=lambda *a, **k: []))
    monkeypatch.setitem(sys.modules, "db", fake_db)
    cands, notes = osheet.resting_candidates(event_date, osheet.Params())
    assert len(cands) == 1 and cands[0]["shadow"]["status"] == "awaiting_gap"
    assert set(cands[0]["shadow"]["features"]) == set(ss.PRE_OPEN_FEATS)
    with_shadow = build_resting_entries(cands, slots_used=0, cfg=SheetConfig(capital=100000))
    stripped = [{k: v for k, v in c.items() if k != "shadow"} for c in cands]
    without = build_resting_entries(stripped, slots_used=0, cfg=SheetConfig(capital=100000))
    assert with_shadow[0] == without[0] and with_shadow[1] == without[1]      # identical orders, identical skip notes
    assert json.dumps(cands[0]["shadow"], default=str)                        # serialisable into the order log


def test_shadow_hook_failure_is_swallowed(world, monkeypatch):
    import order_sheet as osheet
    out = osheet.shadow_pre_open(pd.DataFrame(), {"listing_date": "bad"}, pd.Timestamp("2026-02-01"), None, None)
    assert out["status"].startswith(("error:", "no_features"))
