#!/usr/bin/env python3
"""
analyze_positions_performance.py
Analyzes active, paper, and closed positions from MongoDB with strict
version-wise cohort isolation (Native v3.5.0 Live vs Historical/Backfilled).
"""

import sys
import os
import pandas as pd
from datetime import datetime

# Force stdout/stderr to use UTF-8 on Windows to prevent UnicodeEncodeError for emojis
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Set terminal color codes
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    DIM = '\033[2m'
    END = '\033[0m'

# Check if terminal supports color
if os.name == 'nt':
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

def print_trade_table(df_subset, title, color_header=Colors.BLUE):
    print("\n" + "-" * 90)
    print(f"{Colors.BOLD}{color_header}{title} ({len(df_subset)} Trades){Colors.END}")
    print("-" * 90)
    
    if df_subset.empty:
        print("  No trades in this cohort.")
        return
        
    df_c = df_subset.copy()
    df_c["pnl_pct"] = pd.to_numeric(df_c["pnl_pct"], errors='coerce').fillna(0.0)
    winners = df_c[df_c["pnl_pct"] > 0]
    losers = df_c[df_c["pnl_pct"] <= 0]
    win_rate = (len(winners) / len(df_c)) * 100
    avg_pnl = df_c["pnl_pct"].mean()
    
    print(f"  • Win Rate:      {Colors.BOLD}{Colors.GREEN if win_rate >= 50 else Colors.YELLOW}{win_rate:.1f}%{Colors.END} ({len(winners)} Win / {len(losers)} Loss)")
    print(f"  • Average PnL:   {Colors.BOLD}{Colors.GREEN if avg_pnl >= 0 else Colors.RED}{avg_pnl:+.2f}%{Colors.END}")
    print(f"  • Max Run:       {Colors.BOLD}{Colors.GREEN}+{df_c['pnl_pct'].max():.2f}%{Colors.END}  |  Min DD: {Colors.BOLD}{Colors.RED}{df_c['pnl_pct'].min():.2f}%{Colors.END}")
    
    print(f"\n{Colors.BOLD}{Colors.UNDERLINE}{'Symbol':<14} {'Entry Date':<12} {'Entry':<10} {'Current':<10} {'PnL %':<10} {'Held':<8} {'Type':<12} {'Version':<8}{Colors.END}")
    
    sorted_df = df_c.sort_values(by="pnl_pct", ascending=False)
    for _, row in sorted_df.iterrows():
        pnl_val = row['pnl_pct']
        pnl_color = Colors.GREEN if pnl_val > 0 else (Colors.RED if pnl_val < 0 else '')
        pnl_str = f"{pnl_val:+.2f}%"
        
        entry_date_str = str(row['entry_date'])[:10]
        grade_str = str(row.get('grade', 'N/A'))[:10]
        entry_price = float(row.get('entry_price', 0.0))
        current_price = float(row.get('current_price', entry_price))
        days_held = f"{int(row.get('days_held', 0))}d"
        ver_str = str(row.get('version', 'legacy'))[:6]
        
        print(f"{Colors.BOLD}{row['symbol']:<14}{Colors.END} {entry_date_str:<12} {entry_price:<10.2f} {current_price:<10.2f} {pnl_color}{pnl_str:<10}{Colors.END} {days_held:<8} {grade_str:<12} {ver_str:<8}")

def main():
    try:
        from db import get_all_positions_df
    except ImportError:
        print("Error: Could not import db.py. Make sure you run this script from the project root.")
        sys.exit(1)
        
    print("=" * 90)
    print(f"{Colors.BOLD}{Colors.CYAN}🏛️  ALPHAPULSE — VERSION-ISOLATED PORTFOLIO PERFORMANCE AUDIT{Colors.END}")
    print(f"Run Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
    print("=" * 90)
    
    df_pos = get_all_positions_df()
    if df_pos.empty:
        print("No positions found in MongoDB.")
        sys.exit(0)
        
    if "_backfilled" in df_pos.columns:
        df_pos["_backfilled"] = df_pos["_backfilled"].map(lambda x: bool(x) if pd.notna(x) else False)
    else:
        df_pos["_backfilled"] = False
    df_pos["version"] = df_pos["version"].fillna("legacy")
    df_pos["pnl_pct"] = pd.to_numeric(df_pos["pnl_pct"], errors='coerce').fillna(0.0)

    # Cohort definitions
    # 1. Native Live v3.5.0: generated natively by v3.5.0 scanner (not backfilled)
    native_v35 = df_pos[(df_pos["version"] == "3.5.0") & (~df_pos["_backfilled"])]
    
    # 2. Backfilled Legacy Cohort: historical/backfilled tracking
    backfilled = df_pos[df_pos["_backfilled"]]
    
    # 3. Paper / Capacity Overflow Cohort
    paper_df = df_pos[df_pos["status"].isin(["PAPER_ONLY", "PAPER_CLOSED"])]
    
    # 4. Closed positions
    closed_df = df_pos[df_pos["status"] == "CLOSED"]
    
    print(f"{Colors.BOLD}📊 System Cohort Counts:{Colors.END}")
    print(f"  • Total Positions Stored:       {Colors.BOLD}{len(df_pos)}{Colors.END}")
    print(f"  • 🟢 Pure Native v3.5.0 Live:   {Colors.BOLD}{Colors.GREEN}{len(native_v35)}{Colors.END} (Forward Production Scorecard)")
    print(f"  • 📑 Backfilled Historical:     {Colors.BOLD}{Colors.YELLOW}{len(backfilled)}{Colors.END} (Reference Evidence Only)")
    print(f"  • 📄 Paper / Overflow Setups:   {Colors.BOLD}{Colors.BLUE}{len(paper_df)}{Colors.END}")
    print(f"  • 🚪 Realized Closed Trades:    {Colors.BOLD}{len(closed_df)}{Colors.END}")
    
    # SECTION 1: NATIVE v3.5.0 LIVE PORTFOLIO
    print_trade_table(
        native_v35,
        "🟢 PURE NATIVE v3.5.0 LIVE PRODUCTION SCORECARD",
        color_header=Colors.GREEN
    )
    
    # SECTION 2: BACKFILLED ACTIVE POSITIONS (Legacy Tracking)
    backfilled_active = backfilled[backfilled["status"] == "ACTIVE"]
    print_trade_table(
        backfilled_active,
        "📑 BACKFILLED ACTIVE TRADES (Managing Out to Velocity / SL)",
        color_header=Colors.YELLOW
    )
    
    # SECTION 3: PAPER PORTFOLIO (Forward Testing Overflow)
    paper_active = paper_df[paper_df["status"] == "PAPER_ONLY"]
    print_trade_table(
        paper_active,
        "📄 PAPER-ONLY OVERFLOW PORTFOLIO (Capacity Protection)",
        color_header=Colors.BLUE
    )

    # SECTION 4: CLOSED TRADES (Realized Outcomes)
    print("\n" + "-" * 90)
    print(f"{Colors.BOLD}{Colors.CYAN}🚪 REALIZED CLOSED TRADES ({len(closed_df)} Trades){Colors.END}")
    print("-" * 90)
    if not closed_df.empty:
        c_winners = closed_df[closed_df["pnl_pct"] > 0]
        c_losers = closed_df[closed_df["pnl_pct"] <= 0]
        c_win_rate = (len(c_winners) / len(closed_df)) * 100
        c_avg_pnl = closed_df["pnl_pct"].mean()
        
        print(f"  • Realized Win Rate:  {Colors.BOLD}{Colors.GREEN if c_win_rate >= 40 else Colors.YELLOW}{c_win_rate:.1f}%{Colors.END} ({len(c_winners)} Win / {len(c_losers)} Loss)")
        print(f"  • Average Realized:   {Colors.BOLD}{Colors.GREEN if c_avg_pnl >= 0 else Colors.RED}{c_avg_pnl:+.2f}%{Colors.END}")
        
        if "exit_reason" in closed_df.columns:
            closed_df = closed_df.copy()
            closed_df["exit_reason"] = closed_df["exit_reason"].fillna("Unknown / Historical")
            reason_counts = closed_df["exit_reason"].value_counts()
            print(f"\n{Colors.BOLD}🚪 Exit Reason Breakdown:{Colors.END}")
            for reason, count in reason_counts.items():
                print(f"  • {reason:<58} : {Colors.BOLD}{count}{Colors.END}")
    
    print("=" * 90)

if __name__ == "__main__":
    main()
