// Copyright (c) 2026, Quark Cyber System FZC and contributors
// For license information, please see license.txt

frappe.ui.form.on("HR Payment", {
	setup(frm) {
		["paid_from", "paid_to"].forEach((field) => {
			frm.set_query(field, () => ({ filters: { company: frm.doc.company, disabled: 0 } }));
		});
		frm.set_query("party_type", () => ({ filters: { name: ["in", ["Employee"]] } }));
		frm.set_query("reference_doctype", "references", () => ({
			filters: { name: ["in", frm.events.get_reference_doctypes()] },
		}));
		frm.set_query("reference_name", "references", () => {
			const filters = { docstatus: 1, company: frm.doc.company };
			if (frm.doc.party_type === "Employee" && frm.doc.party) filters.employee = frm.doc.party;
			return { filters };
		});
	},

	get_reference_doctypes() {
		return [
			"Expense Claim",
			"Employee Advance",
			"Leave Encashment",
			"Full and Final Statement",
			"Gratuity",
			"Payroll Entry",
			"Salary Withholding",
		];
	},

	refresh(frm) {
		if (frm.doc.docstatus > 0) {
			frm.add_custom_button(__("Ledger Entries"), () => {
				frappe.set_route("List", "HR Ledger Entry", {
					voucher_type: frm.doc.doctype,
					voucher_no: frm.doc.name,
				});
			});
		}
	},

	company(frm) {
		if (!frm.doc.company) return;
		frappe.db.get_value("Company", frm.doc.company, "default_currency").then((r) => {
			if (r.message && !frm.doc.currency) frm.set_value("currency", r.message.default_currency);
		});
	},

	payment_type(frm) {
		const from = frm.doc.paid_from;
		frm.set_value("paid_from", frm.doc.paid_to);
		frm.set_value("paid_to", from);
	},

	paid_amount(frm) {
		frm.events.set_totals(frm);
	},

	set_totals(frm) {
		const allocated = (frm.doc.references || []).reduce((t, r) => t + flt(r.allocated_amount), 0);
		frm.set_value("total_allocated_amount", allocated);
		frm.set_value("unallocated_amount", flt(frm.doc.paid_amount) - allocated);
		frm.set_value("base_paid_amount", flt(frm.doc.paid_amount) * flt(frm.doc.exchange_rate || 1));
	},
});

frappe.ui.form.on("HR Payment Reference", {
	reference_name(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!(row.reference_doctype && row.reference_name)) return;
		frappe
			.call({
				method: "qcs_hrms.ledger.doctype.hr_payment.hr_payment.get_reference_outstanding",
				args: {
					reference_doctype: row.reference_doctype,
					reference_name: row.reference_name,
					payment_type: frm.doc.payment_type,
				},
			})
			.then((r) => {
				const d = r.message || {};
				frappe.model.set_value(cdt, cdn, "outstanding_amount", d.outstanding_amount);
				frappe.model.set_value(cdt, cdn, "allocated_amount", d.outstanding_amount);
				if (!frm.doc.party && d.party) {
					frm.set_value("party_type", d.party_type);
					frm.set_value("party", d.party);
				}
				const party_field = frm.doc.payment_type === "Pay" ? "paid_to" : "paid_from";
				if (!frm.doc[party_field] && d.account) frm.set_value(party_field, d.account);
			});
	},
	allocated_amount(frm) {
		frm.events.set_totals(frm);
	},
	references_remove(frm) {
		frm.events.set_totals(frm);
	},
});
