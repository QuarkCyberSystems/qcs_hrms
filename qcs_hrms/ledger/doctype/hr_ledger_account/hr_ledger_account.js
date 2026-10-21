// Copyright (c) 2026, Quark Cyber System FZC and contributors
// For license information, please see license.txt

frappe.ui.form.on("HR Ledger Account", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__("Ledger Entries"), () => {
				frappe.set_route("List", "HR Ledger Entry", { account: frm.doc.name, is_cancelled: 0 });
			});
		}
	},
});
