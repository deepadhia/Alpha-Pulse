# -*- coding: utf-8 -*-
"""
research_tail_capture.py -- where the profit comes from (the right tail) and which levers can capture more of it.

PRE-REGISTRATION (written 2026-10-14 before the variants were run).  Population: policy P (resting entry, causal gates, live
close-based stops, 12% cap; see research_risk_profile.py).  Goal stated by the owner: win rate is secondary, outliers must
compensate for the losers.
  A  Tail anatomy: concentration of profit, holding time of outliers, share captured of the peak, and whether any of the 18
     entry features separates outliers (peak run-up >= 30%) from the rest (rank AUC, discovery 2022-24 vs holdout 2025-26).
  Q1 Re-entry after a shake-out: per-symbol cooldown 30 bars -> 20 / 10 / 5.
  Q3 Pyramid: add 50% of base size at the open after the first close >= +10% over entry; judged per capital-day.
  Q4 Conviction sizing: x1.5 / x1.0 / x0.5 by terciles of the 10-day range (terciles from earlier years only).
  E11 Let positive sleepers live: drop the "peak run-up < 4% at day 14" dead-money clause (env DEAD_MONEY_RUNUP_IPO=0); the
      velocity gate (day 14 and PnL <= 0), volume exhaustion and the 40-day stagnant guard stay.
Adoption bar: the 90% symbol-cluster CI of the paired gain excludes 0, the gain stays positive after dropping the single
largest contributor, at most one worse year, trade count not reduced.  Tail levers are judged on the same bar on purpose:
a change that only wins because of one or two runners is a lottery ticket, not evidence.

  python research_tail_capture.py --cache data.pkl --trades reports/2026-10-14/data_base_trades_causal_resting_eod.csv
"""

import argparse
import logging
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)

import research_alpha_lab as lab
from core.honest_backtest import LIVE_FAITHFUL, Params, simulate_symbol_cross
from research_alpha_lab import COST, cluster_boot, rerun_exits

P0 = Params()
EOD = replace(LIVE_FAITHFUL, exit_fill="eod_next_open")


def pfv(x):
    x = np.asarray(x, float)
    w, l = x[x > 0].sum(), -x[x < 0].sum()
    return float(w / l) if l > 0 else float("inf")


def auc(x, y):
    r = pd.Series(x).rank().values
    n1 = int(y.sum())
    n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def gen(cm, lm, p=P0, **k):
    kw = dict(min_age=5, causal=True, vol_gate=False, slip_pct=0.3, fill_cap_pct=2.5)
    kw.update(k)
    tr = []
    for s, df in cm.items():
        tr += simulate_symbol_cross(df, s, lm.get(s, {}), p, cfg=EOD, **kw)
    return tr


def frame(tr):
    d = pd.DataFrame([{k: v for k, v in t.items() if k != "path"} for t in tr])
    d["net"] = d.pnl_pct - COST
    d["entry_ts"] = pd.to_datetime(d.entry_date)
    d["year"] = d.entry_ts.dt.year
    return d


def summary(lbl, d):
    x = d.net.values
    m, lo, hi = cluster_boot(x, d.symbol.values)
    srt = np.sort(x)[::-1]
    gp = x[x > 0].sum()
    top5 = srt[:5][srt[:5] > 0].sum() / gp if gp > 0 else float("nan")
    print(f"{lbl:<46} N={len(x):3d} mean {m:+5.2f}% CI90[{lo:+5.1f},{hi:+5.1f}] total {x.sum():+7.1f}pp med {np.median(x):+5.2f} "
          f"PF {pfv(x):4.2f} top-decile mean {srt[:max(1, len(x) // 10)].mean():+6.1f} top-5 share of gross profit {top5:4.0%}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trades", required=True, help="data_base_trades_causal_resting_eod.csv from research_alpha_lab.py")
    args = ap.parse_args(argv)
    with open(args.cache, "rb") as fh:
        lm, cm = pickle.load(fh)
    csv = pd.read_csv(args.trades)
    csv["entry_ts"] = pd.to_datetime(csv.entry_date)
    csv["year"] = csv.entry_ts.dt.year
    csv["net"] = csv.pnl_pct - COST

    # ---------------------------------------------------------------- A. anatomy
    print("=" * 112)
    print("A. TAIL ANATOMY (policy P)")
    print("=" * 112)
    d = csv
    print(f"N {len(d)}, still open at the end of the data {int(d.open.sum())}")
    print("gross pnl percentiles 5/25/50/75/90/95/99:", np.percentile(d.pnl_pct, [5, 25, 50, 75, 90, 95, 99]).round(1))
    gp = d.pnl_pct[d.pnl_pct > 0].sum()
    for thr in (15, 25, 40, 75):
        s = d[d.pnl_pct >= thr]
        print(f"pnl >= {thr:>2}%: {len(s):3d} trades ({len(s) / len(d):5.1%}) = {s.pnl_pct.sum() / gp:4.0%} of gross profit; still open {int(s.open.sum())}")
    big = d[d.max_runup_pct >= 30]
    print(f"trades whose peak run-up >= 30%: {len(big)}; realised mean {big.pnl_pct.mean():.1f}% vs peak mean {big.max_runup_pct.mean():.1f}% "
          f"(capture {big.pnl_pct.sum() / big.max_runup_pct.sum():.2f}); median days held {big.days_held.median():.0f}")
    print("top 10 by gross pnl:")
    print(d.sort_values("pnl_pct", ascending=False)[["symbol", "entry_date", "pnl_pct", "max_runup_pct", "days_held", "exit_reason", "open"]]
          .head(10).round(1).to_string(index=False))
    d = d.assign(out=(d.max_runup_pct >= 30).astype(int))
    rows = []
    for c in ["age", "vr", "ext", "risk_pct", "rr", "turn20", "ld_range", "gap", "ret5", "ret10", "atr10", "range10", "dist_prev",
              "supply30", "n_active", "frac_above", "med_ret20", "ld_sent60"]:
        s = d[[c, "out"]].dropna()
        e = d[d.year <= 2024][[c, "out"]].dropna()
        l = d[d.year >= 2025][[c, "out"]].dropna()
        rows.append((c, round(auc(s[c].values, s.out.values), 2), round(auc(e[c].values, e.out.values), 2),
                     round(auc(l[c].values, l.out.values), 2)))
    print(f"\noutlier (peak run-up >= 30%, n={int(d.out.sum())}) separation by entry feature; 0.50 = no information:")
    print(pd.DataFrame(rows, columns=["feature", "AUC all", "AUC 2022-24", "AUC 2025-26"]).to_string(index=False))

    # ---------------------------------------------------------------- Q1
    base_tr = gen(cm, lm)
    base = frame(base_tr)
    print("\n" + "=" * 112)
    print("Q1. RE-ENTRY COOLDOWN (bars) - more shots at the leaders that shake out first")
    print("=" * 112)
    summary("policy P (cooldown 30)", base)
    key = set(zip(base.symbol, base.entry_idx))
    for k in (20, 10, 5):
        t = frame(gen(cm, lm, replace(P0, cooldown_bars=k)))
        summary(f"cooldown {k}", t)
        extra = t[[(s, i) not in key for s, i in zip(t.symbol, t.entry_idx)]]
        summary(f"   extra trades (cooldown {k})", extra)
        print("      extra trades by year (n, total net pp):", {int(y): (int(len(g)), round(float(g.net.sum()), 1)) for y, g in extra.groupby("year")})
    print("   Reading: the gain is not monotone in the cooldown (20 loses, 10 wins, 5 is mixed) and the 10-bar extras' CI includes 0.")

    # ---------------------------------------------------------------- Q3
    print("\n" + "=" * 112)
    print("Q3. PYRAMID: add 50% at the next open after the first close >= +10% over entry")
    print("=" * 112)
    rows = []
    for t in base_tr:
        df = cm[t["symbol"]]
        dates = pd.to_datetime(df.DATE).values
        fill = float(t["entry_price"])
        ex_date, ent = pd.Timestamp(t["exit_date"]), pd.Timestamp(t["entry_date"])
        add_px = add_date = None
        for (dt, close, _trail) in t["path"]:
            if pd.Timestamp(dt) <= ent:
                continue
            if close >= fill * 1.10:
                j = int(np.searchsorted(dates, np.datetime64(pd.Timestamp(dt)))) + 1
                if j < len(df) and pd.Timestamp(dates[j]) <= ex_date:
                    add_px, add_date = float(df.OPEN.iat[j]) * 1.003, pd.Timestamp(dates[j])
                break
        days = max(1, (ex_date - ent).days)
        rec = dict(symbol=t["symbol"], year=ent.year, base_net=t["pnl_pct"] - COST, days=days, added=add_px is not None)
        if add_px is not None:
            r2 = (float(t["exit_price"]) / add_px - 1) * 100
            rec.update(add_net=0.5 * (r2 - COST), cap_days=(add_date - ent).days + (ex_date - add_date).days * 1.5, add_ret_gross=r2)
        else:
            rec.update(add_net=0.0, cap_days=float(days))
        rows.append(rec)
    q = pd.DataFrame(rows)
    q["pyr_net"] = q.base_net + q.add_net
    print(f"add triggered on {int(q.added.sum())} of {len(q)} trades; mean add-tranche gross return {q[q.added].add_ret_gross.mean():+.1f}%")
    print(f"total net: policy P {q.base_net.sum():+.1f}pp, with pyramid {q.pyr_net.sum():+.1f}pp; capital-days +{q.cap_days.sum() / q.days.sum() - 1:.0%}; "
          f"return per capital-day with pyramid = {(q.pyr_net.sum() / q.cap_days.sum()) / (q.base_net.sum() / q.days.sum()):.2f}x policy P")
    m, lo, hi = cluster_boot(q.add_net.values, q.symbol.values)
    o = np.argsort(-q.add_net.values)
    m2, lo2, hi2 = cluster_boot(np.delete(q.add_net.values, o[:1]), np.delete(q.symbol.values, o[:1]))
    print(f"add-on contribution per trade {m:+.2f}pp CI90[{lo:+.2f},{hi:+.2f}]; without the largest ({q.symbol.values[o[0]]}) {m2:+.2f}[{lo2:+.2f},{hi2:+.2f}]")

    # ---------------------------------------------------------------- Q4
    print("\n" + "=" * 112)
    print("Q4. CONVICTION SIZING by tight base (terciles of the 10-day range from earlier years; test 2024-26)")
    print("=" * 112)
    out = []
    for Y in (2024, 2025, 2026):
        tr_ = csv[csv.year < Y].dropna(subset=["range10"])
        te = csv[csv.year == Y].dropna(subset=["range10"]).copy()
        lo_t, hi_t = np.percentile(tr_.range10, [33.3, 66.7])
        te["w"] = np.where(te.range10 <= lo_t, 1.5, np.where(te.range10 >= hi_t, 0.5, 1.0))
        out.append(te)
    te = pd.concat(out)
    dd = (te.w.values / te.w.mean() - 1) * te.net.values
    m, lo, hi = cluster_boot(dd, te.symbol.values)
    o = np.argsort(-np.abs(dd))
    m2, lo2, hi2 = cluster_boot(np.delete(dd, o[:1]), np.delete(te.symbol.values, o[:1]))
    print(f"N={len(te)}: equal-weight mean {te.net.mean():+.2f}%; extra pp per trade at equal average exposure {m:+.2f} CI90[{lo:+.2f},{hi:+.2f}]; "
          f"without the largest ({te.symbol.values[o[0]]}) {m2:+.2f}[{lo2:+.2f},{hi2:+.2f}]")
    for w in (1.5, 1.0, 0.5):
        s = te[te.w == w]
        print(f"   weight {w}: N={len(s)} mean net {s.net.mean():+.2f}% share of >=25% outliers {(s.pnl_pct >= 25).mean():.1%}")

    # ---------------------------------------------------------------- E11
    print("\n" + "=" * 112)
    print("E11. LET POSITIVE SLEEPERS LIVE (drop the peak < 4% dead-money clause; env DEAD_MONEY_RUNUP_IPO=0)")
    print("=" * 112)
    lab.CFG = EOD
    b0 = rerun_exits(csv, cm, P0)
    print(f"control: re-run reproduces the baseline, max abs diff {float(np.nanmax(np.abs(b0 - csv.pnl_pct.values))):.1e}pp")
    for lbl, p in (("E11 drop the clause", replace(P0, exit_overrides=(("dead_money_runup_ipo", 0.0),))),
                   ("E11b clause at peak < 2% instead of < 4%", replace(P0, exit_overrides=(("dead_money_runup_ipo", 2.0),)))):
        v = rerun_exits(csv, cm, p)
        diff = v - b0
        ok = ~np.isnan(diff)
        m, lo, hi = cluster_boot(diff[ok], csv.symbol.values[ok])
        o = np.argsort(-np.abs(diff[ok]))
        m2, lo2, hi2 = cluster_boot(np.delete(diff[ok], o[:1]), np.delete(csv.symbol.values[ok], o[:1]))
        yy = {int(y): round(float(diff[ok][csv.year.values[ok] == y].mean()), 2) for y in sorted(csv.year.unique())}
        net = v[ok] - COST
        print(f"{lbl:<42} diff {m:+5.2f}pp CI90[{lo:+5.2f},{hi:+5.2f}] | without the largest mover {m2:+5.2f}[{lo2:+5.2f},{hi2:+5.2f}] | "
              f"net {net.mean():+5.2f}% PF {pfv(net):.2f} | years {yy}")
        changed = np.abs(diff[ok]) > 0.01
        print(f"      trades changed: {int(changed.sum())} (improved {int((diff[ok] > 0.01).sum())}, worsened {int((diff[ok] < -0.01).sum())}); "
              f"biggest movers {[(csv.symbol.values[ok][i], round(float(diff[ok][i]), 1)) for i in o[:4]]}")
        gains, losses = diff[ok][diff[ok] > 0.01], diff[ok][diff[ok] < -0.01]
        if len(gains) and len(losses):
            be = -losses.mean() / (gains.mean() - losses.mean())
            print(f"      average gain on improved {gains.mean():+.1f}pp, average loss on worsened {losses.mean():+.1f}pp: the change breaks even if "
                  f"{be:.1%} of affected trades turn into runners (observed {len(gains) / changed.sum():.0%}, n={int(changed.sum())})")


if __name__ == "__main__":
    main()
