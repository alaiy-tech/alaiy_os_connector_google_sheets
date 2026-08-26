frappe.ui.form.on("Google Sheets Mapping", {
  refresh(frm) {
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

    frm.add_custom_button(
      __("Run Alaiy OS -> Sheets Sync"),
      () => {
        frappe.call({
          method: "alaiy_os_connector_google_sheets.api.sync.trigger_push_sync",
          callback: () =>
            frappe.show_alert(
              { message: __("Alaiy OS -> Sheets sync queued for every enabled mapping"), indicator: "blue" },
              5,
            ),
        });
      },
      __("Actions"),
    );

    frm.add_custom_button(
      __("Run Sheets -> Alaiy OS Sync"),
      () => {
        frappe.call({
          method: "alaiy_os_connector_google_sheets.api.sync.trigger_pull_sync",
          callback: () =>
            frappe.show_alert(
              { message: __("Sheets -> Alaiy OS sync queued for every enabled mapping"), indicator: "blue" },
              5,
            ),
        });
      },
      __("Actions"),
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
