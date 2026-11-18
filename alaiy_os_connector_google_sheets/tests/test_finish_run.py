"""How a Google Sheets sync run is closed. Runs without a bench."""

import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

MODULE = "alaiy_os_connector_google_sheets.google_sheets.sync"


def _core_run_status(processed, failed, skipped, error=None):
    if processed > 0 and failed >= processed:
        return "failed"
    if processed == 0 and (error or skipped > 0):
        return "failed"
    return "success"


class _ValidationError(Exception):
    pass


def _load(core=True):
    frappe = types.ModuleType("frappe")
    frappe.db = MagicMock()
    frappe._ = lambda s: s
    frappe.ValidationError = _ValidationError
    frappe.clear_messages = lambda: None
    frappe.get_traceback = lambda: "traceback"
    frappe.log_error = MagicMock()
    frappe.whitelist = lambda *a, **k: (lambda f: f)
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: None
    utils.strip_html = lambda s: s
    frappe.utils = utils
    stubs = {"frappe": frappe, "frappe.utils": utils, "alaiy_os": types.ModuleType("alaiy_os"),
             "alaiy_os.health": types.ModuleType("alaiy_os.health")}
    if core:
        runs = types.ModuleType("alaiy_os.health.runs")
        runs.run_status = _core_run_status
        stubs["alaiy_os.health.runs"] = runs
    else:
        stubs["alaiy_os.health.runs"] = None  # makes the import raise ImportError
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    sys.modules.pop(MODULE, None)
    return importlib.import_module(MODULE), saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    sys.modules.pop(MODULE, None)


class Base(unittest.TestCase):
    core = True

    def setUp(self):
        self.sync, saved = _load(self.core)
        self.addCleanup(_restore, saved)
        self.log = SimpleNamespace(status="", error_message=None, items_processed=0, items_failed=0,
                                   save=lambda **k: None)
        self.sync.get_or_create_log = lambda *a, **k: self.log

    def run_with(self, processed, failed, raises=None):
        def worker(log):
            if raises:
                raise raises
            log.items_processed, log.items_failed = processed, failed

        self.sync._run("pull", "manual", worker)
        return self.log


class TestWithCore(Base):
    def test_every_item_failed(self):
        log = self.run_with(5, 5)
        self.assertEqual(log.status, "failed")
        self.assertEqual(log.error_message, "Every item failed (5); see the log.")

    def test_some_failed_is_success(self):
        log = self.run_with(5, 2)
        self.assertEqual(log.status, "success")
        self.assertFalse(log.error_message)

    def test_nothing_processed_is_success(self):
        self.assertEqual(self.run_with(0, 0).status, "success")

    def test_push_without_failed_count_is_success(self):
        del self.log.items_failed
        self.assertEqual(self.run_with_push(4).status, "success")

    def run_with_push(self, processed):
        def worker(log):
            log.items_processed = processed

        self.sync._run("push", "manual", worker)
        return self.log

    def test_worker_raises(self):
        log = self.run_with(0, 0, raises=ValueError("boom"))
        self.assertEqual(log.status, "failed")
        self.assertEqual(log.error_message, "traceback")


class TestWithoutCore(Base):
    core = False

    def test_fallback_keeps_old_behaviour(self):
        self.assertEqual(self.run_with(5, 5).status, "success")


if __name__ == "__main__":
    unittest.main()
