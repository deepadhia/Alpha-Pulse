# -*- coding: utf-8 -*-
"""
research_outlier_anatomy.py -- what do the outlier trades of policy P have in common, tested across 2006-2026 (added 2026-11-07).

WHY THIS IS NOT A REPEAT: LEARNINGS 13, 17.x, 20.x searched for outlier markers on 2022-26 only (about 230 trades, 18 price/volume features,
no index data) and found none that survived.  Now there are 419 P trades over two independent eras, and the Nifty history (2007-) gives
market-regime features that were never tested.  The design is "discover in one era, confirm in the other" in BOTH directions.

PRE-REGISTRATION (written before running)
  Population: policy-P trades (live exits, resting entries, flat 1.0% cost), era A = entries 2007-09-17..2022-05-26 (panel proxy; Nifty history
  starts 2007-09-17), era B = 2022-05-27.. (Atlas).  Outlier label: net >= +25% (about 10% of trades).  Second label: X1-exit net >= +25%.
  Features known BEFORE the order is placed (prior-session data only): age, ext, vol_ratio, rr, initial risk, listing-day range, listing-day
  close location, gap, ret5, ret10, atr10, range10, distance from listing high, turnover decay (20-day turnover / listing-day turnover), IPO supply
  in the prior 30 days, and Nifty regime: 63-day return, above 200-day average, 21-day volatility, drawdown from the 252-day high.
  A feature is a CANDIDATE MARKER only if its rank AUC for the outlier label is on the same side of 0.5 in BOTH eras and at least 0.60 (or at
  most 0.40) in both.  17 features x 2 labels => several false candidates are expected by chance; a candidate is then tested as an account-level
  filter with its threshold fixed on the OTHER era (median), against the unfiltered X1 book, and must raise Calmar and CAGR in both eras.
  Nothing is adopted from this script; a pass is a candidate for the forward test only.

  .venv\\Scripts\\python.exe research_outlier_anatomy.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
"""
import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
import research_alpha_lab as lab
import research_improve_history as ih
import research_max_history as mh
from core.execution_model import CostModel
from core.honest_backtest import LIVE_FAITHFUL, Params, _listing_context
from core.portfolio_sim import PortfolioConfig, run_portfolio
from core.setup3 import NO_TIME_EXITS

NIFTY = r"D:\Personal Projects\Finance-Guru\research_archive_2026-10\studies\index_sleeve_2026-10\data\nifty50.csv"
COSTM = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)
NIFTY_START = pd.Timestamp("2007-09-17")
FEATS = ["age", "ext", "vol_ratio", "rr", "risk", "ld_range", "ld_close_loc", "gap", "ret5", "ret10", "atr10", "range10", "dist_prev",
         "turn_decay", "supply30", "n_ret63", "n_above200", "n_vol21", "n_dd252"]


def auc(x, y):
    """Rank AUC of x for the positive class y (0.5 = no information)."""
    x, y = np.asarray(x, float), np.asarray(y, bool)
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok]
    n1, n0 = y.sum(), (~y).sum()
    if n1 < 5 or n0 < 5:
        return float("nan")
    r = pd.Series(x).rank().values
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def nifty_features(nifty):
    c = nifty
    f = pd.DataFrame(index=c.index)
    f["n_ret63"] = c / c.shift(63) - 1
    f["n_above200"] = (c > c.rolling(200).mean()).astype(float).where(c.rolling(200).mean().notna())
    f["n_vol21"] = c.pct_change().rolling(21).std() * 100
    f["n_dd252"] = c / c.rolling(252, min_periods=60).max() - 1
    return f.shift(1)      # only information up to the prior close


def trade_features(d, cm, lm, nf, ld_dates):
    rows = []
    for r in d.itertuples():
        df = cm[r.symbol]
        i = int(r.entry_idx)
        c, h, l, o, v = (df[k].values.astype(float) for k in ("CLOSE", "HIGH", "LOW", "OPEN", "VOLUME"))
        ld_high, ld_low, ld = _listing_context(df, lm.get(r.symbol, {}))
        prev = c[i - 1]
        ld_turn = c[0] * v[0] / 1e7
        turn20 = np.mean(c[max(0, i - 20):i] * v[max(0, i - 20):i]) / 1e7
        date = pd.Timestamp(df["DATE"].iat[i])
        f = dict(
            age=r.days_since_listing, ext=r.ext_pct, vol_ratio=r.vol_ratio, rr=r.rr, risk=r.initial_risk_pct,
            ld_range=(ld_high - ld_low) / ld_low * 100 if ld_low else np.nan,
            ld_close_loc=(c[0] - l[0]) / (h[0] - l[0]) if h[0] > l[0] else np.nan,
            gap=(o[i] / prev - 1) * 100,
            ret5=(prev / c[i - 6] - 1) * 100 if i >= 6 else np.nan,
            ret10=(prev / c[i - 11] - 1) * 100 if i >= 11 else np.nan,
            atr10=float(np.mean((h[max(0, i - 10):i] - l[max(0, i - 10):i]) / c[max(0, i - 10):i])) * 100,
            range10=(h[max(0, i - 10):i].max() / l[max(0, i - 10):i].min() - 1) * 100,
            dist_prev=(prev / ld_high - 1) * 100,
            turn_decay=turn20 / ld_turn if ld_turn > 0 else np.nan,
            supply30=int(((ld_dates >= date - pd.Timedelta(days=30)) & (ld_dates < date)).sum()),
        )
        if date >= NIFTY_START:
            k = nf.index.searchsorted(date, side="right") - 1
            f.update({c_: float(nf.iloc[k][c_]) for c_ in ("n_ret63", "n_above200", "n_vol21", "n_dd252")})
        else:
            f.update(n_ret63=np.nan, n_above200=np.nan, n_vol21=np.nan, n_dd252=np.nan)
        rows.append(f)
    return pd.DataFrame(rows, index=d.index)


def account(trades, slots=10):
    pc = PortfolioConfig(capital=100000.0, soft_cap=slots, hard_cap=slots, cap_policy="flat", cost=COSTM, priority="first")
    s = run_portfolio(trades, pc)["summary"]
    calmar = s["cagr_pct"] / abs(s["max_drawdown_pct"]) if s.get("max_drawdown_pct") else float("nan")
    return s["cagr_pct"], s["max_drawdown_pct"], calmar, s["trades_taken"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    p = Params()
    px1 = replace(p, exit_overrides=NO_TIME_EXITS)
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    nifty = pd.read_csv(NIFTY, parse_dates=["Date"]).set_index("Date")["Close"]
    nf = nifty_features(nifty)
    all_ld = pd.Series(sorted(pd.Timestamp(li["listing_date"]).tz_localize(None).normalize() if pd.Timestamp(li["listing_date"]).tzinfo
                              else pd.Timestamp(li["listing_date"]).normalize() for li in list(p_lm.values()) + list(a_lm.values())))
    ld_dates = pd.DatetimeIndex(all_ld.values)
    eras = {"A 2007-09..2022-05": mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START),
            "B 2022-05..2026-11": mh.subset(a_lm, a_cm, lo=mh.ATLAS_START)}
    data = {}
    for era, (lm, cm) in eras.items():
        base, trades = ih.build_base(cm, lm, p)
        x1 = lab.rerun_exits(base, cm, px1)
        F = trade_features(base, cm, lm, nf, ld_dates)
        base = pd.concat([base.drop(columns=[c for c in F.columns if c in base.columns]), F], axis=1)
        base["x1_net"] = x1 - lab.COST
        if era.startswith("A"):
            base = base[pd.to_datetime(base.entry_date) >= NIFTY_START]
        data[era] = (base, lm, cm)
        print(f"{era}: {len(base)} trades; outliers (net >= +25%): live {int((base.net >= 25).sum())} ({100 * (base.net >= 25).mean():.0f}%), "
              f"X1 {int((base.x1_net >= 25).sum())}")
    print("\nRANK AUC for 'outlier' (0.50 = no information); candidate = same side and >= 0.60 / <= 0.40 in BOTH eras")
    print(f"{'feature':<14} {'A live':>7} {'B live':>7} {'A X1':>7} {'B X1':>7}  candidate")
    cands = []
    for ft in FEATS:
        vals = {}
        for era, (b, _, _) in data.items():
            vals[(era, "live")] = auc(b[ft].values, (b.net >= 25).values)
            vals[(era, "x1")] = auc(b[ft].values, (b.x1_net >= 25).values)
        ks = list(data)
        row = [vals[(ks[0], "live")], vals[(ks[1], "live")], vals[(ks[0], "x1")], vals[(ks[1], "x1")]]
        flag = ""
        for lab_, (u, v) in (("live", row[0:2]), ("x1", row[2:4])):
            if not np.isnan(u) and not np.isnan(v) and ((u >= 0.60 and v >= 0.60) or (u <= 0.40 and v <= 0.40)):
                flag += f" {lab_}:{'high' if u > 0.5 else 'low'}"
        if flag:
            cands.append((ft, flag))
        print(f"{ft:<14} {row[0]:7.2f} {row[1]:7.2f} {row[2]:7.2f} {row[3]:7.2f}  {flag.strip()}")
    print(f"\ncandidate markers: {cands if cands else 'NONE'}")

    # account-level test of the candidates (threshold = median of the OTHER era) and of the Nifty 200-day rule
    print("\nACCOUNT-LEVEL FILTER TEST on the X1 book (10 slots; threshold fixed on the other era; reference = unfiltered X1)")
    books = {}
    for era, (b, lm, cm) in data.items():
        tr = lab.baseline_trades(cm, lm, px1, slip=0.3, fill_cap_pct=2.5)
        td = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in tr])
        Ft = trade_features(td, cm, lm, nf, ld_dates)
        books[era] = (tr, pd.concat([td.drop(columns=[c for c in Ft.columns if c in td.columns]), Ft], axis=1))
    tests = [(ft, fl.split(":")[1].split()[0]) for ft, fl in cands]
    tests.append(("n_above200", "high"))        # standard regime rule, not fitted
    seen = set()
    for ft, side in tests:
        if (ft, side) in seen:
            continue
        seen.add((ft, side))
        out = []
        ok = True
        for era, other in (("A 2007-09..2022-05", "B 2022-05..2026-11"), ("B 2022-05..2026-11", "A 2007-09..2022-05")):
            tr, td = books[era]
            thr = 0.5 if ft == "n_above200" else float(data[other][0][ft].median())
            keep = (td[ft] >= thr) if side == "high" else (td[ft] <= thr)
            if ft.startswith("n_"):
                keep = keep | td[ft].isna()          # before the index history starts: no filter
            filt = [t for t, k in zip(tr, keep.values) if k]
            base_m, filt_m = account(tr), account(filt)
            ok &= filt_m[2] > base_m[2] and filt_m[0] > base_m[0]
            out.append(f"{era[:1]}: kept {len(filt)}/{len(tr)} CAGR {base_m[0]:+.1f}->{filt_m[0]:+.1f} DD {base_m[1]:.1f}->{filt_m[1]:.1f} Calmar {base_m[2]:.2f}->{filt_m[2]:.2f}")
        print(f"  {ft:<12} {side:<5} thr from other era | " + " | ".join(out) + f" -> {'CANDIDATE' if ok else 'not adopted'}")


if __name__ == "__main__":
    main()
