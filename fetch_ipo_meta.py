# -*- coding: utf-8 -*-
"""
fetch_ipo_meta.py -- collect issue size, issue price and subscription multiples for mainboard IPOs (research data, local files only).

Source: Trendlyne public monthly IPO screener pages, e.g. https://next.trendlyne.com/ipo/screener/year-2025/mainline/aug/ .
Why this source: the page lists the full month (the Chittorgarh free tier returns only 5 records for past years, so it is NOT used and nothing
there is bypassed).  Trendlyne robots.txt allows /ipo/ and asks for a 1 second delay for generic agents; this script waits 2 seconds, sends a
descriptive User-Agent, fetches each month once (raw HTML is cached in data_ipo_meta/raw/ and never re-fetched) and makes about 51 requests in total.
Check the site terms before any commercial use; this is personal research data.

Writes data_ipo_meta/ipo_meta_raw.csv (one row per IPO row on the pages).  Matching to database symbols is done by research_ipo_meta_signal.py.
Nothing is written to MongoDB.

  .venv\\Scripts\\python.exe fetch_ipo_meta.py
"""
import argparse
import csv
import os
import re
import time

import requests
from bs4 import BeautifulSoup

UA = "AlphaPulse-research/1.0 (personal research, low volume; contact dadhia@nuvista.ai)"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
COLS = ["company", "ltp", "mcap_cr", "listing_date", "issue_size", "issue_price", "qib", "hni", "retail", "total_sub", "list_open", "list_close", "list_gain", "cur_gain"]


def num(s):
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", s.replace("₹", " "))
    return float(m.group(0).replace(",", "")) if m else None


def parse(html):
    soup = BeautifulSoup(html, "lxml")
    best = None
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if best is None or len(rows) > len(best):
            best = rows
    out = []
    for tr in best or []:
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) >= 13:
            out.append(tds[:14])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-06")
    ap.add_argument("--end", default="2026-09")
    ap.add_argument("--out-dir", default="data_ipo_meta")
    ap.add_argument("--delay", type=float, default=2.0)
    a = ap.parse_args()
    raw = os.path.join(a.out_dir, "raw")
    os.makedirs(raw, exist_ok=True)
    sy, sm = map(int, a.start.split("-"))
    ey, em = map(int, a.end.split("-"))
    sess = requests.Session()
    rows, fetched, cached, empty = [], 0, 0, []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        key = f"{y}-{m:02d}"
        path = os.path.join(raw, f"{key}.html")
        if os.path.exists(path):
            html = open(path, encoding="utf-8").read()
            cached += 1
        else:
            url = f"https://next.trendlyne.com/ipo/screener/year-{y}/mainline/{MONTHS[m - 1]}/"
            r = sess.get(url, headers={"User-Agent": UA}, timeout=60)
            if r.status_code == 429:
                print("429: stopping; rerun later (cached months are kept)")
                break
            html = r.text if r.status_code == 200 else ""
            if html:
                open(path, "w", encoding="utf-8").write(html)
            fetched += 1
            time.sleep(a.delay)
        parsed = parse(html) if html else []
        if not parsed:
            empty.append(key)
        for tds in parsed:
            rows.append(dict(zip(COLS, tds), month=key))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    out = os.path.join(a.out_dir, "ipo_meta_raw.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["month"] + COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"months fetched {fetched}, cached {cached}, rows {len(rows)}, months with no table rows {empty}")
    print("wrote", out)


if __name__ == "__main__":
    main()
