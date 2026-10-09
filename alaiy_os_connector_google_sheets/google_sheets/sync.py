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
        # Logged and recorded on the Sync Log; the caller keeps going so one
        # failing mapping does not stop the mappings after it.


def _require_mappable_fields(mapping, fieldnames):
    """Fail the run with the mapping and the field names instead of a raw
    database error. A mapping saved before a field was removed, or on a
    site where the field has no column, would otherwise stop at the first
    query with an unreadable 'Unknown column' message."""
    from alaiy_os_connector_google_sheets.api.mapping import mappable_fieldnames

    allowed = mappable_fieldnames(mapping.source_doctype)
    missing = [f for f in dict.fromkeys(fieldnames) if f not in allowed]
    if missing:
        frappe.throw(
            f"Mapping {mapping.name}: {mapping.source_doctype} has no database field "
            f"{', '.join(missing)}. Remove it from the mapping."
        )


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


_HTML_TYPES = ("Text Editor", "HTML Editor", "HTML")
_NUMERIC_TYPES = ("Int", "Long Int", "Float", "Currency", "Percent")
_TRUE_TEXT = ("1", "true", "yes", "y")


def _norm(value, df):
    """Canonical text of one value. Frappe values and Sheet cells both go
    through this, so a baseline written by one direction compares equal to
    the other direction's reading of the same value (HTML stripped, 10.0
    and "10" alike, True and "TRUE" alike)."""
    if value is None:
        return ""
    fieldtype = df.fieldtype if df else ""
    if fieldtype in _HTML_TYPES:
        return frappe.utils.strip_html(str(value)).strip()
    if fieldtype == "Check":
        return "1" if str(value).strip().lower() in _TRUE_TEXT else "0"
    if fieldtype in _NUMERIC_TYPES:
        text = str(value).strip().replace(",", "")
        if not text:
            return ""
        try:
            number = float(text)
        except ValueError:
            return text
        return str(int(number)) if number.is_integer() else repr(number)
    return str(value)


def _coerce(text, df):
    """Sheet text back to a value the field accepts: an empty cell on a
    typed field is None, not an empty string a Link/Date would reject."""
    fieldtype = df.fieldtype if df else ""
    if fieldtype == "Check":
        return 1 if text == "1" else 0
    if fieldtype in _NUMERIC_TYPES:
        if text == "":
            return None
        number = float(text)
        return int(number) if fieldtype in ("Int", "Long Int") else number
    if text == "" and fieldtype not in ("Data", "Small Text", "Text", "Long Text", "Text Editor", "Code", "Select"):
        return None
    return text


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
            _require_mappable_fields(mapping, fields + [mapping.id_field])
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
            meta = frappe.get_meta(mapping.source_doctype)

            processed, updated, failed, skipped, conflicts = 0, 0, 0, 0, 0
            for sheet_row in sheet_rows:
                processed += 1
                record_id = sheet_row[id_col_offset].strip() if id_col_offset < len(sheet_row) else ""
                docname = frappe.db.get_value(mapping.source_doctype, {mapping.id_field: record_id}, "name") if record_id else None
                if not docname:
                    skipped += 1
                    continue

                try:
                    doc = frappe.get_doc(mapping.source_doctype, docname)
                    changed = False
                    for fieldname, offset in field_col_offsets:
                        df = meta.get_field(fieldname)
                        sheet_value = _norm(sheet_row[offset] if offset < len(sheet_row) else "", df)
                        frappe_value = _norm(doc.get(fieldname), df)
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
                        doc.set(fieldname, _coerce(sheet_value, df))
                        changed = True
                        _save_sync_state(mapping_name, record_id, fieldname, sheet_value, state)

                    if changed:
                        doc.save()
                        updated += 1
                    # One row, one transaction: a later row that fails must not
                    # undo this row's save or its sync-state baselines.
                    frappe.db.commit()
                except Exception:
                    failed += 1
                    frappe.log_error(
                        title=f"Google Sheets connector: pull sync row failed ({mapping_name})",
                        message=f"record_id={record_id}\n{frappe.get_traceback()}",
                    )
                    frappe.db.rollback()

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
            _require_mappable_fields(mapping, fields)

            meta = frappe.get_meta(mapping.source_doctype)
            id_field = mapping.id_field
            editable = {row.doctype_field for row in mapping.field_map if row.editable_from_sheet}
            records = frappe.get_all(
                mapping.source_doctype, fields=list(dict.fromkeys(fields)), order_by="creation asc"
            )
            sync_state = _load_sync_state(mapping_name)
            conflicted_fields = {
                (key[0], key[1]) for key, row in sync_state.items() if row.conflict_flagged
            }

            def cell_value(record, fieldname):
                return _norm(record.get(fieldname), meta.get_field(fieldname))

            client = GoogleSheetsClient()
            start_row = mapping.header_row + 1
            min_col = min(_col_letter_to_index(c) for c in columns)
            max_col = max(_col_letter_to_index(c) for c in columns)
            width = max_col - min_col + 1
            start_col = _index_to_col_letter(min_col)
            end_col = _index_to_col_letter(max_col)
            col_offsets = [_col_letter_to_index(c) - min_col for c in columns]
            id_offset = _col_letter_to_index(mapping.id_column) - min_col

            # Write the header row (real field labels, not raw fieldnames),
            # only when it differs from what is already there.
            field_labels = {df.fieldname: (df.label or df.fieldname) for df in meta.fields}
            field_labels[id_field] = field_labels.get(id_field) or id_field
            header_cells = [""] * width
            for f, offset in zip(fields, col_offsets):
                header_cells[offset] = field_labels.get(f, f)
            header_range = (
                f"{mapping.sheet_tab}!{start_col}{mapping.header_row}:{end_col}{mapping.header_row}"
            )
            current_header = client.get_values(mapping.spreadsheet_id, header_range)
            current_header_row = (current_header[0] if current_header else []) + [""] * width
            if current_header_row[:width] != header_cells:
                client.update_values(mapping.spreadsheet_id, header_range, [header_cells])

            # What the Sheet holds right now. Rows keep the position they
            # already have (a record is found by its ID column, never by row
            # number), so a push never reorders the Sheet under someone who
            # is editing it; a new record is appended at the end, a deleted
            # record has only its mapped cells cleared.
            current = client.get_values(
                mapping.spreadsheet_id, f"{mapping.sheet_tab}!{start_col}{start_row}:{end_col}"
            )
            current_rows = [(row + [""] * width)[:width] for row in current]
            existing_row = {}
            for index, row in enumerate(current_rows):
                rid = row[id_offset].strip()
                if rid and rid not in existing_row:
                    existing_row[rid] = index
            by_id = {str(record.get(id_field) or ""): record for record in records}

            block = [list(row) for row in current_rows]
            # Cells left alone because the Sheet holds an edit that pull has
            # not read yet; their baseline must stay as it is.
            kept_sheet_edit = set()

            def fill(cells, rid, record):
                for f, offset in zip(fields, col_offsets):
                    if (rid, f) in conflicted_fields:
                        continue
                    value = cell_value(record, f)
                    if f in editable and f != id_field and rid in existing_row:
                        state = sync_state.get((rid, f))
                        baseline = (state.last_synced_value or None) if state else None
                        sheet_value = _norm(current_rows[existing_row[rid]][offset], meta.get_field(f))
                        if baseline is not None and sheet_value != baseline and sheet_value != value:
                            kept_sheet_edit.add((rid, f))
                            continue
                    cells[offset] = value

            for index, row in enumerate(current_rows):
                rid = row[id_offset].strip()
                if rid in by_id:
                    fill(block[index], rid, by_id[rid])
                elif rid:
                    for offset in col_offsets:
                        block[index][offset] = ""
            for rid, record in by_id.items():
                if rid not in existing_row:
                    cells = [""] * width
                    fill(cells, rid, record)
                    block.append(cells)

            rows_written = 0
            if block and block != current_rows:
                end_row = start_row + len(block) - 1
                client.update_values(
                    mapping.spreadsheet_id, f"{mapping.sheet_tab}!{start_col}{start_row}:{end_col}{end_row}", block
                )
                rows_written = sum(1 for i, row in enumerate(block) if i >= len(current_rows) or row != current_rows[i])

            # What was written now matches the Sheet, so it becomes the new
            # agreed baseline. Cells left alone (conflicted, or holding an
            # unread Sheet edit) keep theirs, and an unchanged baseline is
            # not written again.
            for rid, record in by_id.items():
                for f in fields:
                    if f == id_field or (rid, f) in conflicted_fields or (rid, f) in kept_sheet_edit:
                        continue
                    value = cell_value(record, f)
                    state = sync_state.get((rid, f))
                    if state is None or state.last_synced_value != value:
                        _save_sync_state(mapping_name, rid, f, value, state)

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
