# -*- coding: utf-8 -*-
"""
core/shadow_score.py -- LOGGED-ONLY shadow score for resting breakout orders (never gates, sizes or changes an order).

What it is: the gradient-boosting selection model from research_event_model.py (2026-10-17).  Walk-forward on 263 resting-eligible breakout events
it predicted "+20% above the fill within 10 sessions" with out-of-sample AUC 0.70 (permutation p = 0.000), but its realised-net link was
unproven (63 matched trades, CI -5.5..+19.5pp).  So it is only LOGGED, to be judged on live out-of-sample days (shadow_score.py --report).

Single source of truth for the feature formulas: research_event_model.events() uses the same definitions, and test_shadow_score.py asserts that
this module reproduces them on synthetic data.  Everything here uses only information available before the entry bar, plus the opening gap
(known after 9:15, so the final score is computed after the session from the daily candle; the pre-market log carries the other features).

Nothing here touches MongoDB; callers pass DataFrames.  scikit-learn is optional at import time: without it features are still produced and the
score is None with a status that says why.
"""
import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

FEATS = ["age", "dist_lh", "gap", "ret5", "ret10", "atr10", "range10", "vdry3", "vprev", "turn20", "ld_range", "logp", "ldv_ratio",
         "supply30", "breadth", "med_r10"]
PRE_OPEN_FEATS = [f for f in FEATS if f not in ("gap", "logp")]     # known before the open without a fill price
TRIGGER_PCT, LIMIT_PCT, SLIP_PCT = 1.5, 2.5, 0.3
MODEL_PATH = os.path.join("models", "shadow_gbm_v1.pkl")
SCORES_LOG = os.path.join("logs", "shadow_scores.jsonl")
MODEL_PARAMS = dict(n_estimators=150, max_depth=2, learning_rate=0.05, subsample=0.8, random_state=0)


def _naive(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None).normalize() if ts.tzinfo else ts.normalize()


def _dates(df: pd.DataFrame) -> pd.Series:
    s = pd.to_datetime(df["DATE"])
    return s.dt.tz_localize(None) if getattr(s.dt, "tz", None) is not None else s


def market_state(candle_map: Dict[str, pd.DataFrame], listing_map: Dict[str, dict], asof) -> dict:
    """Breadth and median 10-day return of active IPOs (age 3-60) on the last completed session before `asof`."""
    asof = _naive(asof)
    last_rows = []
    for sym, df in candle_map.items():
        li = listing_map.get(sym)
        if li is None or df is None or len(df) < 2:
            continue
        d = df[_dates(df) < asof]
        if len(d) < 1:
            continue
        d = d.reset_index(drop=True)
        last_date = _naive(d["DATE"].iloc[-1])
        last_rows.append((sym, last_date, d))
    if not last_rows:
        return dict(breadth=np.nan, med_r10=np.nan, n_active=0)
    latest = max(r[1] for r in last_rows)
    above, r10s = [], []
    for sym, last_date, d in last_rows:
        if last_date != latest:
            continue
        li = listing_map[sym]
        age = (latest - _naive(li["listing_date"])).days
        if not (3 <= age <= 60):
            continue
        c = d["CLOSE"].astype(float).values
        ldh = float(li.get("listing_day_high") or d["HIGH"].iloc[0])
        above.append(c[-1] > ldh)
        if len(c) > 10:
            r10s.append(c[-1] / c[-11] - 1)
    return dict(breadth=float(np.mean(above)) if above else np.nan, med_r10=float(np.median(r10s)) if r10s else np.nan, n_active=len(above))


def supply30(listing_dates: List, asof) -> int:
    asof = _naive(asof)
    return int(sum(1 for x in listing_dates if asof - pd.Timedelta(days=30) <= _naive(x) < asof))


def entry_features(df_before: pd.DataFrame, li: dict, asof, mkt: Optional[dict] = None, supply: Optional[int] = None,
                   open_price: Optional[float] = None) -> Optional[dict]:
    """
    Features for a breakout order on session `asof`, from completed sessions BEFORE it (df_before, index 0 = listing day).
    `open_price` (the session open) adds gap and logp; without it those two are None (pre-market log).
    """
    d = df_before.reset_index(drop=True)
    i = len(d)                       # index of the entry bar in the full series
    if i < 12:
        return None
    o, h, l, c, v = (d[k].values.astype(float) for k in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"))
    ld = _naive(li["listing_date"])
    asof = _naive(asof)
    ldh = float(li.get("listing_day_high") or h[0])
    ldl = float(li.get("listing_day_low") or l[0])
    ldv = float(li.get("listing_day_volume") or v[0])
    trig = ldh * (1 + TRIGGER_PCT / 100.0)
    turn20 = float((c[i - 20:i] * v[i - 20:i]).mean()) / 1e7 if i >= 20 else float((c[:i] * v[:i]).mean()) / 1e7
    vb = v[max(1, i - 13):i - 3]
    avg20 = v[max(0, i - 20):i].mean()
    f = dict(
        age=int((asof - ld).days), dist_lh=(c[i - 1] / ldh - 1) * 100, gap=None, ret5=(c[i - 1] / c[i - 6] - 1) * 100,
        ret10=(c[i - 1] / c[i - 11] - 1) * 100,
        atr10=float(((h[i - 10:i] - l[i - 10:i]) / c[i - 10:i]).mean() * 100),
        range10=(h[i - 10:i].max() - l[i - 10:i].min()) / c[i - 1] * 100,
        vdry3=v[i - 3:i].mean() / vb.mean() if len(vb) >= 2 and vb.mean() > 0 else np.nan,
        vprev=v[i - 1] / avg20 if avg20 > 0 else np.nan, turn20=turn20, ld_range=(ldh - ldl) / ldh * 100, logp=None,
        ldv_ratio=ldv / avg20 if avg20 > 0 else np.nan, supply30=supply, breadth=(mkt or {}).get("breadth"), med_r10=(mkt or {}).get("med_r10"))
    if open_price is not None:
        f["gap"] = (float(open_price) / c[i - 1] - 1) * 100
        f["logp"] = float(np.log(max(float(open_price), trig) * (1 + SLIP_PCT / 100.0)))
    return {k: (None if (isinstance(x, float) and np.isnan(x)) else x) for k, x in f.items()}


# ----------------------------------------------------------------------------- model

def train_model(events: pd.DataFrame, path: str = MODEL_PATH) -> dict:
    """Fit the same model as the research (depth 2, 150 trees, lr 0.05) on ALL events and save it."""
    import pickle
    import sklearn
    from sklearn.ensemble import GradientBoostingClassifier
    X = events[FEATS]
    med = X.median()
    mdl = GradientBoostingClassifier(**MODEL_PARAMS).fit(X.fillna(med), events["y20"])
    meta = dict(model=mdl, feats=FEATS, medians=med.to_dict(), version="gbm_d2_v1", n_events=int(len(events)),
                train_start=str(pd.to_datetime(events["date"]).min().date()), train_end=str(pd.to_datetime(events["date"]).max().date()),
                sklearn=sklearn.__version__, positives=int(events["y20"].sum()))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(meta, fh)
    return {k: v for k, v in meta.items() if k != "model"}


def load_model(path: str = MODEL_PATH) -> Optional[dict]:
    import pickle
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None


def score(features: dict, model_meta: Optional[dict]) -> Tuple[Optional[float], str]:
    """(score, status).  Never raises: a missing model, a missing sklearn or a missing gap gives (None, reason)."""
    if model_meta is None:
        return None, "no_model"
    if features is None:
        return None, "no_features"
    if features.get("gap") is None or features.get("logp") is None:
        return None, "awaiting_gap"
    try:
        row = pd.DataFrame([{k: (np.nan if features.get(k) is None else features.get(k)) for k in model_meta["feats"]}])
        row = row.fillna(pd.Series(model_meta["medians"]))
        return float(model_meta["model"].predict_proba(row[model_meta["feats"]])[:, 1][0]), "ok"
    except Exception as e:  # sklearn missing or version mismatch: log the reason, never break the caller
        return None, f"score_error:{type(e).__name__}"


def append_jsonl(path: str, rows: List[dict]) -> int:
    if not rows:
        return 0
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    return len(rows)


def already_scored(path: str = SCORES_LOG) -> set:
    done = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    done.add((r.get("date"), r.get("symbol")))
                except Exception:
                    pass
    return done
