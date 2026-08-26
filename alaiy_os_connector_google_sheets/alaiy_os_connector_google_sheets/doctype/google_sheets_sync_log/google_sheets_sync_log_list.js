const GS_SYNC_TYPE_COLORS = {
	pull: "blue",
	push: "orange",
	webhook: "cyan",
};

// "pull"/"push" are this connector's internal names for the two sync
// directions -- shown everywhere else in the app as the direction itself
// (Sheet -> Alaiy OS / Alaiy OS -> Sheet), so the log list matches instead
// of exposing the internal vocabulary.
const GS_SYNC_TYPE_LABELS = {
	pull: "Sheet -> Alaiy OS",
	push: "Alaiy OS -> Sheet",
	webhook: "Webhook",
};

const GS_TRIGGER_COLORS = {
	scheduled: "purple",
	manual: "pink",
	webhook: "cyan",
};

const GS_STATUS_COLORS = {
	queued: "grey",
	running: "blue",
	success: "green",
	failed: "red",
	skipped: "yellow",
};

function alaiy_pill(value, colors, labels) {
	if (!value) return "";
	const color = colors[value] || "darkgrey";
	const label = (labels && labels[value]) || value;
	return `<span class="indicator-pill ${color} filterable" data-filter="=,${value}">
		<span>${frappe.utils.escape_html(label)}</span>
	</span>`;
}

frappe.listview_settings["Google Sheets Sync Log"] = {
	get_indicator(doc) {
		return [
			__(doc.status),
			GS_STATUS_COLORS[doc.status] || "darkgrey",
			`status,=,${doc.status}`,
		];
	},
	formatters: {
		sync_type: (value) => alaiy_pill(value, GS_SYNC_TYPE_COLORS, GS_SYNC_TYPE_LABELS),
		trigger: (value) => alaiy_pill(value, GS_TRIGGER_COLORS),
		status: (value) => alaiy_pill(value, GS_STATUS_COLORS),
	},
};
