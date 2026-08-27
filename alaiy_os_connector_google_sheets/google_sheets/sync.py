# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
The actual sync work + the Google Sheets Sync Log lifecycle helpers every
sync shares. Both directions fan out over every enabled Google Sheets
Mapping, one Sync Log per mapping per run, and both check Google Sheets
Sync State to avoid stomping a field that changed on both sides since
the last sync (see run_pull_sync's docstring for how conflicts are
detected and left for an admin to resolve).
"""

import frappe
from frappe import _
from frappe.utils import now_datetime


@frappe.whitelist()
def resolve_conflict(sync_state_name):
    """Clears a flagged conflict. Resets last_synced_value to empty rather
    than to either side's current value -- the admin may have just edited
    one side after seeing the conflict, or may resolve it by editing
    later; either way, an empty baseline makes the NEXT sync treat this
    field as never-before-synced, so whichever value is current on
    Frappe or the Sheet at that point applies normally (same as any
    other first-sync field), instead of guessing which side "won" here."""
    frappe.only_for("System Manager")
    doc = frappe.get_doc("Google Sheets Sync State", sync_state_name)
    if not doc.conflict_flagged:
        frappe.throw(_("This row isn't flagged as a conflict."))
    doc.conflict_flagged = 0
    doc.last_synced_value = ""
    doc.save()


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


def _load_sync_state(mapping_name):
    """{(record_id, fieldname): {"name", "last_synced_value", "conflict_flagged"}}
    for every tracked field of this mapping, one query instead of one per
    record+field -- real row counts here are the same order of magnitude as
    the records being synced, so this stays cheap even for a full mapping."""
    rows = frappe.get_all(
        "Google Sheets Sync State",
        filters={"mapping": mapping_name},
        fields=["name", "record_id", "fieldname", "last_synced_value", "conflict_flagged"],
    )
    return {(r.record_id, r.fieldname): r for r in rows}


def _save_sync_state(mapping_name, record_id, fieldname, value, existing_row, conflict_flagged=False):
    """Upserts the (mapping, record, field) baseline used by conflict
    detection on the next sync. Called after a value is successfully
    applied on either side (push wrote it to the Sheet, or pull wrote it
    to Frappe) -- at that point both sides agree, so this becomes the new
    baseline both future changes get compared against."""
    if existing_row:
        frappe.db.set_value(
            "Google Sheets Sync State", existing_row.name,
            {"last_synced_value": value, "conflict_flagged": 1 if conflict_flagged else 0},
            update_modified=False,
        )
    else:
        frappe.get_doc({
            "doctype": "Google Sheets Sync State",
            "mapping": mapping_name,
            "record_id": record_id,
            "fieldname": fieldname,
            "last_synced_value": value,
            "conflict_flagged": 1 if conflict_flagged else 0,
        }).insert(ignore_permissions=True)


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
    """Sheets -> Alaiy OS: for every enabled mapping, read the mapped
    range from the Sheet, match each row back to a record via the ID
    Column, and apply every column marked Editable from Sheet to that
    record through normal doctype validation/permissions. A row whose ID
    Column is blank, or whose id doesn't match an existing record, is
    skipped and counted, never silently creating or guessing a target --
    Phase 1/2 scope is updating existing records, not import-by-pull.

    A row that fails validation (bad link field, etc.) is caught, logged,
    and does not stop the rest of the run -- same per-row isolation
    push sync and every other connector in this codebase already uses.

    Conflict detection (per field, per record): if the current Frappe
    value AND the current Sheet value have both moved away from the last
    value both sides agreed on (Google Sheets Sync State), and they now
    disagree with each other, neither side is applied -- the field is
    flagged as a conflict instead, with both real values recorded, and
    stays flagged (skipped by future syncs) until an admin resolves it by
    clearing the flag. A field where only ONE side changed is not a
    conflict -- it applies normally, same as before this existed.
    """
    from alaiy_os_connector_google_sheets.google_sheets.client import GoogleSheetsClient

    for mapping_name in _enabled_mappings():
        def worker(log, mapping_name=mapping_name):
            mapping = frappe.get_doc("Google Sheets Mapping", mapping_name)
            editable_rows = [row for row in mapping.field_map if row.editable_from_sheet]
            if not editable_rows:
                # Nothing on this mapping is writable from the Sheet side --
                # a perfectly valid configuration (e.g. a mapping that's
                # push-only in practice), just nothing to do here.
                log.items_processed = 0
                log.save(ignore_permissions=True)
                frappe.db.commit()
                return

            fields = [row.doctype_field for row in editable_rows]
            columns = [row.sheet_column for row in editable_rows] + [mapping.id_column]
            min_col = min(_col_letter_to_index(c) for c in columns)
            max_col = max(_col_letter_to_index(c) for c in columns)

            client = GoogleSheetsClient()
            start_row = mapping.header_row + 1
            start_col = _index_to_col_letter(min_col)
            end_col = _index_to_col_letter(max_col)
            a1_range = f"{mapping.sheet_tab}!{start_col}{start_row}:{end_col}"
            sheet_rows = client.get_values(mapping.spreadsheet_id, a1_range)

            id_col_offset = _col_letter_to_index(mapping.id_column) - min_col
            field_col_offsets = [(f, _col_letter_to_index(c) - min_col) for f, c in zip(fields, columns)]
            sync_state = _load_sync_state(mapping_name)

            processed, updated, failed, skipped, conflicts = 0, 0, 0, 0, 0
            for sheet_row in sheet_rows:
                processed += 1
                record_id = sheet_row[id_col_offset].strip() if id_col_offset < len(sheet_row) else ""
                if not record_id or not frappe.db.exists(mapping.source_doctype, {mapping.id_field: record_id}):
                    skipped += 1
                    continue

                try:
                    doc = frappe.get_doc(mapping.source_doctype, {mapping.id_field: record_id})
                    changed = False
                    for fieldname, offset in field_col_offsets:
                        sheet_value = sheet_row[offset] if offset < len(sheet_row) else ""
                        frappe_value = str(doc.get(fieldname) or "")
                        state = sync_state.get((record_id, fieldname))
                        # An empty baseline (no state row yet, or one just
                        # cleared by resolve_conflict) is treated as "never
                        # synced" the same way -- otherwise a resolved
                        # conflict's cleared baseline ("" is not None) would
                        # still count as a real prior value below.
                        baseline = (state.last_synced_value or None) if state else None

                        if state and state.conflict_flagged:
                            # Already flagged from a previous run and not
                            # yet resolved -- skip until an admin clears it,
                            # regardless of what either side says now.
                            continue

                        if sheet_value == frappe_value:
                            # Sides agree (whether or not this differs from
                            # the old baseline) -- nothing to apply, but the
                            # baseline should reflect the now-agreed value.
                            if baseline != sheet_value:
                                _save_sync_state(mapping_name, record_id, fieldname, sheet_value, state)
                            continue

                        frappe_moved = baseline is not None and frappe_value != baseline
                        sheet_moved = baseline is not None and sheet_value != baseline

                        if frappe_moved and sheet_moved:
                            # Both sides changed to DIFFERENT values since
                            # the last agreed baseline -- a real conflict.
                            # Apply neither; record both real values so an
                            # admin can see exactly what disagreed.
                            _save_sync_state(
                                mapping_name, record_id, fieldname,
                                f"CONFLICT -- Alaiy OS: {frappe_value!r} / Sheet: {sheet_value!r}",
                                state, conflict_flagged=True,
                            )
                            conflicts += 1
                            continue

                        # Only the Sheet moved (or there's no baseline yet,
                        # i.e. first sync ever for this field) -- apply it,
                        # same behavior as before conflict detection existed.
                        doc.set(fieldname, sheet_value)
                        changed = True
                        _save_sync_state(mapping_name, record_id, fieldname, sheet_value, state)

                    if changed:
                        doc.save()
                        updated += 1
                except Exception:
                    failed += 1
                    frappe.log_error(
                        title=f"Google Sheets connector: pull sync row failed ({mapping_name})",
                        message=f"record_id={record_id}\n{frappe.get_traceback()}",
                    )
                    frappe.db.rollback()

            frappe.db.commit()

            log.items_processed = processed
            log.items_updated = updated
            log.items_failed = failed
            log.conflict_count = conflicts
            log.save(ignore_permissions=True)
            frappe.db.commit()

            frappe.db.set_value("Google Sheets Mapping", mapping_name, "last_pull_row_count", processed)
            frappe.db.commit()

        _run("pull", trigger, worker, mapping=mapping_name)


def run_push_sync(trigger="scheduled"):
    """Alaiy OS -> Sheets: for every enabled mapping, overwrite the Sheet
    tab's data rows with the current field values of every record of the
    source doctype -- a full mirror refresh, not an incremental diff
    (matches Phase 1's "read-only mirror" scope; incremental push and
    per-row create/update tracking is a natural extension once this proves
    reliable, not needed for the read-only-mirror milestone itself).

    A field flagged as a conflict (see run_pull_sync) is left alone here
    too -- pushing the Frappe value over it would silently resolve the
    conflict in Frappe's favor without an admin ever seeing it. That one
    cell keeps whatever the Sheet currently shows until the conflict is
    resolved; every other cell in the same mirror still refreshes normally.
    """
    from alaiy_os_connector_google_sheets.google_sheets.client import GoogleSheetsClient

    for mapping_name in _enabled_mappings():
        def worker(log, mapping_name=mapping_name):
            mapping = frappe.get_doc("Google Sheets Mapping", mapping_name)
            fields = [row.doctype_field for row in mapping.field_map] + [mapping.id_field]
            columns = [row.sheet_column for row in mapping.field_map] + [mapping.id_column]

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

            id_field = mapping.id_field
            records = frappe.get_all(mapping.source_doctype, fields=list(dict.fromkeys(fields)))
            sync_state = _load_sync_state(mapping_name)
            conflicted_fields = {
                (key[0], key[1]) for key, row in sync_state.items() if row.conflict_flagged
            }

            def cell_value(record, fieldname):
                value = record.get(fieldname) or ""
                if fieldname in html_fields:
                    value = frappe.utils.strip_html(value)
                return str(value)

            # One row per record, columns in field_map order plus the
            # dedicated id_column last -- pull sync (#4) reads that column
            # to find which record a row belongs to. A conflicted cell gets
            # a sentinel here (None) rather than the real value, resolved
            # against the sheet's current content further down.
            rows = [
                [
                    None if (str(record.get(id_field) or ""), f) in conflicted_fields
                    else cell_value(record, f)
                    for f in fields
                ]
                for record in records
            ]

            client = GoogleSheetsClient()
            start_row = mapping.header_row + 1
            min_col = min(_col_letter_to_index(c) for c in columns)
            max_col = max(_col_letter_to_index(c) for c in columns)
            end_row = start_row + max(len(rows) - 1, 0)
            width = max_col - min_col + 1

            # Write the header row itself -- confirmed live, header_row was
            # only ever used to compute where DATA starts; nothing wrote the
            # titles into it, so a sheet with no headers already typed in by
            # hand synced real data into a column-title-less grid with no
            # way to tell which column was which. Real field labels (not
            # raw fieldnames -- "Item Name", not "item_name"), same
            # min_col..max_col contiguous block shape as the data write
            # below, skipped if that exact row already has the same values
            # (no point re-writing an unchanged header on every push).
            field_labels = {df.fieldname: (df.label or df.fieldname) for df in meta.fields}
            field_labels[id_field] = field_labels.get(id_field) or id_field
            header_cells = [""] * width
            for f, col_letter in zip(fields, columns):
                header_cells[_col_letter_to_index(col_letter) - min_col] = field_labels.get(f, f)
            header_range = (
                f"{mapping.sheet_tab}!{_index_to_col_letter(min_col)}{mapping.header_row}"
                f":{_index_to_col_letter(max_col)}{mapping.header_row}"
            )
            current_header = client.get_values(mapping.spreadsheet_id, header_range)
            current_header_row = (current_header[0] if current_header else []) + [""] * width
            if current_header_row[:width] != header_cells:
                client.update_values(mapping.spreadsheet_id, header_range, [header_cells])

            rows_written = 0
            if rows:
                start_col = _index_to_col_letter(min_col)
                end_col = _index_to_col_letter(max_col)
                a1_range = f"{mapping.sheet_tab}!{start_col}{start_row}:{end_col}{end_row}"

                # Read the current range up front for two reasons: to fill
                # in a conflicted cell's sentinel with whatever the sheet
                # already shows (leaving it untouched), and (ported from
                # gavindsouza/sheets' get_diff-before-save gate) to skip
                # the write entirely when nothing actually changed --
                # avoiding needless quota usage and, once real-time
                # collaborators are watching the sheet, a spurious "cell
                # updated" flash for a value that didn't move.
                current = client.get_values(mapping.spreadsheet_id, a1_range)
                current_padded = [
                    (row + [""] * width)[:width] for row in current
                ] + [[""] * width] * max(len(rows) - len(current), 0)

                # Build a full contiguous block (min_col..max_col) even
                # though only the mapped columns have real values, so a
                # single update_values call can write every mapped column
                # regardless of gaps between them (e.g. columns A and D
                # mapped, B/C left untouched) -- Sheets' values.update only
                # accepts one rectangular range per call.
                block = []
                for row_index, row in enumerate(rows):
                    cells = list(current_padded[row_index])
                    for value, col_letter in zip(row, columns):
                        if value is not None:
                            cells[_col_letter_to_index(col_letter) - min_col] = value
                    block.append(cells)

                if current_padded[: len(block)] != block:
                    client.update_values(mapping.spreadsheet_id, a1_range, block)
                    rows_written = len(block)

            # Whatever was actually written now matches the Sheet, so it
            # becomes the new agreed baseline for conflict detection --
            # skips id_field itself (not a Sheet-visible data field to
            # track) and any cell that was left alone above because it's
            # mid-conflict (its baseline stays whatever it already was
            # until the conflict is resolved).
            for record in records:
                record_id = str(record.get(id_field) or "")
                for f in fields:
                    if f == id_field or (record_id, f) in conflicted_fields:
                        continue
                    state = sync_state.get((record_id, f))
                    _save_sync_state(mapping_name, record_id, f, cell_value(record, f), state)

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
