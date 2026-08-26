frappe.ui.form.on("Google Sheets Connector Settings", {
  refresh(frm) {
    frm.page.set_title(__("Google Sheets Settings"));

    // Mount the shared Alaiy OS connector status card.
    alaiy_os.connector_card.mount(frm, "google_sheets");

    // This page is step 1 of 2 (connect the account here, configure what
    // syncs on Google Sheets Mapping) -- state that explicitly rather than
    // relying on the admin to discover the Mapping doctype on their own.
    frm.dashboard.add_comment(
      __(
        "This connects your Google account so Alaiy OS can read and write a Google Sheet. " +
          "Once connected, go to <a href='/app/google-sheets-mapping'>Google Sheets Mapping</a> " +
          "to choose what data syncs and to which Sheet.",
      ),
      "blue",
      true,
    );

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

    // Deliberately NOT tucked into the "Actions" dropdown -- this is the
    // single most important action on this whole page (nothing else here
    // works until an account is connected), so it renders as its own
    // standalone, primary button in the page toolbar where it can't be
    // missed.
    if (frm.doc.gs_connected_email) {
      frm.add_custom_button(__("Disconnect Google Account"), () => {
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
      });
    } else {
      frm.page.set_primary_action(__("Connect Google Account"), () => {
        frappe.call({
          method: "alaiy_os_connector_google_sheets.google_sheets.oauth.get_authorization_url",
          callback(r) {
            const url = (r.message || {}).url;
            if (url) window.location.href = url;
          },
        });
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
  },
});
