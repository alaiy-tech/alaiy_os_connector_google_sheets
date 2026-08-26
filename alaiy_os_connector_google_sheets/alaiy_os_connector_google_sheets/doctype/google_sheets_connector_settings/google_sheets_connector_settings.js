frappe.ui.form.on("Google Sheets Connector Settings", {
  refresh(frm) {
    frm.page.set_title(__("Google Sheets Settings"));

    // Mount the shared Alaiy OS connector status card + password reveal.
    alaiy_os.connector_card.mount(frm, "google_sheets");
    alaiy_os.connector_card.setup_password_reveal(
      frm,
      "gs_api_token",
      "google_sheets",
    );

    // Auto-fill Company with the site default if empty.
    if (!frm.doc.gs_company) {
      frappe.db
        .get_single_value("Global Defaults", "default_company")
        .then((company) => {
          if (company) frm.set_value("gs_company", company);
        });
    }

    frm.add_custom_button(
      __("Test Connection"),
      () => {
        frappe.call({
          // Go through the registry wrapper (not test_connection directly)
          // so a successful test also flips the "Connector Status" card at
          // the top of this form from "Not configured" to "Connected".
          method: "alaiy_os.api.connectors.test_connector",
          args: { connector_id: "google_sheets" },
          callback(r) {
            const res = r.message || {};
            frappe.show_alert(
              {
                message:
                  res.message ||
                  (res.success ? __("Connected") : __("Connection failed")),
                indicator: res.success ? "green" : "red",
              },
              res.success ? 5 : 7,
            );
            frm.reload_doc();
          },
        });
      },
      __("Actions"),
    );

    frm.add_custom_button(
      __("Run Sheets -> Frappe Sync"),
      () => {
        frappe.call({
          method: "alaiy_os_connector_google_sheets.api.sync.trigger_pull_sync",
          callback: () =>
            frappe.show_alert(
              { message: __("Sheets -> Frappe sync queued"), indicator: "blue" },
              5,
            ),
        });
      },
      __("Actions"),
    );

    frm.add_custom_button(
      __("Run Frappe -> Sheets Sync"),
      () => {
        frappe.call({
          method: "alaiy_os_connector_google_sheets.api.sync.trigger_push_sync",
          callback: () =>
            frappe.show_alert(
              { message: __("Frappe -> Sheets sync queued"), indicator: "blue" },
              5,
            ),
        });
      },
      __("Actions"),
    );
  },
});
