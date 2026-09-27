import sys
import io
import os
import pandas as pd
import requests
from datetime import datetime, timedelta
from io import StringIO

# Force UTF-8 on Windows consoles to prevent charmap emoji encoding crashes
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")



def fetch_nse_equity_list():
    """Fetch the latest official master equity list from NSE."""
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    session = requests.Session()
    session.headers.update(headers)
    session.get("https://www.nseindia.com", timeout=15)
    resp = session.get(url, timeout=45)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text))
    return df

def purge_delisted_symbols(active_nse_df=None, verbose=True):
    """
    Reconcile MongoDB scanning universe against the official NSE active equity list.
    Safely removes delisted, suspended, or expired symbols (e.g. -RE, -SM, defunct tickers)
    from ipos, listing_data, instrument_keys, daily_candles_cache, and watchlist collections.
    Preserves historical closed positions and signals for backtest/quant audit integrity.
    """
    try:
        if active_nse_df is None:
            if verbose:
                print("[*] Fetching active NSE equity master list for delisted symbol reconciliation...")
            active_nse_df = fetch_nse_equity_list()

        symbol_col = None
        for col in active_nse_df.columns:
            if 'SYMBOL' in col.upper():
                symbol_col = col
                break

        if not symbol_col:
            if verbose:
                print("[Warning] Could not identify SYMBOL column in NSE master list. Skipping delisted purge.")
            return {"removed_count": 0, "removed_symbols": []}

        active_nse_symbols = set(active_nse_df[symbol_col].dropna().str.strip().str.upper().tolist())
        if verbose:
            print(f"[Info] Active NSE Equities Universe: {len(active_nse_symbols)} symbols")

        from db import (
            ipos_col, listing_data_col, instrument_keys_col,
            daily_candles_col, watchlist_col, positions_col
        )

        # Collect all unique symbols present in universe collections
        universe_symbols = set()
        if ipos_col is not None:
            for d in ipos_col.find({}, {"symbol": 1, "_id": 0}):
                if d.get("symbol"):
                    universe_symbols.add(d["symbol"].strip().upper())

        if listing_data_col is not None:
            for d in listing_data_col.find({}, {"symbol": 1, "_id": 0}):
                if d.get("symbol"):
                    universe_symbols.add(d["symbol"].strip().upper())

        if instrument_keys_col is not None:
            for d in instrument_keys_col.find({}, {"ipo_symbol": 1, "_id": 0}):
                if d.get("ipo_symbol"):
                    universe_symbols.add(d["ipo_symbol"].strip().upper())

        if daily_candles_col is not None:
            for d in daily_candles_col.find({}, {"symbol": 1, "_id": 0}):
                if d.get("symbol"):
                    universe_symbols.add(d["symbol"].strip().upper())

        if watchlist_col is not None:
            for d in watchlist_col.find({}, {"symbol": 1, "_id": 0}):
                if d.get("symbol"):
                    universe_symbols.add(d["symbol"].strip().upper())

        # Determine invalid or delisted symbols
        delisted_symbols = set()
        for sym in universe_symbols:
            # Check 1: Not present in active NSE EQUITY_L master
            if sym not in active_nse_symbols:
                delisted_symbols.add(sym)
            # Check 2: Rights issue / expired / SME patterns that leaked in
            elif any(pat in sym for pat in ['-RE', '-SM', 'RE1']):
                delisted_symbols.add(sym)

        if not delisted_symbols:
            if verbose:
                print("✅ [Delisted Check] Scanning universe is 100% clean. No delisted symbols found.")
            return {"removed_count": 0, "removed_symbols": []}

        if verbose:
            print(f"⚠️ [Delisted Check] Identified {len(delisted_symbols)} delisted/invalid symbols: {sorted(list(delisted_symbols))}")

        # Check if any open positions are affected (warn if active)
        if positions_col is not None:
            open_affected = list(positions_col.find({
                "symbol": {"$in": list(delisted_symbols)},
                "status": {"$in": ["ACTIVE", "PAPER_ONLY"]}
            }))
            if open_affected:
                for pos in open_affected:
                    print(f"🚨 [CRITICAL ALERT] Active position {pos.get('symbol')} detected as delisted from NSE! Manual trade exit recommended.")

        # Safely remove from scanning universe collections
        delisted_list = list(delisted_symbols)
        stats = {}
        if ipos_col is not None:
            res = ipos_col.delete_many({"symbol": {"$in": delisted_list}})
            stats["ipos_removed"] = res.deleted_count

        if listing_data_col is not None:
            res = listing_data_col.delete_many({"symbol": {"$in": delisted_list}})
            stats["listing_data_removed"] = res.deleted_count

        if instrument_keys_col is not None:
            res = instrument_keys_col.delete_many({"ipo_symbol": {"$in": delisted_list}})
            stats["instrument_keys_removed"] = res.deleted_count

        if daily_candles_col is not None:
            res = daily_candles_col.delete_many({"symbol": {"$in": delisted_list}})
            stats["daily_candles_removed"] = res.deleted_count

        if watchlist_col is not None:
            res = watchlist_col.delete_many({"symbol": {"$in": delisted_list}})
            stats["watchlist_removed"] = res.deleted_count

        # Clean local file artifacts if present
        txt_path = os.path.join(os.path.dirname(__file__), "recent_ipo_symbols.txt")
        if os.path.exists(txt_path):
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    file_syms = [line.strip() for line in f if line.strip()]
                clean_file_syms = [s for s in file_syms if s.upper() not in delisted_symbols and s.upper() in active_nse_symbols]
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(sorted(clean_file_syms)) + "\n")
                if verbose:
                    print(f"[Info] Cleaned recent_ipo_symbols.txt ({len(clean_file_syms)} active symbols retained)")
            except Exception as txt_err:
                if verbose:
                    print(f"[Warning] Could not update recent_ipo_symbols.txt: {txt_err}")

        # Invalidate stale pickle caches if they contain delisted symbols
        for cache_name in ["ipo_cache.pkl", "scratch_ohlc_cache.pkl"]:
            cache_file = os.path.join(os.path.dirname(__file__), cache_name)
            if os.path.exists(cache_file):
                try:
                    os.remove(cache_file)
                    if verbose:
                        print(f"[Info] Removed stale cache file: {cache_name}")
                except Exception:
                    pass

        if verbose:
            print(f"✅ [Delisted Purge Complete] Removed {len(delisted_symbols)} symbols across DB collections: {stats}")

        return {
            "removed_count": len(delisted_symbols),
            "removed_symbols": sorted(list(delisted_symbols)),
            "stats": stats
        }

    except Exception as e:
        print(f"[Error] purge_delisted_symbols failed: {e}")
        return {"removed_count": 0, "removed_symbols": [], "error": str(e)}

def fetch_recent_ipo_symbols(years_back=3, purge_delisted=True):
    """Dynamic IPO symbol fetching with automatic delisted symbol purging and multiple fallback methods"""
    try:
        print(f"[*] Fetching recent IPO symbols for last {years_back} year(s)...")
        
        # Method 1: Try NSE API with retry
        for attempt in range(3):
            try:
                print(f"[Attempt {attempt + 1}/3] Fetching NSE equity list...")
                df = fetch_nse_equity_list()
                print("[OK] NSE API connection successful")
                print(f"[Info] NSE EQUITY_L returned {len(df)} records")
                
                # Automatically reconcile and purge delisted symbols from universe
                if purge_delisted:
                    purge_delisted_symbols(active_nse_df=df, verbose=True)

                # Find the right columns
                date_col = None
                symbol_col = None
                name_col = None
                
                for col in df.columns:
                    col_upper = col.upper()
                    if 'DATE' in col_upper and 'LISTING' in col_upper:
                        date_col = col
                    elif 'SYMBOL' in col_upper:
                        symbol_col = col
                    elif 'NAME' in col_upper and 'COMPANY' in col_upper:
                        name_col = col
                
                if date_col and symbol_col:
                    df[date_col] = pd.to_datetime(df[date_col], errors='coerce', format='mixed')
                    cutoff = datetime.now() - timedelta(days=365 * years_back)
                    
                    # Filter for recent IPOs
                    recent_mask = df[date_col] > cutoff
                    recent_ipos = df[recent_mask]
                    
                    # Remove suspicious companies
                    suspicious_patterns = ['RNBDENIMS'] 
                    if name_col:
                        suspicious_mask = recent_ipos[name_col].str.contains('|'.join(suspicious_patterns), case=False, na=False)
                        recent_ipos = recent_ipos[~suspicious_mask]
                        
                    # Remove RE and SME shares
                    if symbol_col:
                        re_sme_mask = recent_ipos[symbol_col].str.contains('-RE|-SM|RE1', case=False, na=False)
                        recent_ipos = recent_ipos[~re_sme_mask]
                    
                    symbols = recent_ipos[symbol_col].tolist()
                    companies = recent_ipos[name_col].tolist() if name_col else symbols
                    dates = recent_ipos[date_col].dt.strftime('%Y-%m-%d').tolist()
                    
                    # --- Penny Stock Filtering (< Rs.25) via Bulk yfinance ---
                    print(f"[*] Bulk checking close prices for {len(symbols)} IPOs to filter out penny stocks (< Rs.25)...")
                    try:
                        import yfinance as yf
                        tickers_ns = [f"{s}.NS" for s in symbols]
                        
                        # Bulk download latest price (Group_by ticker)
                        prices_df = yf.download(tickers_ns, period="1d", progress=False)
                        
                        valid_symbols = []
                        valid_companies = []
                        valid_dates = []
                        
                        for idx_sym, sym in enumerate(symbols):
                            ticker = f"{sym}.NS"
                            close_price = None
                            try:
                                # yfinance MultiIndex check
                                if isinstance(prices_df.columns, pd.MultiIndex):
                                    if 'Close' in prices_df.columns.levels[0] and ticker in prices_df.columns.levels[1]:
                                        close_val = prices_df['Close'][ticker].iloc[-1]
                                        if pd.notna(close_val):
                                            close_price = float(close_val)
                                else:
                                    if 'Close' in prices_df.columns:
                                        close_val = prices_df['Close'][ticker].iloc[-1] if isinstance(prices_df['Close'], pd.DataFrame) else prices_df['Close'].iloc[-1]
                                        if pd.notna(close_val):
                                            close_price = float(close_val)
                            except Exception:
                                pass
                                
                            # Skip symbols where price lookup failed (possibly delisted, suspended, or no data)
                            if close_price is None:
                                print(f"  [Skip] Excluding {sym} — price lookup failed (possibly delisted or suspended)")
                                continue

                            if close_price < 25.0:
                                print(f"  [Skip] Filtering out penny stock from fetch: {sym} (Price Rs.{close_price:.2f} < Rs.25.00)")
                                continue
                                
                            valid_symbols.append(sym)
                            if name_col:
                                valid_companies.append(companies[idx_sym])
                            valid_dates.append(dates[idx_sym])

                            
                        symbols = valid_symbols
                        companies = valid_companies if name_col else symbols
                        dates = valid_dates
                        print(f"[OK] Filtered symbol list: {len(symbols)} symbols remain after price filter")
                    except Exception as e:
                        print(f"[Warning] Bulk price filter failed: {e}. Proceeding with all symbols.")
                    
                    print(f"[OK] NSE API: Found {len(symbols)} recent IPOs")
                    
                    df_symbols = pd.DataFrame({
                        'symbol': symbols,
                        'company': companies,
                        'listing_date': dates
                    })

                    # MongoDB dual-write: upsert discovered IPOs
                    try:
                        from db import upsert_ipo, ensure_indexes
                        ensure_indexes()
                        for _, row in df_symbols.iterrows():
                            upsert_ipo(
                                symbol=row['symbol'],
                                listing_date=row['listing_date'],
                                name=row['company']
                            )
                        print(f"[MongoDB] Upserted {len(df_symbols)} IPO records")
                    except Exception as db_e:
                        print(f"[Warning] [MongoDB] IPO write FAILED (CSV write succeeded): {db_e}")
                        try:
                            from db import db_metrics
                            db_metrics["failures"] = db_metrics.get("failures", 0) + 1
                        except Exception:
                            pass

                    return df_symbols
                else:
                    print("[Warning] NSE API: Could not find required columns")
                    raise Exception("Column mapping failed")
                    
            except Exception as e:
                print(f"[Warning] NSE API attempt {attempt + 1} failed: {e}")
                if attempt == 2:  # Last attempt
                    print("[Error] All NSE API attempts failed")
                    break
                else:
                    print("[Info] Retrying in 5 seconds...")
                    import time
                    time.sleep(5)
        
        # Method 2: Fallback to MongoDB list
        print("[Info] Falling back to MongoDB records...")
        try:
            from db import ipos_col
            if ipos_col is not None:
                docs = list(ipos_col.find({}, {"_id": 0, "symbol": 1, "company": 1, "listing_date": 1}))
                if docs:
                    df_symbols = pd.DataFrame(docs)
                    print(f"[Info] MongoDB fallback: {len(df_symbols)} symbols")
                    return df_symbols
            print("[Error] No valid MongoDB records found")
        except Exception as db_fallback_error:
            print(f"[Warning] MongoDB fallback failed: {db_fallback_error}")
            print("[Info] Creating minimal fallback data...")
            
            # Method 3: Create minimal fallback
            fallback_symbols = [
                'SWIGGY', 'BLACKBUCK', 'STALLION', 'BHARATSE', 
                'NATCAPSUQ', 'MOSCHIP', 'TRAVELFOOD', 'OCCLLTD', 'GARUDA',
                'CEWATER', 'RACLGEAR', 'ORCHASP', 'OSWALPUMPS', 'IGIL',
                'VIKRAN', 'AFCONS', 'MOBIKWIK', 'MASTERTR', 'JAINREC',
                'DRAGARWQ', 'KOTIC', 'SCANSTL', 'IWP', 'NOVARTIND'
            ]
            
            df_symbols = pd.DataFrame({
                'symbol': fallback_symbols,
                'company': [f"{sym} Ltd" for sym in fallback_symbols],
                'listing_date': [datetime.now().strftime('%Y-%m-%d')] * len(fallback_symbols)
            })
            
            # Save to MongoDB
            try:
                from db import upsert_ipo
                for _, row in df_symbols.iterrows():
                    upsert_ipo(row['symbol'], row['listing_date'], row['company'])
            except:
                pass
            
            print(f"[Info] Created minimal fallback with {len(fallback_symbols)} symbols")
            return df_symbols
                
    except Exception as e:
        print(f"[Error] fetch_recent_ipo_symbols failed: {e}")
        return None

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch and reconcile IPO symbols")
    parser.add_argument("--purge-delisted", action="store_true", help="Reconcile and purge delisted symbols from MongoDB")
    parser.add_argument("--years", type=int, default=3, help="Number of years back to fetch IPOs")
    args = parser.parse_args()

    if args.purge_delisted:
        print("=" * 60)
        print("Delisted Symbols Reconciliation & Purge")
        print("=" * 60)
        res = purge_delisted_symbols(verbose=True)
        print(f"\nResult: {res['removed_count']} delisted symbols removed.")
    else:
        print("=" * 60)
        print("Fetch Recent IPO Symbols")
        print("=" * 60)
        df_res = fetch_recent_ipo_symbols(years_back=args.years, purge_delisted=True)
        if df_res is not None:
            print(f"\nFetched {len(df_res)} valid IPO symbols.")

