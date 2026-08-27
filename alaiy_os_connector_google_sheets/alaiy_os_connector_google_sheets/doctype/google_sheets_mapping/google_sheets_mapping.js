// {fieldname, label, fieldtype}[] for the currently-chosen source_doctype --
// cached per doctype so re-opening the grid or adding another row doesn't
// re-fetch on every keystroke/row-add. Cleared whenever source_doctype
// changes (see the source_doctype handler below).
let _syncableFieldsCache = null;
let _syncableFieldsForDoctype = null;

function fetchSyncableFields(doctype) {
  if (_syncableFieldsForDoctype === doctype && _syncableFieldsCache) {
    return Promise.resolve(_syncableFieldsCache);
  }
  return frappe.call({
    method: "alaiy_os_connector_google_sheets.api.mapping.get_syncable_fields",
    args: { doctype },
  }).then((r) => {
    _syncableFieldsCache = r.message || [];
    _syncableFieldsForDoctype = doctype;
    return _syncableFieldsCache;
  });
}

/** Called from both refresh (page load) and the source_doctype field
 * handler (picking a value after the form is already open, which does
 * NOT trigger a full refresh) -- a Link field change is its own event,
 * not a form refresh, so the button needs its own explicit add on that
 * path too. remove_custom_button first since this can run more than
 * once per page view (switching Doctype twice) and add_custom_button
 * has no built-in de-dup -- without it, picking a different doctype
 * would stack a second "Map All Fields" button rather than replacing
 * the first. */
function add_map_all_fields_button(frm) {
  frm.remove_custom_button(__("Map All Fields"), __("Fields"));
  if (!frm.doc.source_doctype) return;

  frm.add_custom_button(
    __("Map All Fields"),
    () => {
      frappe.confirm(
        __(
          "This replaces the current Fields table with every field on {0}, each assigned the next Sheet column in order. Review and remove any you don't want Sheet-visible before saving.",
          [frappe.utils.escape_html(frm.doc.source_doctype)],
        ),
        () => {
          frappe.call({
            method: "alaiy_os_connector_google_sheets.api.mapping.map_all_fields",
            args: {
              source_doctype: frm.doc.source_doctype,
              id_field: frm.doc.id_field,
              id_column: frm.doc.id_column,
            },
            callback: (r) => {
              const rows = (r.message || {}).rows || [];
              frm.clear_table("field_map");
              rows.forEach((row) => {
                const child = frm.add_child("field_map");
                Object.assign(child, row);
              });
              frm.refresh_field("field_map");
              frappe.show_alert(
                { message: __("Mapped {0} field(s). Save to apply.", [rows.length]), indicator: "green" },
                5,
              );
            },
          });
        },
      );
    },
    __("Fields"),
  );
}

/** Called from both refresh (page load) and the source_doctype field
 * handler -- picking a Link field's value is its own event, not a full
 * form refresh, so this needs its own explicit call on that path too.
 *
 * Confirmed against Frappe's real ControlAutocomplete source
 * (frappe/public/js/frappe/form/controls/autocomplete.js): two earlier
 * attempts set a get_data() FUNCTION on the docfield, which is not how
 * this control reads its options at all -- get_data() is a method on the
 * control INSTANCE that returns its own internal _data, populated by
 * set_data(), which itself only ever gets called from set_options() off
 * df.options (a plain array) or from a df.get_query round trip. Setting
 * an unused get_data property on the docfield did nothing in either the
 * inline grid cell or the row dialog -- confirmed live, no suggestions
 * appeared in either, and free text saved with zero validation feedback.
 * The real, working mechanism is df.options -- a plain {label,value}[]
 * array, read once when the control is created. */
function wire_doctype_field_autocomplete(frm) {
  const grid = frm.fields_dict.field_map.grid;
  if (!frm.doc.source_doctype) {
    // No doctype chosen yet means there is no real field list to offer or
    // validate against -- free text typed here (confirmed live) is
    // guaranteed garbage, since the very next server-side save would
    // reject it anyway (google_sheets_mapping.py's
    // _validate_source_doctype_fields requires source_doctype to even
    // check a value). Lock the column outright rather than let it sit
    // open with nothing behind it.
    grid.update_docfield_property("doctype_field", "options", []);
    grid.update_docfield_property("doctype_field", "read_only", 1);
    return;
  }

  grid.update_docfield_property("doctype_field", "read_only", 0);
  fetchSyncableFields(frm.doc.source_doctype).then((fields) => {
    const options = fields.map((f) => ({
      value: f.fieldname,
      label: `${f.label} (${f.fieldname})`,
      description: f.fieldtype,
    }));
    grid.update_docfield_property("doctype_field", "fieldtype", "Autocomplete");
    grid.update_docfield_property("doctype_field", "options", options);
    // update_docfield_property only touches the stored docfield -- any
    // row/control already rendered before this fetch resolved is still
    // showing the OLD (or empty) options. refresh_field re-renders every
    // row's control from the now-updated docfield so a row added before
    // the fetch landed doesn't stay stuck with nothing.
    frm.refresh_field("field_map");
  });
}

frappe.ui.form.on("Google Sheets Mapping", {
  source_doctype(frm) {
    // Stale options for the old doctype must not linger in the grid --
    // an admin switching source_doctype after already picking fields would
    // otherwise still see the previous doctype's fieldnames on offer.
    _syncableFieldsCache = null;
    _syncableFieldsForDoctype = null;
    wire_doctype_field_autocomplete(frm);
    add_map_all_fields_button(frm);
  },

  refresh(frm) {
    wire_doctype_field_autocomplete(frm);
    add_map_all_fields_button(frm);

    // A mapping is useless without a connected Google account -- check and
    // show it right here instead of making the admin guess or go check
    // Google Sheets Connector Settings separately.
    frappe.call({
      method: "alaiy_os_connector_google_sheets.google_sheets.oauth.get_connection_status",
      callback(r) {
        const status = r.message || {};
        frm.dashboard.clear_headline();
        if (status.connected) {
          frm.dashboard.set_headline_alert(
            `<span class="indicator-pill green">${__("Google account connected")}: ${frappe.utils.escape_html(status.email)}</span>`,
          );
        } else {
          frm.dashboard.set_headline_alert(
            `<span class="indicator-pill red">${__("No Google account connected")} -- <a href="/app/google-sheets-connector-settings">${__("connect it first")}</a></span>`,
          );
        }
      },
    });

    if (frm.doc.__islocal) return; // buttons below need a saved mapping

    // Both buttons run for every enabled mapping, not just this one --
    // stated in the toast rather than implied by being on this form.
    frm.add_custom_button(
      __("Sync Now: Alaiy OS -> Sheet"),
      () => {
        frappe.call({
          method: "alaiy_os_connector_google_sheets.api.sync.trigger_push_sync",
          callback: () =>
            frappe.show_alert(
              {
                message: __("Refreshing every enabled mapping's Sheet with the latest Alaiy OS data…"),
                indicator: "blue",
              },
              5,
            ),
        });
      },
      __("Sync Now"),
    );

    frm.add_custom_button(
      __("Sync Now: Sheet Edits -> Alaiy OS"),
      () => {
        frappe.call({
          method: "alaiy_os_connector_google_sheets.api.sync.trigger_pull_sync",
          callback: () =>
            frappe.show_alert(
              {
                message: __("Checking every enabled mapping for Sheet edits to bring in…"),
                indicator: "blue",
              },
              5,
            ),
        });
      },
      __("Sync Now"),
    );

    frm.add_custom_button(
      __("View Sync Log"),
      () => {
        frappe.set_route("list", "Google Sheets Sync Log", { mapping: frm.doc.name });
      },
      __("Actions"),
    );

    frappe.db
      .count("Google Sheets Sync State", { filters: { mapping: frm.doc.name, conflict_flagged: 1 } })
      .then((count) => {
        if (!count) return;
        frm.dashboard.add_indicator(
          __("{0} unresolved conflict{1}", [count, count === 1 ? "" : "s"]),
          "red",
        );
        frm.add_custom_button(
          __("View Conflicts ({0})", [count]),
          () => {
            frappe.set_route("list", "Google Sheets Sync State", {
              mapping: frm.doc.name,
              conflict_flagged: 1,
            });
          },
          __("Actions"),
        );
      });
  },
});
