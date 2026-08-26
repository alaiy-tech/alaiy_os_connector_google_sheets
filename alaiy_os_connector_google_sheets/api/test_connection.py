# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Reachability check for the connected Google account. Wired into the
registry via connector_meta["test_method"] and called by the "Test
Connection" button. Always returns {"success": bool, "message": str} --
never raises to the caller.
"""

import frappe


@frappe.whitelist()
def test_connection():
    doc = frappe.get_single("Google Sheets Connector Settings")
    # doc.gs_refresh_token (a Password field) is not decrypted on a plain
    # attribute read -- get_password() is required to see whether a real
    # value is actually stored, same as oauth.py's own checks.
    if not (doc.get_password("gs_refresh_token") if doc.gs_refresh_token else None):
        return {"success": False, "message": "No Google account connected yet."}

    from alaiy_os_connector_google_sheets.google_sheets.oauth import get_valid_access_token

    try:
        get_valid_access_token()
        email = doc.gs_connected_email or "unknown account"
        return {"success": True, "message": f"Connected as {email}"}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}
