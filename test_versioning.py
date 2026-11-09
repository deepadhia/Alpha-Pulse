# -*- coding: utf-8 -*-
"""Version-sync guard: the six touchpoints (AGENTS.md section 2) must match, and the native forward cohort must include the current version.
Static file parsing only; nothing imports the scanners or touches MongoDB."""
import os
import re
import unittest

from core.versioning import NATIVE_FORWARD_CUTOFF, NATIVE_FORWARD_VERSIONS, TRUSTED_RECORD_VERSIONS, is_native_forward, is_trusted_record

BASE = os.path.dirname(os.path.abspath(__file__))
SCANNER_RE = r'SCANNER_VERSION\s*=\s*["\']([^"\']+)["\']'


def grab(name, pattern):
    with open(os.path.join(BASE, name), encoding="utf-8") as fh:
        m = re.search(pattern, fh.read())
    assert m, f"{name}: pattern not found"
    return m.group(1)


class TestVersionSync(unittest.TestCase):
    def test_six_touchpoints_match(self):
        expected = grab("master_audit.py", r'EXPECTED_VERSION\s*=\s*["\']([^"\']+)["\']')
        found = {f: grab(f, SCANNER_RE) for f in ("streamlined_ipo_scanner.py", "listing_day_breakout_scanner.py",
                                                  "hourly_breakout_scanner.py", "db.py")}
        found["README badge"] = grab("README.md", r"badge/version-([0-9.]+)-orange")
        found["README footer"] = grab("README.md", r"AlphaPulse v([0-9]+\.[0-9]+\.[0-9]+) \|")
        for name, ver in found.items():
            self.assertEqual(ver, expected, name)

    def test_cohort_includes_the_current_version(self):
        current = grab("db.py", SCANNER_RE)
        self.assertIn(current, NATIVE_FORWARD_VERSIONS)


class TestNativeForward(unittest.TestCase):
    def test_rules(self):
        d = NATIVE_FORWARD_CUTOFF
        self.assertTrue(is_native_forward("3.5.0", False, d))
        self.assertTrue(is_native_forward("3.6.0", False, "2026-11-01"))
        self.assertFalse(is_native_forward("3.6.0", True, "2026-11-01"))      # backfilled
        self.assertFalse(is_native_forward("3.5.0", False, "2026-09-28"))     # before the cutoff
        self.assertTrue(is_native_forward("3.7.0", False, "2026-11-05"))      # entry rules unchanged in 3.7.0: same forward cohort
        self.assertTrue(is_native_forward("3.8.0", False, "2026-11-09"))      # v3.8.0: partial-day gate elimination
        self.assertFalse(is_native_forward("3.3.0", False, "2026-11-01"))     # other version
        self.assertFalse(is_native_forward(None, False, "2026-11-01"))


class TestTrustedRecords(unittest.TestCase):
    def test_only_guarded_native_rows_are_trusted(self):
        self.assertTrue(is_trusted_record("3.7.0", False))
        self.assertTrue(is_trusted_record("3.8.0", False))
        self.assertFalse(is_trusted_record("3.7.0", True))        # backfilled
        self.assertFalse(is_trusted_record("3.6.0", False))       # stored before the entry guard existed
        self.assertFalse(is_trusted_record(None, False))

    def test_trusted_versions_are_inside_the_forward_cohort(self):
        for v in TRUSTED_RECORD_VERSIONS:
            self.assertIn(v, NATIVE_FORWARD_VERSIONS)


if __name__ == "__main__":
    unittest.main()
