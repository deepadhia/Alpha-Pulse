"""SYNTHETIC_TEST: exercise the scanner's actual stop-update payload expression."""
import ast
from pathlib import Path
import unittest
from datetime import datetime
from unittest.mock import patch
import types
import sys
import html


class TestStopUpdateMetadata(unittest.TestCase):
    def test_update_uses_this_positions_grade_instead_of_a_previous_loops_grade(self):
        path = Path(__file__).with_name("streamlined_ipo_scanner.py")
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "append" and isinstance(n.func.value, ast.Name) and n.func.value.id == "sl_updates_batch")
        expression = compile(ast.Expression(call.args[0]), str(path), "eval")
        scope = dict(sym="SKYWAYS", old_trailing=110.11, new_trailing=117.60, current_price=130.67,
                     entry_price=122.35, pnl=6.8, days_held=1, grade="LISTING_BREAKOUT", pos={"grade": "B", "status": "PAPER_ONLY"})
        update = eval(expression, {}, scope)
        self.assertEqual(update["grade"], "B")
        self.assertEqual(update["status"], "PAPER_ONLY")

    def test_stop_message_does_not_claim_an_atr_calculation(self):
        path = Path(__file__).with_name("streamlined_ipo_scanner.py")
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "format_consolidated_sl_updates")
        scope = dict(SCANNER_VERSION="3.8.0", _now_ist=lambda: datetime(2026, 11, 17, 18, 44))
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), scope)
        utils = types.ModuleType("utils")
        utils.escape_html_text = lambda value: html.escape(str(value))
        with patch.dict(sys.modules, {"utils": utils}):
            message = scope[fn.name]([dict(symbol="SKYWAYS", old_sl=110.11, new_sl=117.60, current_price=130.67,
                                          entry_price=122.35, pnl=6.8, days_held=1, grade="B", status="PAPER_ONLY")])
        self.assertIn("Grade: B", message)
        self.assertIn("Trailing stop", message)
        self.assertNotIn("ATR", message)
        self.assertIn("PAPER", message)
        self.assertIn("<b>SKYWAYS</b>\nBook: PAPER\nGrade: B", message)
        self.assertIn("Previous stop: ₹110.11\nNew stop: ₹117.60", message)
        self.assertNotIn("━", message)
        self.assertNotIn(" · ", message)


if __name__ == "__main__":
    unittest.main()
