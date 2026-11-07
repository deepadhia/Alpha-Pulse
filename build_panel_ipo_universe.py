# -*- coding: utf-8 -*-
"""
build_panel_ipo_universe.py -- IPO-like listing universe for 2006-2026 from the 20-year NSE panel
(research tool, read-only; added 2026-11-07 for the max-history replay).

WHY: the Atlas / Upstox candle cache only has listings from 2022-05.  The NSE bhavcopy panel in
Learning-Lab (clean_panel.parquet, split-UNadjusted raw prices, includes delisted names) goes back to 2006, so
the same engine can be replayed on 2007-2022 listings.

HOW A LISTING IS IDENTIFIED (a PROXY, not an IPO list; see the replay report for the caveats):
  * the symbol's FIRST panel row is series EQ or BE (series SM = SME board -> migrations are excluded);
  * the first row is on or after 2006-07-01 (the first six months of the panel are existing stocks);
  * it is not the target of a symbol rename (symbolchange.csv, effective date within 7 days of the first row);
  * it is not a continuation of a symbol that stopped trading within 15 days before it started at a price
    within 0.7x..1.4x (renames the list misses).
  CONTAMINATION that remains: NSE cross-listings of old BSE companies, demerged entities, relistings.

PRICE ADJUSTMENT: split / bonus / demerger price factors from all_corporate_actions.parquet are applied
backwards to rows before the ex-date (price * factor, volume / factor) for ex-dates inside the kept window.
Rights are ignored.

Run with the SYSTEM python (needs pyarrow).  Output: two CSV files for the .venv script
`build_panel_pickle.py` (pickles cannot cross pandas versions).

  python build_panel_ipo_universe.py --out-dir data_panel
"""

import argparse
import os

import numpy as np
import pandas as pd

PANEL = r"D:\Personal Projects\Learning-Lab\external\nse_archive\clean_panel.parquet"
CA = r"D:\Personal Projects\Learning-Lab\external\nse_archive\all_corporate_actions.parquet"
SYMCHG = r"D:\Personal Projects\Learning-Lab\external\nse_archive\lists\symbolchange.csv"
WINDOW_DAYS = 1200   # S3 trades up to age 400 d and X1 exits have no time limit, so keep a long tail of history


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data_panel")
    ap.add_argument("--panel", default=PANEL)
    args = ap.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)

    df = pd.read_parquet(args.panel)
    df["series"] = df["series"].astype(str)
    allrows = df.sort_values(["symbol", "date"])
    first = allrows.groupby("symbol", sort=False).head(1).set_index("symbol")
    last_date = allrows.groupby("symbol").date.max()
    last_close = allrows.groupby("symbol").close.last()
    nrows = allrows.groupby("symbol").size()

    panel_start = df.date.min()
    cand = first[first.series.isin(["EQ", "BE"]) & (first.date >= pd.Timestamp("2006-07-01"))].copy()
    n0 = len(cand)

    sc = pd.read_csv(SYMCHG)
    sc.columns = [c.strip() for c in sc.columns]
    sc["eff"] = pd.to_datetime(sc["EFFECTIVE DATE"].str.strip(), format="%d-%b-%Y", errors="coerce")
    sc["NEW SYMBOL"] = sc["NEW SYMBOL"].str.strip()
    ren = sc.dropna(subset=["eff"]).set_index("NEW SYMBOL")["eff"]
    rename_hit = []
    for s, row in cand.iterrows():
        e = ren.get(s)
        if e is not None:
            es = pd.Series(e).iloc[:]
            if ((es - row.date).abs() <= pd.Timedelta(days=7)).any():
                rename_hit.append(s)
    cand = cand.drop(index=rename_hit)

    # heuristic: another symbol ended 0..15 days before, at a similar price
    ends = pd.DataFrame({"last": last_date, "lc": last_close, "n": nrows})
    ends = ends[ends.n >= 60]
    cont = []
    for s, row in cand.iterrows():
        e = ends[(ends["last"] < row.date) & (ends["last"] >= row.date - pd.Timedelta(days=15))]
        e = e.drop(index=[s], errors="ignore")
        if len(e) and (((row.open / e.lc) > 0.7) & ((row.open / e.lc) < 1.4)).any() and (row.open > 0):
            # a real IPO can coincide with an unrelated delisting at a similar price: require tiny-gap match
            m = (row.open / e.lc)
            if ((m > 0.93) & (m < 1.07)).any():
                cont.append(s)
    cand = cand.drop(index=cont)
    print(f"first-row EQ/BE candidates {n0}; rename-list removed {len(rename_hit)}; continuation heuristic removed {len(cont)}; kept {len(cand)}")

    ca = pd.read_parquet(CA)
    ca["ex_date"] = pd.to_datetime(ca["ex_date"])
    ca = ca[ca.kind.isin(["split", "bonus", "demerger"]) & ca.price_factor.between(0.02, 0.999)]

    keep_syms = set(cand.index)
    sub = df[df.symbol.isin(keep_syms) & df.series.isin(["EQ", "BE"])].copy()
    sub["list_date"] = sub.symbol.map(cand.date)
    sub = sub[(sub.date >= sub.list_date) & (sub.date <= sub.list_date + pd.Timedelta(days=WINDOW_DAYS))]
    sub = sub.sort_values(["symbol", "date", "series"]).drop_duplicates(["symbol", "date"], keep="first")
    sub = sub.sort_values(["symbol", "date"]).reset_index(drop=True)

    n_adj = 0
    cag = ca.groupby("symbol")
    out = []
    for s, g in sub.groupby("symbol", sort=False):
        g = g.copy()
        if s in cag.groups:
            for _, r in cag.get_group(s).iterrows():
                if g.date.min() < r.ex_date <= g.date.max():
                    pre = g.date < r.ex_date
                    for c in ("open", "high", "low", "close"):
                        g.loc[pre, c] = g.loc[pre, c] * r.price_factor
                    g.loc[pre, "volume"] = g.loc[pre, "volume"] / r.price_factor
                    n_adj += 1
        out.append(g)
    sub = pd.concat(out, ignore_index=True)
    print(f"corporate-action adjustments applied inside windows: {n_adj}")

    rows = sub[["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]]
    rows.to_csv(os.path.join(args.out_dir, "panel_rows.csv.gz"), index=False)
    d0 = sub.groupby("symbol").head(1)
    lst = pd.DataFrame({"symbol": d0.symbol, "listing_date": d0.date, "ld_open": d0.open, "ld_high": d0.high,
                        "ld_low": d0.low, "ld_close": d0.close, "ld_volume": d0.volume,
                        "first_series": d0.symbol.map(cand.series)})
    lst.to_csv(os.path.join(args.out_dir, "panel_listings.csv"), index=False)
    print(lst.listing_date.dt.year.value_counts().sort_index().to_string())
    print("wrote", len(rows), "rows,", len(lst), "listings to", args.out_dir)


if __name__ == "__main__":
    main()
