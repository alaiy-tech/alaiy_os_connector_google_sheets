frappe.ui.form.on("Google Sheets Connector Settings", {
  refresh(frm) {
    frm.page.set_title(__("Google Sheets Settings"));

    // Mount the shared Alaiy OS connector status card.
    alaiy_os.connector_card.mount(frm, "google_sheets");

    // oauth.py's callback() redirects back here with ?google_oauth=... since
    // that request comes from Google's own redirect, not this form's JS --
    // there's no frappe.call promise to resolve a result into, so it's
    // passed as a query param instead. Show the outcome once, then strip it
    // from the URL so refreshing the page doesn't repeat the toast.
    const params = new URLSearchParams(window.location.search);
    if (params.has("google_oauth")) {
      const ok = params.get("google_oauth") === "success";
      frappe.show_alert(
        {
          message: ok
            ? __("Google account connected.")
            : __("Google connection failed: {0}", [params.get("reason") || "unknown error"]),
          indicator: ok ? "green" : "red",
        },
        ok ? 5 : 8,
      );
      params.delete("google_oauth");
      params.delete("reason");
      const query = params.toString();
      window.history.replaceState({}, "", window.location.pathname + (query ? `?${query}` : ""));
      frm.reload_doc();
    }

    if (frm.doc.gs_connected_email) {
      frm.add_custom_button(
        __("Disconnect Google Account"),
        () => {
          frappe.confirm(
            __("Disconnect {0}? Scheduled syncs will stop until you reconnect.", [
              frm.doc.gs_connected_email,
            ]),
            () => {
              frappe.call({
                method: "alaiy_os_connector_google_sheets.google_sheets.oauth.disconnect",
                callback: () => {
                  frappe.show_alert({ message: __("Disconnected."), indicator: "blue" }, 5);
                  frm.reload_doc();
                },
              });
            },
          );
        },
        __("Actions"),
      );
    } else {
      frm.add_custom_button(
        __("Connect Google Account"),
        () => {
          frappe.call({
            method: "alaiy_os_connector_google_sheets.google_sheets.oauth.get_authorization_url",
            callback(r) {
              const url = (r.message || {}).url;
              if (url) window.location.href = url;
            },
          });
        },
        __("Actions"),
      );
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
      __("Run Sheets -> Alaiy OS Sync"),
      () => {
        frappe.call({
          method: "alaiy_os_connector_google_sheets.api.sync.trigger_pull_sync",
          callback: () =>
            frappe.show_alert(
              { message: __("Sheets -> Alaiy OS sync queued"), indicator: "blue" },
              5,
            ),
        });
      },
      __("Actions"),
    );

    frm.add_custom_button(
      __("Run Alaiy OS -> Sheets Sync"),
      () => {
        frappe.call({
          method: "alaiy_os_connector_google_sheets.api.sync.trigger_push_sync",
          callback: () =>
            frappe.show_alert(
              { message: __("Alaiy OS -> Sheets sync queued"), indicator: "blue" },
              5,
            ),
        });
      },
      __("Actions"),
    );
  },
});
