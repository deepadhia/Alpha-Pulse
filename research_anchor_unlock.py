# -*- coding: utf-8 -*-
"""
research_anchor_unlock.py -- does price behave differently around the anchor-investor lock-in expiries (day ~30 and day ~90 after listing)?

PRE-REGISTRATION (written 2026-10-21 BEFORE any return around an unlock date was computed).
What was looked at before freezing: nothing about returns.  Only the public SEBI rule (anchor shares: 50% free about 30 days after allotment, the rest about 90
days after allotment; sources disagree on which half is which, which does not matter here because both dates are tested) and data hygiene
(core.setup2.clean_universe: 676 usable symbols).  No allotment dates are in the database, so unlock dates are NOMINAL:
  allotment is about 2-3 sessions before listing (T+3 and T+6 regimes), so   U30 = listing date + 28 calendar days,   U90 = listing date + 88 days.
The true unlock is within about +/- 3 days of the nominal date; the event window below is wide enough to contain it and the measurement is an
event-time path, not a one-day trade.  This is the main limitation: it blurs, and can only bias an effect toward zero.

WHY: LEARNINGS 17 found no entry-time information separating winners.  Calendar structure (supply that is legally allowed to sell on a known date)
is a different kind of information, computable from the listing date alone.  Mechanism: anchors are institutions that held at the issue price; when they
may sell, supply rises and price should show negative abnormal returns around the date (the literature on Indian IPO lock-in expiries reports small
negative effects).  Direction is pre-registered as NEGATIVE.  A positive result would be reported but is not the hypothesis.

STAGE 1 (the only gating test).  Event e = first session on or after the nominal unlock date.  Event window return = close[e+5] / close[e-5] - 1, minus
the equal-weight IPO-universe return over the same dates (daily mean of winsorised +/-20% returns of all clean-universe symbols with age >= 5 days and
at least 20 symbols that day: removes market and cohort-wave moves).  Placebo windows: the same stock, the same shape (e'-5 to e'+5), centred at
ages that avoid every unlock window by at least 10 days:
    for U30 : listing + 42, 50, 58, 66 calendar days            for U90 : listing + 102, 110, 118, 126 calendar days
D = abnormal return of the event window - mean abnormal return of that stock's valid placebo windows (at least 2 valid).  This pairs each stock with
itself, so stock drift and age are controlled.  A stock enters only if the event window exists in the data and, at the window start, close >= Rs 25
and the 20-session mean turnover is >= Rs 0.75 Cr (policy-P liquidity floor).  Two tests: U30 and U90.
  BAR for an unlock effect (each test separately; ALL must hold):
    S1  N >= 100 stocks;
    S2  mean D is NEGATIVE and its 90% bootstrap CI (resampling stocks) excludes 0;
    S3  mean D stays negative after dropping the single most negative stock;
    S4  same sign (negative) in at least 3 of the listing years that have N >= 15.
  Two tests at 90% means one false pass in ten is expected by chance; a single pass without the same-sign year consistency is not an effect.

STAGE 2 (runs ONLY if a Stage 1 test passes; otherwise it is not run and not reported).  Policy-P trades (causal resting, unchanged) split by whether the entry
falls 0-10 calendar days BEFORE a nominal unlock date (U30 or U90).  A skip rule would be adopted only if the trades it removes have a NEGATIVE mean net
with a symbol-cluster 90% CI excluding 0 (the repo bar for any filter, AGENTS rule 14).  Exit or timing rules around unlocks are not tested here (they would
need their own pre-registration).

REPORTED, NOT GATING: mean cumulative abnormal return path in event time (-10..+10 sessions) for the event and for the placebo average; abnormal volume ratio
path; results by listing year; the share of stocks with a negative D.  Evidence tier: REPLAY / event study (not live, not paper).

HONEST LIMITS: nominal dates (about +/- 3 days of blur); equal-weight IPO universe is not Nifty; survivors only; 4.4 years with strong cohort waves; ratios
of a few tenths of a percent are not detectable and not claimed.  A null result is a valid result and ends the line.

  .venv\\Scripts\\python.exe research_anchor_unlock.py --cache data.pkl --out-dir reports/2026-10-21
"""
import argparse
import logging
import os
import pickle
import sys

import numpy as np
import pandas as pd

logging.disable(logging.CRITICAL)
from core.setup2 import clean_universe
from research_alpha_lab import CI_LEVEL, cluster_boot

H = 5                                                    # event window half-width in sessions
NOMINAL = {"U30": 28, "U90": 88}                         # calendar days after the listing date
PLACEBO = {"U30": (42, 50, 58, 66), "U90": (102, 110, 118, 126)}
PRICE_FLOOR, TURNOVER_FLOOR_CR = 25.0, 0.75
MIN_PLACEBOS = 2
PATH_OFFSETS = (-10, -5, -3, -1, 0, 1, 3, 5, 10)


class Sym:
    __slots__ = ("sym", "d", "c", "v", "ld", "year")

    def __init__(self, sym, df, li):
        s = df.drop_duplicates("DATE")
        self.sym = sym
        self.d = pd.DatetimeIndex(s["DATE"])
        self.c = s["CLOSE"].values.astype(float)
        self.v = s["VOLUME"].values.astype(float)
        ld = pd.Timestamp(li["listing_date"])
        self.ld = (ld.tz_localize(None) if ld.tzinfo else ld).normalize()
        self.year = int(self.ld.year)


def build_market(syms):
    """Equal-weight IPO-universe daily return: winsorised +/-20% daily returns of symbols with age >= 5 days, at least 20 symbols a day."""
    cols = []
    for s in syms:
        r = pd.Series(s.c, index=s.d).pct_change()
        r[(s.d - s.ld).days < 5] = np.nan
        cols.append(r.clip(-0.20, 0.20).rename(s.sym))
    wide = pd.concat(cols, axis=1, sort=True)
    m = wide.mean(axis=1)
    m[wide.notna().sum(axis=1) < 20] = np.nan
    return m.fillna(0.0)


def window(s, offset_days, mkt_log, h=H):
    """Abnormal return of [e-h, e+h] where e is the first session on/after listing + offset_days, or None."""
    target = s.ld + pd.Timedelta(days=offset_days)
    e = int(s.d.searchsorted(target))
    if e - h < 5 or e + h >= len(s.d) or (s.d[e] - target).days > 5:
        return None
    a, b = e - h, e + h
    if s.c[a] < PRICE_FLOOR:
        return None
    lo = max(0, a - 20)                              # the engine's convention: the mean of the sessions available, up to 20
    turn = float(np.mean(s.c[lo:a] * s.v[lo:a])) / 1e7
    if turn < TURNOVER_FLOOR_CR:
        return None
    mr = float(np.exp(mkt_log.loc[s.d[b]] - mkt_log.loc[s.d[a]]) - 1.0)
    return dict(e=e, ar=float(s.c[b] / s.c[a] - 1.0 - mr))


def car_path(s, e, mkt_ret, vol_base_slice=(25, 10)):
    """Cumulative abnormal return (from session e-10) and volume ratio at PATH_OFFSETS, or None when the data do not cover it."""
    if e - 10 < 1 or e + 10 >= len(s.d):
        return None
    r = np.clip(np.diff(s.c, prepend=np.nan) / np.concatenate([[np.nan], s.c[:-1]]), -0.20, 0.20)
    ar = r - mkt_ret.reindex(s.d).values
    base = float(np.mean(s.v[max(1, e - vol_base_slice[0]):e - vol_base_slice[1]]))
    car, vol = {}, {}
    for o in PATH_OFFSETS:
        car[o] = float(np.nansum(ar[e - 10 + 1:e + o + 1])) * 100.0
        vol[o] = float(s.v[e + o] / base) if base > 0 else np.nan
    return car, vol


def stage1(name, syms, mkt_log, mkt_ret, out_dir):
    rows, ev_paths, pl_paths = [], [], []
    for s in syms:
        ev = window(s, NOMINAL[name], mkt_log)
        if ev is None:
            continue
        pls = [w for w in (window(s, k, mkt_log) for k in PLACEBO[name]) if w is not None]
        if len(pls) < MIN_PLACEBOS:
            continue
        rows.append(dict(symbol=s.sym, year=s.year, ar_event=ev["ar"] * 100, ar_placebo=float(np.mean([w["ar"] for w in pls])) * 100,
                         n_placebo=len(pls)))
        pe = car_path(s, ev["e"], mkt_ret)
        pp = [car_path(s, w["e"], mkt_ret) for w in pls]
        pp = [x for x in pp if x is not None]
        if pe is not None and pp:
            ev_paths.append(pe)
            pl_paths.append(({o: float(np.mean([x[0][o] for x in pp])) for o in PATH_OFFSETS},
                             {o: float(np.nanmean([x[1][o] for x in pp])) for o in PATH_OFFSETS}))
    d = pd.DataFrame(rows)
    d["D"] = d.ar_event - d.ar_placebo
    print(f"\n--- {name}: nominal unlock = listing + {NOMINAL[name]} days; window sessions e-{H}..e+{H}; placebo centres {PLACEBO[name]} ---")
    n = len(d)
    m, lo, hi = cluster_boot(d.D.values, d.symbol.values)
    worst = d.D.idxmin()
    m_ex = float(d.drop(index=worst).D.mean())
    print(f"  stocks N={n}   abnormal return event window {d.ar_event.mean():+.2f}%   placebo windows {d.ar_placebo.mean():+.2f}%   "
          f"D = {m:+.2f}pp  CI{CI_LEVEL:.0f} [{lo:+.2f}, {hi:+.2f}]   median D {d.D.median():+.2f}   share D<0 {100 * (d.D < 0).mean():.0f}%")
    print(f"  without the most negative stock ({d.symbol[worst]}, {d.D[worst]:+.1f}): {m_ex:+.2f}pp")
    ytab = {}
    for y, g in d.groupby("year"):
        ytab[int(y)] = (len(g), float(g.D.mean()))
        print(f"    listing year {int(y)}: N={len(g):3d}  D {g.D.mean():+6.2f}pp")
    qual = {y: v for y, v in ytab.items() if v[0] >= 15}
    neg_years = [y for y, v in qual.items() if v[1] < 0]
    s1, s2, s3 = n >= 100, (m < 0 and hi < 0), m_ex < 0
    s4 = len(neg_years) >= 3
    print(f"  S1 N >= 100: {'PASS' if s1 else 'FAIL'} | S2 mean D < 0 with CI excluding 0: {'PASS' if s2 else 'FAIL'} | "
          f"S3 negative without the most negative stock: {'PASS' if s3 else 'FAIL'} | "
          f"S4 negative in >= 3 years with N >= 15 (negative years {neg_years} of {sorted(qual)}): {'PASS' if s4 else 'FAIL'}")
    passed = bool(s1 and s2 and s3 and s4)
    if m > 0 and lo > 0:
        print("  NOTE: the CI is entirely POSITIVE (opposite to the pre-registered direction); reported, not the hypothesis, not a pass.")
    if ev_paths:
        print(f"  event-time path, mean cumulative abnormal return in % from session e-10 (event vs placebo average), N={len(ev_paths)}; volume ratio vs the e-25..e-10 mean:")
        print("    offset   " + "".join(f"{o:>9d}" for o in PATH_OFFSETS))
        print("    CAR event  " + "".join(f"{np.mean([p[0][o] for p in ev_paths]):>9.2f}" for o in PATH_OFFSETS))
        print("    CAR placebo" + "".join(f"{np.mean([p[0][o] for p in pl_paths]):>9.2f}" for o in PATH_OFFSETS))
        print("    vol event  " + "".join(f"{np.nanmean([p[1][o] for p in ev_paths]):>9.2f}" for o in PATH_OFFSETS))
        print("    vol placebo" + "".join(f"{np.nanmean([p[1][o] for p in pl_paths]):>9.2f}" for o in PATH_OFFSETS))
    if out_dir:
        d.to_csv(os.path.join(out_dir, f"anchor_unlock_{name}.csv"), index=False)
    return passed, d


def stage2(listing_map, candle_map, clean):
    import research_setup2 as r2
    from research_alpha_lab import line
    P = r2.to_df(r2.run_p(listing_map, candle_map))
    P = P[P.symbol.isin(clean.keys())].copy()
    ld = {s: pd.Timestamp(clean[s][1]["listing_date"]).tz_localize(None).normalize() if pd.Timestamp(clean[s][1]["listing_date"]).tzinfo
          else pd.Timestamp(clean[s][1]["listing_date"]).normalize() for s in clean}

    def before_unlock(row):
        for k in NOMINAL.values():
            delta = ((ld[row.symbol] + pd.Timedelta(days=k)) - row.entry_date).days
            if 0 <= delta <= 10:
                return True
        return False

    P["near"] = [before_unlock(r) for r in P.itertuples()]
    rem, kept = P[P.near], P[~P.near]
    print("\n=== STAGE 2: policy-P trades entered 0-10 calendar days BEFORE a nominal unlock ===")
    print(line("all P trades (clean symbols)", P.net.values, P.symbol.values))
    print(line("REMOVED if skipped (near an unlock)", rem.net.values, rem.symbol.values))
    print(line("kept", kept.net.values, kept.symbol.values))
    if len(rem) > 2:
        m, lo, hi = cluster_boot(rem.net.values, rem.symbol.values)
        ok = hi < 0
        print(f"  skip rule bar: removed mean net {m:+.2f}% CI [{lo:+.1f}, {hi:+.1f}] must be NEGATIVE with the CI excluding 0 -> {'PASS' if ok else 'FAIL (not adopted)'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()
    with open(a.cache, "rb") as fh:
        listing_map, candle_map = pickle.load(fh)
    clean, dropped = clean_universe(listing_map, candle_map)
    syms = [Sym(k, df, li) for k, (df, li) in clean.items()]
    import io

    class Tee:
        def __init__(self):
            self.buf = io.StringIO()

        def write(self, x):
            sys.__stdout__.write(x)
            self.buf.write(x)

        def flush(self):
            sys.__stdout__.flush()

    tee = Tee()
    sys.stdout = tee
    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
    mkt = build_market(syms)
    mkt_log = np.log1p(mkt).cumsum()
    print(f"research_anchor_unlock.py  clean universe {len(syms)} symbols, market series {mkt.index.min().date()} to {mkt.index.max().date()}; CI = {CI_LEVEL:.0f}% bootstrap over stocks")
    print("evidence tier: REPLAY / event study; unlock dates are NOMINAL (listing + 28 / + 88 days, blur about +/- 3 days)")
    passed = {}
    for name in ("U30", "U90"):
        passed[name], _ = stage1(name, syms, mkt_log, mkt, a.out_dir)
    print("\n" + "=" * 100)
    print("STAGE 1 VERDICT: " + "; ".join(f"{k}: {'effect meets the bar' if v else 'does NOT meet the bar'}" for k, v in passed.items()))
    if any(passed.values()):
        stage2(listing_map, candle_map, clean)
    else:
        print("Stage 2 not run (pre-registered: only if a Stage 1 test passes).")
    if a.out_dir:
        with open(os.path.join(a.out_dir, "anchor_unlock_output.txt"), "w", encoding="utf-8") as fh:
            fh.write(tee.buf.getvalue())
    sys.stdout = sys.__stdout__


if __name__ == "__main__":
    main()
