frappe.listview_settings["HR Ledger Entry"] = {
	add_fields: ["sync_status", "is_cancelled"],
	get_indicator(doc) {
		const colors = { Pending: "orange", Synced: "green", Failed: "red", Skipped: "grey" };
		return [__(doc.sync_status), colors[doc.sync_status] || "grey", "sync_status,=," + doc.sync_status];
	},
};
