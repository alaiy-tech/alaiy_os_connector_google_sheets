"""Value normalisation shared by push and pull. Runs without a bench:
python -m unittest alaiy_os_connector_google_sheets.tests.test_sync_values"""

import sys
import types
import unittest

if "frappe" not in sys.modules:
    fake = types.ModuleType("frappe")
    fake.utils = types.SimpleNamespace(strip_html=lambda s: __import__("re").sub(r"<[^>]*>", "", s))
    fake._ = lambda s: s
    fake.whitelist = lambda *a, **k: (lambda f: f)
    sys.modules["frappe"] = fake
    sys.modules["frappe.utils"] = types.SimpleNamespace(now_datetime=None)

from alaiy_os_connector_google_sheets.google_sheets.sync import _coerce, _norm  # noqa: E402


def df(fieldtype):
    return types.SimpleNamespace(fieldtype=fieldtype)


class TestValues(unittest.TestCase):
    def test_numbers_match_across_sides(self):
        self.assertEqual(_norm(10.0, df("Float")), _norm("10", df("Float")))
        self.assertEqual(_norm("1,250.50", df("Currency")), "1250.5")

    def test_checkbox(self):
        self.assertEqual(_norm(1, df("Check")), _norm("TRUE", df("Check")))
        self.assertEqual(_norm(0, df("Check")), _norm("", df("Check")))

    def test_html_is_stripped_on_both_sides(self):
        html = '<div class="ql-editor"><p>hello</p></div>'
        self.assertEqual(_norm(html, df("Text Editor")), _norm("hello", df("Text Editor")))

    def test_empty_cell_on_typed_field_is_none(self):
        self.assertIsNone(_coerce("", df("Link")))
        self.assertIsNone(_coerce("", df("Date")))
        self.assertIsNone(_coerce("", df("Float")))
        self.assertEqual(_coerce("", df("Data")), "")
        self.assertEqual(_coerce("7", df("Int")), 7)
        self.assertEqual(_coerce("1", df("Check")), 1)


if __name__ == "__main__":
    unittest.main()
