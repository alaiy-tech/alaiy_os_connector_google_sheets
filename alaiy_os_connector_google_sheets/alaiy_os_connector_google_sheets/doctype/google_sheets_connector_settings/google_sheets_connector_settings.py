# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class GoogleSheetsConnectorSettings(Document):
    def validate(self):
        # old_enabled is the last-committed DB value, so this comparison has
        # to run before the save overwrites it. Heavy setup runs only on the
        # 0 -> 1 transition, not on every save.
        old_enabled = frappe.db.get_single_value(
            "Google Sheets Connector Settings", "is_enabled"
        ) or 0
        self.flags.google_sheets_just_enabled = bool(self.is_enabled and not old_enabled)
        self.flags.google_sheets_just_disabled = bool(not self.is_enabled and old_enabled)
        self._sync_registry_is_enabled()

    def on_update(self):
        # Deferred to on_update (after this row is written) so any code that
        # reads back the freshly saved credentials sees the new values.
        if self.flags.google_sheets_just_enabled:
            self._on_first_enable()
        elif self.flags.google_sheets_just_disabled:
            self._on_disable()

    def _on_first_enable(self):
        # Nothing needed yet -- unlike the product/order connectors this
        # was cloned from, there are no ERPNext custom fields to add on
        # first enable. Real first-enable setup lands with the Mapping
        # doctype (validating/creating anything a mapping needs).
        pass

    def _on_disable(self):
        # Deliberately does NOT revoke the Google OAuth grant or clear
        # gs_refresh_token -- disabling the connector just stops scheduled
        # syncs (see google_sheets/sync_jobs.py's is_enabled check).
        # Revoking the actual connection is a separate, explicit action
        # (see google_sheets/oauth.py::disconnect), so toggling this
        # checkbox off and back on doesn't force reconnecting Google.
        pass

    def _sync_registry_is_enabled(self):
        if frappe.db.exists("OS Connector Registry", "google_sheets"):
            frappe.db.set_value(
                "OS Connector Registry", "google_sheets", "is_enabled", self.is_enabled
            )
