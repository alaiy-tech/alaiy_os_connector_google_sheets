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
 * handler -- same reason as add_map_all_fields_button below: picking a
 * Link field's value is its own event, not a full form refresh, so this
 * needs its own explicit call on that path too. Confirmed live: typing
 * directly into a Fields row (instead of using Map All Fields) showed no
 * dropdown options at all until the page was reloaded after choosing a
 * Doctype, for the exact same reason the button didn't appear. */
function wire_doctype_field_autocomplete(frm) {
  if (!frm.doc.source_doctype) return;
  const grid = frm.fields_dict.field_map.grid;
  grid.update_docfield_property("doctype_field", "fieldtype", "Autocomplete");

  const get_data = () => {
    // Autocomplete's get_data must return synchronously -- the fields
    // list is fetched once (see fetchSyncableFields' cache above) and
    // reused here; on a genuinely first-ever open before that fetch
    // lands, this returns nothing for one keystroke rather than
    // blocking the input, which resolves itself the moment the fetch
    // finishes and the admin types again.
    return (_syncableFieldsForDoctype === frm.doc.source_doctype ? _syncableFieldsCache : []).map(
      (f) => ({ value: f.fieldname, label: `${f.label} (${f.fieldname})`, description: f.fieldtype }),
    );
  };

  // Two separate rendering paths in Frappe's grid, each reading get_data
  // off a different object -- confirmed against the real framework source
  // (frappe/public/js/frappe/form/grid_row.js): the row-edit dialog (what
  // the pencil icon opens) uses the child column's own docfield, via
  // grid.get_field(). The INLINE cell in the grid table itself instead
  // checks this.grid.df.get_data -- the Table field's own docfield, a
  // completely different object. Setting only the first (what get_field
  // returns) is why suggestions worked from the pencil dialog but typing
  // straight into the grid cell showed nothing at all.
  grid.get_field("doctype_field").get_data = get_data;
  const table_docfield = grid.df;
  if (table_docfield) table_docfield.get_data = get_data;

  fetchSyncableFields(frm.doc.source_doctype);
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
