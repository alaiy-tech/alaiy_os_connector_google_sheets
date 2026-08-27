# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt
"""
Field-picker support for the Google Sheets Mapping form -- lets an admin
select a real doctype field from a dropdown instead of typing its exact
fieldname by hand, and bulk-populate the whole Field Map table in one
click instead of adding a row per field.

Kept separate from google_sheets_mapping.py's own validate() -- that file
enforces correctness at save time; this one only helps build a valid
row set in the first place.
"""

import frappe
from frappe import _

# Framework-level attributes real on every doc but not in meta.fields --
# same set google_sheets_mapping.py's own validation already allows.
_FRAMEWORK_FIELDS = ("name", "owner", "creation", "modified", "modified_by", "docstatus")

# Fieldtypes that can't hold a single Sheet cell value -- offering these in
# the picker would let an admin map something the sync can never actually
# read/write.
_UNSYNCABLE_FIELDTYPES = {
    "Table", "Table MultiSelect", "Section Break", "Column Break", "Tab Break",
    "HTML", "Button", "Fold", "Heading",
}


@frappe.whitelist()
def get_syncable_fields(doctype):
    """Every real field on `doctype` that can be mapped to a Sheet column --
    the same population the picker dropdown and Map All Fields button both
    draw from, so what's offered always matches what save-time validation
    would actually accept."""
    if not doctype or not frappe.db.exists("DocType", doctype):
        frappe.throw(_("Choose a Doctype first."))

    meta = frappe.get_meta(doctype)
    fields = [
        {"fieldname": df.fieldname, "label": df.label or df.fieldname, "fieldtype": df.fieldtype}
        for df in meta.fields
        if df.fieldtype not in _UNSYNCABLE_FIELDTYPES
    ]
    fields.insert(0, {"fieldname": "name", "label": "ID (name)", "fieldtype": "Data"})
    return fields


def _index_to_col_letter(index):
    """0-based column index -> A1-notation letter(s), e.g. 0 -> A, 26 -> AA.
    Mirrors google_sheets/sync.py's own converter -- duplicated rather than
    imported since that module is sync-runtime code and this is a design-
    time helper with no reason to share an import path."""
    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


@frappe.whitelist()
def map_all_fields(source_doctype, id_field=None, id_column=None, skip_id_field=True, start_column=None):
    """Every syncable field on `source_doctype`, each assigned the next
    Sheet column in order starting from `start_column` (or right after
    `id_column` if one is already set). Real fieldnames only -- draws from
    the identical list get_syncable_fields returns, so nothing added here
    could fail google_sheets_mapping.py's own validate().

    Takes the doctype/id-field/id-column as plain arguments rather than a
    saved Mapping name -- this needs to work on a brand-new, not-yet-saved
    Mapping form (the whole point is filling in Fields before the first
    save, not only after), so there is no document to look up yet.

    Returns the row data for the form to apply and let the admin
    review/adjust before saving, same as any other "fill this in for me"
    helper (the admin might want to unmap a field they don't actually
    want Sheet-visible before committing).
    """
    if not source_doctype:
        frappe.throw(_("Choose a Doctype before mapping fields."))

    fields = get_syncable_fields(source_doctype)
    id_field = (id_field or "name").strip()

    if start_column:
        start_index = _col_letter_to_index(start_column)
    elif id_column:
        start_index = _col_letter_to_index(id_column.strip()) + 1
    else:
        start_index = 0

    rows = []
    col_index = start_index
    for f in fields:
        if skip_id_field and f["fieldname"] == id_field:
            # The id field already has its own dedicated ID Column --
            # mapping it again into Fields would trip
            # _validate_id_column_not_reused the moment an admin also fills
            # in ID Column, and there's nothing useful about seeing the same
            # value in two columns.
            continue
        rows.append({
            "doctype_field": f["fieldname"],
            "sheet_column": _index_to_col_letter(col_index),
            "editable_from_sheet": 0,
        })
        col_index += 1

    return {"rows": rows}


def _col_letter_to_index(letters):
    """A1-notation letter(s) -> 0-based column index, e.g. A -> 0, AA -> 26.
    Inverse of _index_to_col_letter above."""
    index = 0
    for char in letters.strip().upper():
        index = index * 26 + (ord(char) - 64)
    return index - 1
