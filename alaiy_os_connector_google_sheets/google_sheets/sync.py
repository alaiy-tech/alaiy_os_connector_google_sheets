# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
The actual sync work + the Google Sheets Sync Log lifecycle helpers every
sync shares. Both directions fan out over every enabled Google Sheets
Mapping, one Sync Log per mapping per run.

run_push_sync (Alaiy OS -> Sheets) is implemented -- Phase 1 full mirror
refresh. run_pull_sync (Sheets -> Alaiy OS) is still a stub, issue #4.
"""

import frappe
from frappe.utils import now_datetime


def get_or_create_log(sync_type, trigger, mapping=None):
    """
    Create a fresh Sync Log for this run, starting as 'queued'. mapping (a
    Google Sheets Mapping name) is optional -- a future run that isn't
    scoped to one mapping can still log without picking one, but
    run_pull_sync/run_push_sync always pass it (one log per mapping per
    run) so the Logs list is filterable by mapping.
    """
    log = frappe.new_doc("Google Sheets Sync Log")
    log.sync_type = sync_type
    log.trigger = trigger
    log.mapping = mapping
    log.status = "queued"
    log.insert(ignore_permissions=True)
    frappe.db.commit()
    return log


def _mark_running(log):
    log.status = "running"
    log.started_at = now_datetime()
    log.save(ignore_permissions=True)
    frappe.db.commit()


def _mark_finished(log, status, error_message=None):
    log.status = status
    log.finished_at = now_datetime()
    if error_message:
        log.error_message = error_message[:2000]
    log.save(ignore_permissions=True)
    frappe.db.commit()


def _run(sync_type, trigger, worker, mapping=None):
    log = get_or_create_log(sync_type, trigger, mapping=mapping)
    _mark_running(log)
    try:
        worker(log)
        _mark_finished(log, "success")
    except Exception:
        _mark_finished(log, "failed", frappe.get_traceback())
        frappe.log_error(
            title=f"Google Sheets connector: {sync_type} sync failed",
            message=frappe.get_traceback(),
        )
        raise


def _enabled_mappings():
    return frappe.get_all("Google Sheets Mapping", filters={"is_enabled": 1}, pluck="name")


def _col_letter_to_index(letter):
    """"A" -> 0, "B" -> 1, ... "AA" -> 26, matching the 0-based index into
    a plain Python list of cells -- the only column-addressing scheme
    Phase 1 supports (header-text columns are a fast-follow, not needed
    yet since every mapping built so far uses letters)."""
    letter = letter.strip().upper()
    index = 0
    for ch in letter:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def run_pull_sync(trigger="scheduled"):
    """Sheets -> Alaiy OS: read changed rows from the mapped Google Sheet
    and apply them to Alaiy OS, through normal doctype validation/
    permissions. TODO: implement (issue #4)."""
    for mapping_name in _enabled_mappings():
        def worker(log, mapping_name=mapping_name):
            pass

        _run("pull", trigger, worker, mapping=mapping_name)


def run_push_sync(trigger="scheduled"):
    """Alaiy OS -> Sheets: for every enabled mapping, overwrite the Sheet
    tab's data rows with the current field values of every record of the
    source doctype -- a full mirror refresh, not an incremental diff
    (matches Phase 1's "read-only mirror" scope; incremental push and
    per-row create/update tracking is a natural extension once this proves
    reliable, not needed for the read-only-mirror milestone itself).
    """
    from alaiy_os_connector_google_sheets.google_sheets.client import GoogleSheetsClient

    for mapping_name in _enabled_mappings():
        def worker(log, mapping_name=mapping_name):
            mapping = frappe.get_doc("Google Sheets Mapping", mapping_name)
            fields = [row.doctype_field for row in mapping.field_map]
            columns = [row.sheet_column for row in mapping.field_map]

            # Text Editor / HTML / Code fields store raw markup as their
            # real value -- confirmed live on ToDo.description, which
            # pushed <div class="ql-editor...">...</div> straight into the
            # Sheet instead of the plain text a Sheet user actually wants
            # to see. Strip tags for exactly these fieldtypes; every other
            # fieldtype's value is written as-is, unchanged.
            meta = frappe.get_meta(mapping.source_doctype)
            html_fields = {
                df.fieldname for df in meta.fields
                if df.fieldtype in ("Text Editor", "HTML Editor", "HTML")
            }

            fetch_fields = fields if mapping.id_field in fields else [mapping.id_field] + fields
            records = frappe.get_all(mapping.source_doctype, fields=fetch_fields)

            def cell_value(record, fieldname):
                value = record.get(fieldname) or ""
                if fieldname in html_fields:
                    value = frappe.utils.strip_html(value)
                return str(value)

            # One row per record, columns in field_map order -- the id_field
            # is intentionally NOT written as a data column here unless it's
            # also explicitly listed in field_map; it exists to let a future
            # pull direction (#4) match a Sheet row back to this record, not
            # to force it onto the Sheet as a visible column.
            rows = [[cell_value(record, f) for f in fields] for record in records]

            client = GoogleSheetsClient()
            start_row = mapping.header_row + 1
            min_col = min(_col_letter_to_index(c) for c in columns)
            max_col = max(_col_letter_to_index(c) for c in columns)
            end_row = start_row + max(len(rows) - 1, 0)

            # Build a full contiguous block (min_col..max_col) even though
            # only the mapped columns have real values, so a single
            # update_values call can write every mapped column regardless
            # of gaps between them (e.g. columns A and D mapped, B/C left
            # untouched) -- Sheets' values.update only accepts one
            # rectangular range per call.
            width = max_col - min_col + 1
            block = []
            for row in rows:
                cells = [""] * width
                for value, col_letter in zip(row, columns):
                    cells[_col_letter_to_index(col_letter) - min_col] = value
                block.append(cells)

            rows_written = 0
            if block:
                start_col = _index_to_col_letter(min_col)
                end_col = _index_to_col_letter(max_col)
                a1_range = f"{mapping.sheet_tab}!{start_col}{start_row}:{end_col}{end_row}"

                # Diff before writing -- ported from gavindsouza/sheets'
                # get_diff-before-save gate. Reading the range back costs
                # one extra API call but skips a write entirely when
                # nothing actually changed since the last run, avoiding
                # needless quota usage and (once real-time collaborators
                # are watching the sheet) a spurious "cell updated" flash
                # for a value that didn't move.
                current = client.get_values(mapping.spreadsheet_id, a1_range)
                current_padded = [
                    (row + [""] * width)[:width] for row in current
                ] + [[""] * width] * max(len(block) - len(current), 0)
                if current_padded[: len(block)] != block:
                    client.update_values(mapping.spreadsheet_id, a1_range, block)
                    rows_written = len(block)

            log.items_processed = len(records)
            log.items_updated = rows_written
            log.save(ignore_permissions=True)
            frappe.db.commit()

            frappe.db.set_value("Google Sheets Mapping", mapping_name, "last_push_row_count", len(records))
            frappe.db.commit()

        _run("push", trigger, worker, mapping=mapping_name)


def _index_to_col_letter(index):
    """Inverse of _col_letter_to_index: 0 -> "A", 26 -> "AA"."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters
