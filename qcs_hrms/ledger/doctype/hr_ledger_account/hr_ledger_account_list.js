frappe.listview_settings["HR Ledger Account"] = {
	add_fields: ["account_type", "disabled"],
	get_indicator(doc) {
		if (doc.disabled) return [__("Disabled"), "grey", "disabled,=,1"];
		return [__(doc.account_type || "Account"), "blue", "account_type,=," + (doc.account_type || "")];
	},
};
