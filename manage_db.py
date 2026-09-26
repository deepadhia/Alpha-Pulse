"""
manage_db.py
Unified entrypoint for MongoDB infrastructure tasks.
"""
import sys
import argparse
import subprocess

def run_script(script_name, args=None):
    cmd = [sys.executable, script_name]
    if args:
        cmd.extend(args)
    print(f"\nRunning: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

def main():
    parser = argparse.ArgumentParser(description="IPO Scanner MongoDB Management Tool")
    parser.add_argument("task", choices=["test", "backfill-all", "validate", "backup", "analyze", "quality", "recent", "backtest", "diagnose", "fill"], 
                        help="Task to perform")
    parser.add_argument("--today", action="store_true", help="For validation: logs only for today")
    parser.add_argument("--days", type=int, default=3, help="For analysis/quality/recent: number of days")
    parser.add_argument("--limit", type=int, default=20, help="For recent: max number of logs")
    parser.add_argument("--symbol", type=str, help="For fill: stock symbol (e.g. SUDEEPPHRM)")
    parser.add_argument("--price", type=float, help="For fill: executed fill price (e.g. 124.50)")
    parser.add_argument("--notes", type=str, default="", help="For fill: optional execution notes")
    parser.add_argument("--symbols", nargs="+", help="For diagnose: symbols to analyze (e.g. KUSUMGAR CMRGREEN)")
    parser.add_argument("--vs-winners", action="store_true", help="For diagnose: compare with winning breakouts")
    parser.add_argument("--system", action="store_true", help="For diagnose: run system-wide strategy self-diagnosis")

    args = parser.parse_args()

    if args.task == "fill":
        if not args.symbol or args.price is None:
            print("❌ Error: --symbol and --price are required for 'fill' task (e.g. python manage_db.py fill --symbol SUDEEPPHRM --price 124.50)")
            sys.exit(1)
        from db import update_manual_fill_price
        success = update_manual_fill_price(args.symbol, args.price, args.notes)
        if success:
            print(f"✅ Position for {args.symbol.upper()} updated to ACTIVE with execution fill price ₹{args.price:,.2f}.")
        else:
            print(f"❌ Failed to update fill price for {args.symbol.upper()}. Make sure an active/paper position exists.")

    elif args.task == "test":
        run_script("test_db_connection.py")
    
    elif args.task == "backfill-all":
        print("Starting full backfill sequence...")
        run_script("backfill_instrument_keys.py")
        run_script("backfill_metadata.py")
        run_script("mongodb_backfill.py")
        print("\n✅ All backfills complete.")

    elif args.task == "validate":
        v_args = ["--today-logs-only"] if args.today else []
        run_script("compare_csv_vs_db.py", v_args)

    elif args.task == "backup":
        run_script("mongodb_backup.py")

    elif args.task == "analyze":
        run_script("analyze_telemetry.py", ["--days", str(args.days)])

    elif args.task == "quality":
        run_script("analyze_db_log_quality.py", ["--days", str(args.days)])

    elif args.task == "recent":
        import os
        path = os.path.join("scratch", "check_recent_logs.py")
        run_script(path, ["--days", str(args.days), "--limit", str(args.limit)])

    elif args.task == "backtest":
        run_script("run_latest_rules_backtest.py")

    elif args.task == "diagnose":
        d_args = []
        if args.system:
            d_args.append("--system")
        if args.symbols:
            d_args.extend(args.symbols)
        if args.vs_winners:
            d_args.append("--vs-winners")
        run_script("diagnose_trade.py", d_args)

if __name__ == "__main__":
    main()
