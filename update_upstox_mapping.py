#!/usr/bin/env python3
"""
update_upstox_mapping.py

Automatically update MongoDB instrument_keys collection with new IPO symbols.
This script:
1. Reads recent IPOs from MongoDB ipos collection
2. Uses Upstox API to find instrument keys for missing symbols
3. Updates MongoDB instrument_keys collection automatically
"""

import os
import pandas as pd
import requests
import time
from datetime import datetime
from dotenv import load_dotenv
import logging

# Load environment
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

# Configuration
UPSTOX_ACCESS_TOKEN = os.getenv('UPSTOX_ACCESS_TOKEN')

def get_isin_from_nse(symbol):
    """Get ISIN code from NSE equity list"""
    try:
        url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        session = requests.Session()
        session.headers.update(headers)
        
        # Establish session
        session.get("https://www.nseindia.com", timeout=15)
        resp = session.get(url, timeout=45)
        resp.raise_for_status()
        
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        
        # Find symbol column
        symbol_col = None
        isin_col = None
        
        for col in df.columns:
            col_upper = col.upper()
            if 'SYMBOL' in col_upper:
                symbol_col = col
            elif 'ISIN' in col_upper:
                isin_col = col
        
        if symbol_col and isin_col:
            match = df[df[symbol_col] == symbol]
            if not match.empty:
                isin = match[isin_col].iloc[0]
                return isin
        
        return None
    except Exception as e:
        logger.debug(f"Error getting ISIN from NSE for {symbol}: {e}")
        return None

def search_instrument_key(symbol, listing_date=None):
    """Search for instrument key using Upstox API - requires ISIN code"""
    if not UPSTOX_ACCESS_TOKEN:
        logger.warning("UPSTOX_ACCESS_TOKEN not found - cannot search for instrument keys")
        return None
    
    try:
        headers = {
            'Accept': 'application/json',
            'Api-Version': '2.0',
            'Authorization': f'Bearer {UPSTOX_ACCESS_TOKEN}'
        }
        
        logger.info(f"Searching for {symbol}...")
        
        # Step 1: Get ISIN code from NSE (same as existing mappings use)
        isin = get_isin_from_nse(symbol)
        
        if not isin:
            logger.warning(f"Could not find ISIN for {symbol} from NSE - skipping")
            return None
        
        # Use ISIN in instrument key format (same as existing mappings: NSE_EQ|INE...)
        instrument_key = f"NSE_EQ|{isin}"
        logger.info(f"Found ISIN for {symbol}: {isin}")
        
        # Step 2: Get company name from NSE data (if available)
        name = symbol  # Default to symbol
        
        try:
            url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            session = requests.Session()
            session.headers.update(headers)
            session.get("https://www.nseindia.com", timeout=15)
            resp = session.get(url, timeout=45)
            resp.raise_for_status()
            
            from io import StringIO
            df = pd.read_csv(StringIO(resp.text))
            
            # Find columns
            symbol_col = None
            name_col = None
            for col in df.columns:
                col_upper = col.upper()
                if 'SYMBOL' in col_upper:
                    symbol_col = col
                elif 'NAME' in col_upper and 'COMPANY' in col_upper:
                    name_col = col
            
            if symbol_col and name_col:
                match = df[df[symbol_col] == symbol]
                if not match.empty:
                    name = match[name_col].iloc[0]
        except:
            pass  # Use symbol as name if we can't fetch
        
        # Return mapping in same format as existing ones (match_type: 'exact')
        logger.info(f"Created mapping for {symbol}: {instrument_key}")
        return {
            'ipo_symbol': symbol,
            'upstox_symbol': symbol,
            'name': name,
            'instrument_key': instrument_key,
            'match_type': 'exact'  # Same as existing mappings
        }
        
    except Exception as e:
        logger.error(f"Error searching for {symbol}: {e}")
        return None

def update_mapping_from_db():
    """Update MongoDB instrument_keys collection with new IPO symbols from ipos_col."""
    try:
        from db import ipos_col, upsert_instrument_key, ensure_indexes, get_instrument_key_mapping
        ensure_indexes()

        if ipos_col is None:
            logger.warning("ipos_col not available — cannot update mappings")
            return 0

        # Get all IPO symbols from MongoDB
        docs = list(ipos_col.find({}, {"_id": 0, "symbol": 1}))
        if not docs:
            logger.warning("ipos_col is empty — no recent IPOs to process")
            return 0

        recent_symbols = [d["symbol"] for d in docs if d.get("symbol")]

        # Get already-mapped symbols from instrument_keys collection
        existing_mapping = get_instrument_key_mapping()
        existing_symbols = set(existing_mapping.keys())

        new_mappings = []
        updated_count = 0

        for symbol in recent_symbols:
            if symbol in existing_symbols:
                continue
            if any(pat in symbol for pat in ['-RE', '-SM', 'RE1']):
                logger.info(f"Skipping rights entitlement / SME symbol: {symbol}")
                continue

            logger.info(f"Processing {symbol}...")
            mapping = search_instrument_key(symbol)

            if mapping:
                new_mappings.append(mapping)
                updated_count += 1
                upsert_instrument_key(
                    ipo_symbol=mapping['ipo_symbol'],
                    instrument_key=mapping['instrument_key'],
                    isin=mapping['instrument_key'].split('|')[-1] if '|' in mapping['instrument_key'] else None,
                    name=mapping.get('name', mapping['ipo_symbol']),
                    match_type=mapping.get('match_type', 'exact')
                )
                logger.info(f"✅ Added mapping for {symbol}")
            else:
                logger.warning(f"⚠️ Could not find mapping for {symbol} - skipping")

            time.sleep(0.2)

        logger.info(f"[MongoDB] Upserted {updated_count} new instrument key mappings")
        return updated_count

    except Exception as e:
        logger.error(f"Error updating mapping from DB: {e}")
        return 0

# Keep backward-compatible alias (deprecated CSV ref)
def update_mapping_csv():
    return update_mapping_from_db()


def main():
    """Main function"""
    try:
        print("="*60)
        print("Upstox Mapping Updater")
        print("="*60)
    except:
        print("="*60)
        print("Upstox Mapping Updater")
        print("="*60)
    
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    print()
    
    if not UPSTOX_ACCESS_TOKEN:
        print("WARNING: UPSTOX_ACCESS_TOKEN not found!")
        print("   Mapping update will be limited.")
        print("   Set UPSTOX_ACCESS_TOKEN in .env file for full functionality.")
        print()
    
    updated = update_mapping_csv()
    
    print()
    print("="*60)
    print(f"Update Complete: {updated} new mappings added")
    print("="*60)

if __name__ == "__main__":
    main()

