frappe.listview_settings["HR Payment"] = {
	add_fields: ["payment_type", "status"],
	get_indicator(doc) {
		if (doc.docstatus === 2) return [__("Cancelled"), "red", "docstatus,=,2"];
		if (doc.docstatus === 0) return [__("Draft"), "grey", "docstatus,=,0"];
		return [__(doc.payment_type), doc.payment_type === "Pay" ? "blue" : "green", "payment_type,=," + doc.payment_type];
	},
};
