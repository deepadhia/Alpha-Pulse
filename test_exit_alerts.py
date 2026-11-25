"""Exit provenance and presentation checks; synthetic records only."""
import unittest
import ast
import html
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
from core.exit_alerts import format_exit_card


class TestExitCards(unittest.TestCase):
    def card(self, **changes):
        p = dict(symbol="RENTOMOJO", entry_source="RESTING_AUTOFILL", status="CLOSED",
                 days_held=23, entry_price=555, exit_price=550.56, pnl_pct=-0.8, exit_reason="Stop Loss Hit")
        p.update(changes)
        return format_exit_card(p)

    def test_pending_forward_price_is_explicitly_provisional(self):
        text = self.card(exit_settled=False)
        self.assertIn("Book: P RESTING PAPER FORWARD", text)
        self.assertIn("awaiting next-open settlement", text)
        self.assertIn("Provisional P&L: -0.80%", text)

    def test_modelled_settlement_is_not_presented_as_broker_fill(self):
        text = self.card(exit_settled=True, exit_fill_model="daily_open_tier_spread")
        self.assertIn("modelled; broker execution unverified", text)
        self.assertNotIn("Provisional P&L", text)

    def test_quote_settlement_still_identifies_paper_execution(self):
        self.assertIn("opening quote; broker execution unverified", self.card(exit_settled=True, exit_fill_model="quote_bid_open"))

    def test_shelf_exit_uses_vertical_card(self):
        text = self.card(symbol="EDELALTS", entry_source="SCANNER", status="PAPER_CLOSED", entry_price=1852.90,
                         exit_price=1785.10, pnl_pct=-3.66, days_held=14)
        self.assertIn("<b>EDELALTS</b>\nBook: PAPER\nHeld: 14 days", text)
        self.assertIn("Entry price: ₹1,852.90\nRecorded exit price: ₹1,785.10", text)
        self.assertNotIn("━", text)

    def test_scanner_record_does_not_claim_verified_broker_sale(self):
        self.assertIn("Book: SCANNER (broker execution unverified)", self.card(entry_source="SCANNER"))

    def test_dynamic_text_is_escaped(self):
        text = self.card(symbol="A<B", exit_reason="X & Y")
        self.assertIn("A&lt;B", text)
        self.assertIn("X &amp; Y", text)

    def test_real_scanner_alert_and_portfolio_show_pending_forward_settlement(self):
        path = Path(__file__).with_name("streamlined_ipo_scanner.py")
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        names = {"format_exit_alert", "format_alphapulse_portfolio_report"}
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        scope = dict(datetime=datetime, pd=pd, logger=logging.getLogger("SYNTHETIC_TEST"),
                     SCANNER_VERSION="3.8.0", _now_ist=lambda: datetime(2026, 11, 25, 18, 44))
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), scope)
        p = dict(symbol="RENTOMOJO", status="CLOSED", entry_source="RESTING_AUTOFILL", exit_settled=False,
                 entry_price=555, exit_price=550.56, pnl_pct=-0.8, days_held=23,
                 exit_reason="Stop Loss Hit", exit_date=datetime(2026, 11, 25))
        with patch.dict("sys.modules", {
            "db": SimpleNamespace(db=SimpleNamespace(positions=SimpleNamespace(find=lambda: [p]))),
            "utils": SimpleNamespace(escape_html_text=lambda v: html.escape(str(v or "")))}):
            report = scope["format_alphapulse_portfolio_report"]()
            alert = scope["format_exit_alert"]("RENTOMOJO", "Stop Loss Hit", 550.56, -0.8, 23, 555, position=p)
        for text in (report, alert):
            self.assertIn("Book: P RESTING PAPER FORWARD", text)
            self.assertIn("Provisional P&L: -0.80%", text)
            self.assertNotIn("━", text)
