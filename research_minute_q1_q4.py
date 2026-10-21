# -*- coding: utf-8 -*-
"""
research_minute_q1_q4.py -- Q1 to Q4 of MINUTE_BAR_TEST_PLAN.md on policy-P trades, using data_minute/ (1-minute candles).

PRE-REGISTRATION: hypotheses, labels, features and bars are those written in MINUTE_BAR_TEST_PLAN.md section 3 (written 2026-10-17 before this
script was run).  Implementation choices fixed here before running:
  trigger = listing high x 1.015, limit = listing high x 1.025 (policy P).  The trigger minute jc is the first minute of the entry day whose high >= trigger.
  Q1 fill: price p = max(trigger, open of minute jc).  If p <= limit the order fills at p; if p > limit (jump through) it fills at the limit only if a later
     minute within 60 minutes trades down to the limit, else it is unfilled.  Measured fill is compared with the replay fill (entry_price, trigger + 0.3% slip);
     net is re-expressed for the measured fill as (1 + gross) x model_fill / measured_fill (same exit; an approximation).
  Q2 gates are read at the cross minute (best case: scan with no latency) and at cross + 60 minutes (the PENDING confirmation):
     volume floor = cumulative day volume >= 150,000; close location = (price - day low so far) / (day high so far - day low so far) >= 0.5;
     hold = close at cross+60 >= trigger AND no minute low in those 60 minutes below 97.5% of the cross price.
  Q3: six features measured over the 60 minutes after the cross (listed in the plan); thresholds chosen on earlier years to keep >= 80% of GOOD; test 2024-26.
  Q4: confirm-then-buy: enter at the close of minute jc+60 plus 0.3% slip (a) always, (b) only if the hold test passes, (c) hold test AND price <= listing high x 1.035
     (the live chase cap).  Compared per signal with the resting entry (a skipped signal contributes 0).
GOOD = net >= 20% or peak run-up >= 25%; BAD = closed loss with peak < 5%.  Costs 1.0% round trip.  90% symbol-cluster bootstrap.

  .venv\\Scripts\\python.exe research_minute_q1_q4.py --cache data.pkl --trades reports/2026-10-17/gate_replay_V0.csv
"""
import argparse
import os
import pickle

import numpy as np
import pandas as pd

from core.honest_backtest import _listing_context
from research_alpha_lab import cluster_boot, pf

COST = 1.0


def load_minutes(d, sym):
    p = os.path.join(d, f"{sym}_1m.pkl")
    if not os.path.exists(p):
        return None
    m = pd.read_pickle(p)
    if not len(m):
        return None
    m["ts"] = pd.to_datetime(m.ts)
    m["day"] = m.ts.dt.strftime("%Y-%m-%d")
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--dir", default="data_minute")
    ap.add_argument("--out", default="reports/2026-10-17/minute_q1_q4_trades.csv")
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    t = pd.read_csv(a.trades)
    t["open"] = t.open.astype(bool)
    t["entry_date"] = pd.to_datetime(t.entry_date)
    t["year"] = t.entry_date.dt.year
    t["good"] = (t.net >= 20) | (t.max_runup_pct >= 25)
    t["bad"] = (~t.open) & (t.net < 0) & (t.max_runup_pct < 5)
    rows = []
    cache = {}
    for r in t.itertuples():
        rec = dict(idx=r.Index)
        if r.symbol not in cache:
            cache[r.symbol] = load_minutes(a.dir, r.symbol)
        m = cache[r.symbol]
        df = candle_map[r.symbol].reset_index(drop=True)
        ld_high, _, _ = _listing_context(df, listing_map.get(r.symbol, {}))
        trig, limit = ld_high * 1.015, ld_high * 1.025
        ed = r.entry_date.strftime("%Y-%m-%d")
        if m is None:
            rows.append(dict(rec, status="no_minutes"))
            continue
        d = m[m.day == ed].reset_index(drop=True)
        if len(d) < 30:
            rows.append(dict(rec, status="no_day"))
            continue
        o, h, l, c, v = (d[k].values.astype(float) for k in ("open", "high", "low", "close", "volume"))
        hit = np.where(h >= trig)[0]
        if not len(hit):
            rows.append(dict(rec, status="no_cross"))
            continue
        jc = int(hit[0])
        p_cross = max(trig, o[jc])
        rec.update(jc=jc, ld_high=ld_high, trig=trig, cross_time=str(d.ts.iloc[jc].time())[:5], mins_from_open=jc)
        if p_cross <= limit:
            fill, st = p_cross, "filled"
        else:
            later = np.where(l[jc + 1:jc + 61] <= limit)[0]
            fill, st = (limit, "filled_after_jump") if len(later) else (np.nan, "unfilled_jump")
        rec.update(status=st, fill=fill, jump=p_cross > limit, slip_vs_trig=(fill / trig - 1) * 100 if fill == fill else np.nan,
                   model_fill=r.entry_price)
        gross = (1 + r.pnl_pct / 100.0)
        rec["net_measured_fill"] = (gross * r.entry_price / fill - 1) * 100 - COST if fill == fill else np.nan
        # Q2 gates
        def at(off):
            j = jc + off
            if j >= len(d):
                return None
            hi, lo, pr = h[:j + 1].max(), l[:j + 1].min(), c[j]
            return dict(vol=v[:j + 1].sum(), loc=(pr - lo) / (hi - lo) if hi > lo else 1.0, price=pr, j=j)
        a0, a60 = at(0), at(60)
        rec.update(vol0=a0["vol"], loc0=a0["loc"])
        if a60:
            hold = bool(c[a60["j"]] >= trig and l[jc:a60["j"] + 1].min() >= p_cross * 0.975)
            rec.update(vol60=a60["vol"], loc60=a60["loc"], hold60=hold, price60=a60["price"])
            # Q3 features over the 60 minutes after the cross
            j = a60["j"]
            tp = (h[:j + 1] + l[:j + 1] + c[:j + 1]) / 3.0
            vwap = (tp * v[:j + 1]).sum() / max(v[:j + 1].sum(), 1.0)
            dprev = df[pd.to_datetime(df.DATE).dt.tz_localize(None) < r.entry_date]
            avg20 = float(dprev.VOLUME.tail(20).mean()) if len(dprev) else np.nan
            rec.update(f_mins_open=jc, f_vol_pace=a60["vol"] / avg20 if avg20 else np.nan, f_above_vwap=(c[j] / vwap - 1) * 100,
                       f_pullback=max(0.0, (trig - l[jc:j + 1].min()) / trig * 100), f_hold_share=float((c[jc:j + 1] >= trig).mean()),
                       f_gap=(o[0] / float(dprev.CLOSE.iloc[-1]) - 1) * 100 if len(dprev) else np.nan)
            # Q4 confirm-then-buy entries
            ent = c[j] * 1.003
            capok = c[j] <= ld_high * 1.035
            for tag, ok in (("a", True), ("b", hold), ("c", hold and capok)):
                rec[f"q4{tag}_net"] = ((gross * r.entry_price / ent - 1) * 100 - COST) if ok else 0.0
                rec[f"q4{tag}_taken"] = bool(ok)
            rec["q4_price60_ext"] = (c[j] / ld_high - 1) * 100
        rows.append(rec)
    X = pd.DataFrame(rows).set_index("idx")
    T = t.join(X)
    T.to_csv(a.out, index=False)
    print("status counts:", T.status.value_counts(dropna=False).to_dict())
    U = T[T.status.isin(["filled", "filled_after_jump", "unfilled_jump"])].copy()
    print(f"trades with minute bars on the entry day and a trigger cross: {len(U)} of {len(T)}; GOOD {int(U.good.sum())} BAD {int(U.bad.sum())}\n")

    # ---------------------------------------------------------------- Q1
    print("=" * 110 + "\nQ1 EXECUTION (resting stop-limit at listing high +1.5%, limit +2.5%)\n" + "=" * 110)
    F = U[U.fill.notna()]
    print(f"cross time: median {int(U.mins_from_open.median())} minutes after the open; crossed within first 15 min: {100 * (U.mins_from_open <= 15).mean():.1f}%, "
          f"after 12:00 IST: {100 * (U.mins_from_open >= 165).mean():.1f}%")
    print(f"jumped through the limit at the cross minute: {100 * U.jump.mean():.1f}% ({int(U.jump.sum())});  unfilled after the jump: {int((U.status == 'unfilled_jump').sum())}")
    print(f"measured fill vs trigger (pct): mean {F.slip_vs_trig.mean():+.2f}  median {F.slip_vs_trig.median():+.2f}  p90 {F.slip_vs_trig.quantile(.9):+.2f}   (replay assumed +0.30 on top of the trigger)")
    d_fill = (F.fill / F.model_fill - 1) * 100
    print(f"measured fill vs replay fill (pct): mean {d_fill.mean():+.2f}  median {d_fill.median():+.2f}  p90 {d_fill.quantile(.9):+.2f}")
    m0, lo0, hi0 = cluster_boot(F.net.values, F.symbol.values)
    m1, lo1, hi1 = cluster_boot(F.net_measured_fill.values, F.symbol.values)
    print(f"mean net per filled trade: replay {m0:+.2f}% (CI {lo0:+.1f}..{hi0:+.1f})   with measured fills {m1:+.2f}% (CI {lo1:+.1f}..{hi1:+.1f})   PF {pf(F.net.values):.2f} -> {pf(F.net_measured_fill.values):.2f}")
    uf = U[U.status == "unfilled_jump"]
    if len(uf):
        print(f"unfilled (jump) trades: mean replay net {uf.net.mean():+.2f}%; biggest: {[(s, round(n, 0)) for s, n in zip(uf.symbol, uf.net)][:6]}")

    # ---------------------------------------------------------------- Q2
    print("\n" + "=" * 110 + "\nQ2 LIVE MID-SESSION GATES REPLAYED FROM MINUTE BARS (bar: keep a gate only if it rejects >= 35% of BAD while keeping >= 80% of GOOD and rejected mean net < 0)\n" + "=" * 110)
    def gate_row(label, keep):
        k = keep.reindex(U.index).fillna(True).astype(bool)
        rej = U[~k]
        rg = (~k[U.good]).mean() * 100 if U.good.any() else np.nan
        rb = (~k[U.bad]).mean() * 100 if U.bad.any() else np.nan
        print(f"{label:<46} rejects {len(rej):3d} ({100 * (~k).mean():4.1f}%)  GOOD rejected {rg:5.1f}%  BAD rejected {rb:5.1f}%  "
              f"rejected mean net {rej.net.mean() if len(rej) else float('nan'):+6.2f}%  kept mean net {U.net[k].mean():+5.2f}%  outliers>=25% rejected {int((rej.net >= 25).sum())}")
    gate_row("volume floor 150k at cross minute", U.vol0 >= 150000)
    gate_row("volume floor 150k at cross+60", U.vol60 >= 150000)
    gate_row("close location >= 50% at cross minute", U.loc0 >= 0.5)
    gate_row("close location >= 50% at cross+60", U.loc60 >= 0.5)
    gate_row("hold test at cross+60 (confirmation)", U.hold60)
    gate_row("all of: vol, loc, hold at cross+60", (U.vol60 >= 150000) & (U.loc60 >= 0.5) & U.hold60)

    # ---------------------------------------------------------------- Q3
    print("\n" + "=" * 110 + "\nQ3 FIRST-60-MINUTE FEATURES, walk-forward 2024-26 (bar: recall GOOD >= 75%, BAD rejected >= 35%, lift >= 10pp, rejected mean net < 0)\n" + "=" * 110)
    V = U.dropna(subset=["f_vol_pace", "f_above_vwap", "f_pullback", "f_hold_share", "f_gap"]).copy()
    print(f"trades with all features: {len(V)}; GOOD {int(V.good.sum())}  BAD {int(V.bad.sum())}")
    out = []
    for f in ("f_mins_open", "f_vol_pace", "f_above_vwap", "f_pullback", "f_hold_share", "f_gap"):
        for direction in ("ge", "le"):
            keeps = []
            for ty in (2024, 2025, 2026):
                tr, te = V[V.year < ty], V[V.year == ty]
                if len(tr) < 25 or len(te) == 0:
                    continue
                best = None
                for th in np.unique(tr[f].values):
                    k = (tr[f] >= th) if direction == "ge" else (tr[f] <= th)
                    if k[tr.good].mean() < 0.80:
                        continue
                    rb = (~k[tr.bad]).mean() if tr.bad.any() else 0
                    if best is None or rb > best[0]:
                        best = (rb, th)
                keeps.append(pd.Series(True, index=te.index) if best is None else ((te[f] >= best[1]) if direction == "ge" else (te[f] <= best[1])))
            te_all = V[V.year >= 2024]
            keep = pd.concat(keeps).reindex(te_all.index).fillna(True)
            g, b = te_all.good, te_all.bad
            rec_g = 100 * keep[g].mean()
            rej_b = 100 * (~keep[b]).mean()
            rej_a = 100 * (~keep).mean()
            out.append(dict(feature=f, keep=direction, n=len(te_all), recall_good=round(rec_g, 1), bad_rej=round(rej_b, 1), rej_all=round(rej_a, 1), lift=round(rej_b - rej_a, 1),
                            rej_net=round(te_all.net[~keep].mean(), 2) if (~keep).any() else np.nan, kept_net=round(te_all.net[keep].mean(), 2)))
    Q3 = pd.DataFrame(out).sort_values("lift", ascending=False)
    pd.set_option("display.width", 220)
    print(Q3.to_string(index=False))
    ok = Q3[(Q3.recall_good >= 75) & (Q3.bad_rej >= 35) & (Q3.lift >= 10) & (Q3.rej_net < 0)]
    print("passing the bar:", list(zip(ok.feature, ok.keep)) or "NONE")

    # ---------------------------------------------------------------- Q4
    print("\n" + "=" * 110 + "\nQ4 CONFIRM-THEN-BUY (enter at cross+60 minutes) vs RESTING ENTRY, per signal, a skipped signal counts 0\n" + "=" * 110)
    W = U.dropna(subset=["q4a_net"]).copy()
    W["rest"] = W.net.where(W.fill.notna(), 0.0)
    print(f"signals compared: {len(W)}; resting book: mean net per signal {W.rest.mean():+.2f}%, total {W.rest.sum():+.0f} pp-of-position")
    for tag, lab in (("a", "(a) always buy at cross+60"), ("b", "(b) only if hold test passes"), ("c", "(c) hold test AND price <= LH+3.5% (live-like)")):
        col = W[f"q4{tag}_net"]
        diff = col - W.rest
        m, lo, hi = cluster_boot(diff.values, W.symbol.values)
        taken = W[f"q4{tag}_taken"].astype(bool)
        top = np.argsort(-np.abs(diff.values))[:3]
        keep = np.ones(len(W), bool); keep[top] = False
        yrs = {int(y): round(float(diff[W.year == y].mean()), 2) for y in sorted(W.year.unique())}
        print(f"{lab}: taken {int(taken.sum())}/{len(W)}  mean net per signal {col.mean():+.2f}%  paired diff vs resting {m:+.2f}pp (CI {lo:+.2f}..{hi:+.2f}); without top-3 {diff.values[keep].mean():+.2f}; "
              f"GOOD missed {int((~taken & W.good).sum())}/{int(W.good.sum())}; BAD avoided {int((~taken & W.bad).sum())}/{int(W.bad.sum())}")
        print(f"      by year (pp): {yrs}")
    print("\nprice at cross+60 vs listing high (pct): median %.1f, share above the +3.5%% chase cap: %.1f%%" % (W.q4_price60_ext.median(), 100 * (W.q4_price60_ext > 3.5).mean()))
    print("GOOD trades whose price at cross+60 was above the cap:", int(((W.q4_price60_ext > 3.5) & W.good).sum()), "of", int(W.good.sum()))


if __name__ == "__main__":
    main()
