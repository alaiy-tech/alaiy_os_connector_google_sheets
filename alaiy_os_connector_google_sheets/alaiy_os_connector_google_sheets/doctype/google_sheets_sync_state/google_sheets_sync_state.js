frappe.ui.form.on("Google Sheets Sync State", {
  refresh(frm) {
    if (!frm.doc.conflict_flagged) return;

    frm.dashboard.add_comment(
      __(
        "This field was edited differently in Alaiy OS and in the Sheet since the last sync, so neither " +
          "value was applied. Decide which value should win, update it directly on the record or the " +
          "Sheet cell, then click Resolve below -- the next sync will treat that value as the new baseline.",
      ),
      "orange",
      true,
    );

    frm.add_custom_button(__("Resolve"), () => {
      frappe.confirm(
        __("Mark this conflict resolved? The next sync will pick up whatever value is currently on either side and treat it as correct."),
        () => {
          frappe.call({
            method: "alaiy_os_connector_google_sheets.google_sheets.sync.resolve_conflict",
            args: { sync_state_name: frm.doc.name },
            callback: () => {
              frappe.show_alert({ message: __("Conflict resolved."), indicator: "green" }, 5);
              frm.reload_doc();
            },
          });
        },
      );
    });
  },
});
