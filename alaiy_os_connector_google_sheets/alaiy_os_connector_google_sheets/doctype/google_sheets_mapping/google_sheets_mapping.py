# Copyright (c) 2026, Alaiy and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GoogleSheetsMapping(Document):
	def validate(self):
		self._validate_source_doctype_fields()
		self._validate_id_field_is_mapped()
		if self.is_enabled:
			self._validate_sheet_reachable()

	def _validate_source_doctype_fields(self):
		"""Every mapped fieldname must be a real field on the source
		doctype -- catches a typo at save time instead of failing silently
		mid-sync (or worse, KeyError-ing a whole sync run over one bad row)."""
		if not self.source_doctype:
			return
		meta = frappe.get_meta(self.source_doctype)
		valid_fieldnames = {df.fieldname for df in meta.fields}
		# name/owner/creation/modified etc. are real, readable attributes on
		# every doc but aren't in meta.fields (they're framework-level, not
		# doctype-defined) -- allow them explicitly rather than rejecting
		# the single most common id_field value ("name") as invalid.
		valid_fieldnames |= {"name", "owner", "creation", "modified", "modified_by", "docstatus"}

		for row in self.field_map:
			if row.doctype_field not in valid_fieldnames:
				frappe.throw(
					_("Row #{0}: {1} has no field {2}.").format(
						row.idx, self.source_doctype, frappe.bold(row.doctype_field)
					)
				)

	def _validate_id_field_is_mapped(self):
		"""id_field doesn't have to be listed as a Sheet-visible column
		(it's an internal matching key, not necessarily something a Sheet
		user should see or edit) -- but it does have to be a real field,
		same check as above, run separately since it lives outside
		field_map."""
		if not self.source_doctype or not self.id_field:
			return
		meta = frappe.get_meta(self.source_doctype)
		valid_fieldnames = {df.fieldname for df in meta.fields} | {"name"}
		if self.id_field not in valid_fieldnames:
			frappe.throw(
				_("Unique ID Field: {0} has no field {1}.").format(
					self.source_doctype, frappe.bold(self.id_field)
				)
			)

	def _validate_sheet_reachable(self):
		"""Confirms the Spreadsheet ID/Sheet Tab actually exist and the
		connected Google account can reach them -- catches a copy-pasted
		wrong ID or a typo'd tab name at save time, not on the first real
		sync run. Only runs when enabling the mapping (not on every save of
		a disabled draft), since it needs a live API call."""
		from alaiy_os_connector_google_sheets.google_sheets.client import GoogleSheetsClient

		try:
			client = GoogleSheetsClient()
			metadata = client.get_spreadsheet_metadata(self.spreadsheet_id)
		except Exception as e:
			frappe.throw(
				_("Could not reach spreadsheet {0}: {1}").format(
					frappe.bold(self.spreadsheet_id), str(e)[:200]
				)
			)

		if self.sheet_tab not in metadata["tabs"]:
			frappe.throw(
				_('Sheet Tab "{0}" not found in "{1}". Available tabs: {2}').format(
					self.sheet_tab, metadata["title"], ", ".join(metadata["tabs"]) or "(none)"
				)
			)
