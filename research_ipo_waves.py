# -*- coding: utf-8 -*-
"""
research_ipo_waves.py -- are there periods when most IPOs move, are those periods visible in advance, and can P be traded only then?
(added 2026-11-07, owner: "there may be a period when most IPOs move; we capture that time")

WAVE MEASURE (known history only): for every liquid IPO-day (age 5-60 d, listing-day range >= 5%, price >= Rs 25, 20-day turnover >= Rs 0.75 Cr)
the indicator HIT = the stock's high within the NEXT 10 sessions is >= +20% above that day's close (the repo's base-rate definition, LEARNINGS
17.1: 13.8% overall).  Wave strength of a month = share of its IPO-days with HIT (months with >= 20 IPO-days only).

STEP 1  do waves exist?     dispersion of the monthly series and the autocorrelation of month t with month t-1 and with the mean of t-1..t-3.
STEP 2  are they visible?   Spearman of month t against information known at the START of month t: previous 1 / 3 month wave strength (a wave that
        persists can be traded), Nifty 63-day return, broad breadth, IPO supply of the previous 60 days, average listing-day body of the previous
        60 days.  Reported for 2007-2022 and 2022-26 separately; "visible" needs the same sign and |rho| >= 0.30 in both.
STEP 3  can P be traded only in waves?  Pre-registered rule: ON in month t when the mean wave strength of months t-1 and t-2 is >= 15% (round number
        near the 13.8% base rate, not fitted); months with no data count as ON.  Trade level (ON vs OFF mean net, symbol-cluster interval) and account
        level (filtered vs unfiltered Calmar / CAGR / drawdown), both eras, live and X1 books.  Candidate only if ON beats OFF and the filtered account
        has higher Calmar AND CAGR (drawdown no worse than 2pp) in both eras on both books.
Nothing is adopted here.

  .venv\\Scripts\\python.exe research_ipo_waves.py --atlas data_20261107.pkl --panel-dir data_panel_w1200
"""
import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
import research_alpha_lab as lab
import research_max_history as mh
import research_outlier_anatomy as oa
from core.execution_model import CostModel
from core.honest_backtest import LIVE_FAITHFUL, Params, _listing_context
from core.portfolio_sim import PortfolioConfig, run_portfolio
from core.setup3 import NO_TIME_EXITS
from research_setup2 import diff_cluster_boot

THRESH = 15.0
MIN_DAYS = 20


def ipo_days(cm, lm, lo, hi):
    rows = []
    for s, df in cm.items():
        li = lm.get(s)
        if li is None or len(df) < 25:
            continue
        ld = pd.Timestamp(li["listing_date"])
        ld = ld.tz_localize(None).normalize() if ld.tzinfo else ld.normalize()
        if not (lo <= ld < hi):
            continue
        ld_high, ld_low, _ = _listing_context(df, li)
        if not ld_low or ld_low <= 0 or (ld_high - ld_low) / ld_low * 100 < 5.0:
            continue
        d = pd.to_datetime(df["DATE"])
        c, h, v = df["CLOSE"].values.astype(float), df["HIGH"].values.astype(float), df["VOLUME"].values.astype(float)
        age = (d - ld).dt.days.values
        turn = pd.Series(c * v / 1e7).rolling(20, min_periods=5).mean().values
        fwd = pd.Series(h)[::-1].rolling(10).max()[::-1].shift(-1).values
        ok = (age >= 5) & (age <= 60) & (c >= 25) & (turn >= 0.75) & ~np.isnan(fwd)
        for i in np.where(ok)[0]:
            rows.append((d.iat[i], float(fwd[i] / c[i] - 1 >= 0.20), (c[0] / df["OPEN"].iat[0] - 1) * 100, ld, s))
    return pd.DataFrame(rows, columns=["date", "hit", "ld_body", "ld", "sym"])


def cohort_effect_test(ev, n_perm=5000, seed=0):
    """Is the listing month (cohort) informative about an IPO's own hit share beyond chance?  Shuffle IPOs across listing months."""
    g = ev.groupby("sym").agg(h=("hit", "mean"), ld=("ld", "first"), n=("hit", "size"))
    g["lm"] = g.ld.dt.to_period("M")
    keep = g.groupby("lm").h.transform("size") >= 3
    g = g[keep]
    overall = np.average(g.h, weights=g.n)

    def stat(lm):
        s = pd.DataFrame({"h": g.h.values, "n": g.n.values, "lm": lm})
        m = s.groupby("lm").apply(lambda x: pd.Series(dict(w=x.n.sum(), mean=np.average(x.h, weights=x.n))))
        return float((m.w * (m["mean"] - overall) ** 2).sum() / m.w.sum())

    obs = stat(g.lm.values)
    rng = np.random.default_rng(seed)
    null = np.array([stat(rng.permutation(g.lm.values)) for _ in range(n_perm)])
    return obs, float(np.mean(null)), float((null >= obs).mean()), len(g), g.lm.nunique()


def spearman(x, y):
    x, y = pd.Series(x, dtype=float), pd.Series(y, dtype=float)
    ok = x.notna() & y.notna()
    return (float(x[ok].rank().corr(y[ok].rank())), int(ok.sum())) if ok.sum() >= 12 else (float("nan"), int(ok.sum()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--panel-dir", default="data_panel_w1200")
    a = ap.parse_args()
    lab.POP.update(causal=True, vol_gate=False, slip=0.3, cap=2.5)
    lab.CFG = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")
    a_lm, a_cm = pickle.load(open(a.atlas, "rb"))
    p_lm, p_cm = mh.load_panel(a.panel_dir)
    ev = pd.concat([ipo_days(p_cm, p_lm, pd.Timestamp("2006-07-01"), mh.ATLAS_START), ipo_days(a_cm, a_lm, mh.ATLAS_START, pd.Timestamp("2100-01-01"))])
    ev["m"] = ev.date.dt.to_period("M")
    mon = ev.groupby("m").agg(days=("hit", "size"), wave=("hit", lambda x: 100 * x.mean()))
    mon = mon[mon.days >= MIN_DAYS]
    mon.index = mon.index.to_timestamp()
    full = pd.DataFrame(index=pd.date_range("2007-01-01", "2026-09-01", freq="MS"))
    full = full.join(mon)
    print(f"IPO-days {len(ev)}; months with >= {MIN_DAYS} IPO-days: {mon.wave.notna().sum()} of {len(full)}; overall hit rate {100 * ev.hit.mean():.1f}%")
    q = mon.wave.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).round(1).to_dict()
    print(f"wave strength by month (%): quantiles {q}; max {mon.wave.max():.0f}% ({mon.wave.idxmax().strftime('%Y-%m')}), months above 25%: {int((mon.wave > 25).sum())}, below 5%: {int((mon.wave < 5).sum())}")
    print("by year (mean monthly wave %): " + " ".join(f"{int(y)}:{g.wave.mean():.0f}" for y, g in mon.groupby(mon.index.year)))

    obs, nullm, pval, n_ipo, n_m = cohort_effect_test(ev, n_perm=1500)
    print(f"\nSTEP 0: are 'waves' more than chance?  Between-month variance of IPO hit shares ({n_ipo} IPOs in {n_m} listing months with >= 3 IPOs): "
          f"observed {obs:.4f} vs {nullm:.4f} when IPOs are shuffled across months (permutation p = {pval:.3f})")

    # ---- STEP 1 persistence
    w = full.wave
    lag1 = w.shift(1)
    lag3 = pd.concat([w.shift(1), w.shift(2), w.shift(3)], axis=1).mean(axis=1, skipna=True).where(pd.concat([w.shift(1), w.shift(2), w.shift(3)], axis=1).notna().sum(axis=1) >= 2)
    split = pd.Timestamp("2022-05-27")
    print("\nSTEP 1: persistence (Spearman rho of month t with earlier months)")
    for nm, sel in (("2007-2022", full.index < split), ("2022-26", full.index >= split)):
        r1, n1 = spearman(w[sel], lag1[sel])
        r3, n3 = spearman(w[sel], lag3[sel])
        print(f"  {nm:<10} month t vs t-1: rho {r1:+.2f} (n={n1}) | vs mean(t-1..t-3): rho {r3:+.2f} (n={n3})")

    # ---- STEP 2 visibility
    br = pd.read_csv(a.panel_dir + "/breadth.csv", index_col=0, parse_dates=True)
    nifty = pd.read_csv(oa.NIFTY, parse_dates=["Date"]).set_index("Date")["Close"]
    n63 = (nifty / nifty.shift(63) - 1) * 100
    lds = pd.Series(sorted(pd.Timestamp(li["listing_date"]).tz_localize(None).normalize() if pd.Timestamp(li["listing_date"]).tzinfo
                           else pd.Timestamp(li["listing_date"]).normalize() for li in list(p_lm.values()) + list(a_lm.values())))
    ev_first = ev.drop_duplicates("ld").copy()
    ev_first["lm"] = ev_first.ld.dt.to_period("M").dt.to_timestamp()
    body_by_month = ev_first.groupby("lm").ld_body.mean()
    pred = pd.DataFrame(index=full.index)
    pred["wave_lag1"], pred["wave_lag3"] = lag1, lag3
    pred["nifty_ret63"] = [n63.loc[:d - pd.Timedelta(days=1)].dropna().iloc[-1] if len(n63.loc[:d - pd.Timedelta(days=1)].dropna()) else np.nan for d in full.index]
    pred["breadth200"] = [br.pct_above200.loc[:d - pd.Timedelta(days=1)].iloc[-1] if len(br.loc[:d - pd.Timedelta(days=1)]) else np.nan for d in full.index]
    pred["supply60"] = [int(((lds >= d - pd.Timedelta(days=60)) & (lds < d)).sum()) for d in full.index]
    pred["ld_body60"] = [body_by_month.loc[d - pd.DateOffset(months=2):d - pd.DateOffset(months=1)].mean() for d in full.index]
    print("\nSTEP 2: is the wave visible at the start of the month?  Spearman rho with this month's wave strength")
    print(f"  {'predictor':<12} {'2007-22':>9} {'2022-26':>9}  visible (same sign, |rho| >= 0.30 in both)")
    for c in pred.columns:
        r1, _ = spearman(pred[c][full.index < split], w[full.index < split])
        r2, _ = spearman(pred[c][full.index >= split], w[full.index >= split])
        vis = (not np.isnan(r1) and not np.isnan(r2)) and np.sign(r1) == np.sign(r2) and abs(r1) >= 0.30 and abs(r2) >= 0.30
        print(f"  {c:<12} {r1:+9.2f} {r2:+9.2f}  {'VISIBLE' if vis else ''}")

    # ---- STEP 3 rule
    prior2 = pd.concat([w.shift(1), w.shift(2)], axis=1).mean(axis=1, skipna=True)
    on_month = (prior2 >= THRESH) | prior2.isna()
    print(f"\nSTEP 3: rule 'ON when mean wave of the previous two months >= {THRESH:.0f}%' (no data = ON); share of months ON: "
          f"{100 * on_month.mean():.0f}% (2007-22 {100 * on_month[full.index < split].mean():.0f}%, 2022-26 {100 * on_month[full.index >= split].mean():.0f}%)")
    p = Params()
    books = {"live": p, "X1": replace(p, exit_overrides=NO_TIME_EXITS)}
    eras = {"A 2007-09..2022-05": mh.subset(p_lm, p_cm, lo=pd.Timestamp("2006-07-01"), hi=mh.ATLAS_START),
            "B 2022-05..2026-11": mh.subset(a_lm, a_cm, lo=mh.ATLAS_START)}
    cost = CostModel(entry_slippage_pct=0.0, exit_slippage_pct=0.3)

    def account(tr):
        s = run_portfolio(tr, PortfolioConfig(capital=100000.0, soft_cap=10, hard_cap=10, cap_policy="flat", cost=cost, priority="first"))["summary"]
        return s["cagr_pct"], s["max_drawdown_pct"], s["cagr_pct"] / abs(s["max_drawdown_pct"]), s["trades_taken"]

    ok_all = True
    for bn, pp in books.items():
        ok_b = True
        for era, (lm, cm) in eras.items():
            tr = lab.baseline_trades(cm, lm, pp, slip=0.3, fill_cap_pct=2.5)
            if era.startswith("A"):
                tr = [t for t in tr if pd.Timestamp(t["entry_date"]) >= oa.NIFTY_START]
            d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in tr])
            d["net"] = d.pnl_pct - lab.COST
            mkeys = pd.to_datetime(d.entry_date).dt.to_period("M").dt.to_timestamp()
            on = np.array([bool(on_month.get(m, True)) for m in mkeys])
            ton, toff = d[on], d[~on]
            dm, dlo, dhi = diff_cluster_boot(ton.net.values, ton.symbol.values, toff.net.values, toff.symbol.values) if len(toff) >= 5 else (np.nan, np.nan, np.nan)
            base, filt = account(tr), account([t for t, k in zip(tr, on) if k])
            good = (not np.isnan(dm)) and dm > 0 and filt[2] > base[2] and filt[0] > base[0] and filt[1] >= base[1] - 2.0
            ok_b &= good
            print(f"  {bn:<5} {era[0]}: ON {len(ton)}/{len(d)} net {ton.net.mean():+.1f} vs OFF {toff.net.mean() if len(toff) else float('nan'):+.1f} diff {dm:+.1f}[{dlo:+.1f},{dhi:+.1f}] | "
                  f"CAGR {base[0]:+.1f}->{filt[0]:+.1f} DD {base[1]:.1f}->{filt[1]:.1f} Calmar {base[2]:.2f}->{filt[2]:.2f}")
        ok_all &= ok_b
    print(f"  -> {'CANDIDATE' if ok_all else 'not adopted'}")


if __name__ == "__main__":
    main()
