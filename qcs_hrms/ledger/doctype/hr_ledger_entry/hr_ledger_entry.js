// Copyright (c) 2026, Quark Cyber System FZC and contributors
// For license information, please see license.txt

frappe.ui.form.on("HR Ledger Entry", {
	refresh(frm) {
		frm.disable_save();
	},
});
