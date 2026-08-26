# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Whitelisted entry points the Alaiy OS connector card and the settings form
call to kick off / inspect syncs. These stay thin: enqueue the real work
on the long queue and return immediately.

No log is pre-created here (unlike the template this was cloned from) --
run_pull_sync/run_push_sync fan out over every enabled Google Sheets
Mapping and create one Sync Log per mapping themselves (see
google_sheets/sync.py), so there is no single log to show as "queued"
in advance; the Logs list picks up each mapping's row once its own sync
actually starts.
"""

import frappe


@frappe.whitelist()
def trigger_pull_sync():
    """Manually enqueue a 'pull' sync (Sheets → Alaiy OS) for every enabled mapping."""
    frappe.enqueue(
        "alaiy_os_connector_google_sheets.google_sheets.sync.run_pull_sync",
        queue="long",
        timeout=600,
        trigger="manual",
    )
    return {"queued": True}


@frappe.whitelist()
def trigger_push_sync():
    """Manually enqueue a 'push' sync (Alaiy OS → Sheets) for every enabled mapping."""
    frappe.enqueue(
        "alaiy_os_connector_google_sheets.google_sheets.sync.run_push_sync",
        queue="long",
        timeout=600,
        trigger="manual",
    )
    return {"queued": True}


@frappe.whitelist()
def get_sync_status(sync_type=None):
    """
    Return the most recent Google Sheets Sync Log rows, newest first.

    The Alaiy OS connector card passes the registry slot name ("categories"
    or "items"); map those to this connector's own sync_type values.
    """
    filters = {}
    if sync_type:
        type_map = {"categories": "pull", "items": "push"}
        filters["sync_type"] = type_map.get(sync_type, sync_type)
    return frappe.get_all(
        "Google Sheets Sync Log",
        filters=filters,
        fields=[
            "name", "sync_type", "trigger", "status",
            "started_at", "finished_at",
            "items_processed", "items_created", "items_updated", "items_failed",
            "pages_total", "pages_done",
            "error_message",
        ],
        order_by="started_at desc",
        limit=5,
    )
