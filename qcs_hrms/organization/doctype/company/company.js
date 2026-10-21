// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// Copyright (c) 2026, Quark Cyber System FZC
// License: GNU General Public License v3. See license.txt

const HR_LEDGER_ACCOUNT_FILTERS = {
	default_payroll_payable_account: { account_type: "Payable" },
	default_expense_claim_payable_account: { account_type: "Payable" },
	default_employee_advance_account: { account_type: ["in", ["Receivable", "Asset"]] },
	default_bank_account: { account_type: "Bank" },
	default_cash_account: { account_type: "Cash" },
	default_salary_expense_account: { account_type: "Expense" },
	round_off_account: { account_type: ["in", ["Round Off", "Expense", "Income"]] },
	exchange_gain_loss_account: { account_type: ["in", ["Expense", "Income"]] },
};

frappe.ui.form.on("Company", {
	setup: function (frm) {
		frm.__rename_queue = "long";

		frm.set_query("parent_company", function () {
			return {
				filters: { is_group: 1 },
			};
		});

		Object.entries(HR_LEDGER_ACCOUNT_FILTERS).forEach(([fieldname, filters]) => {
			frm.set_query(fieldname, function (doc) {
				return { filters: { company: doc.name, disabled: 0, ...filters } };
			});
		});
	},

	onload: function (frm) {
		if (frm.doc.__islocal && frm.doc.parent_company) {
			frappe.db.get_value("Company", frm.doc.parent_company, "is_group", (r) => {
				if (!r.is_group) {
					frm.set_value("parent_company", "");
				}
			});
		}
	},

	company_name: function (frm) {
		if (frm.doc.__islocal) {
			let parts = (frm.doc.company_name || "").split(" ");
			let abbr = $.map(parts, function (p) {
				return p ? p.substr(0, 1) : null;
			}).join("");
			frm.set_value("abbr", abbr);
		}
	},

	date_of_incorporation: function (frm) {
		if (
			frm.doc.date_of_establishment &&
			frm.doc.date_of_incorporation &&
			frm.doc.date_of_incorporation < frm.doc.date_of_establishment
		) {
			frappe.msgprint(__("Date of Incorporation is before the Date of Establishment"));
		}
	},

	refresh: function (frm) {
		frm.toggle_display("address_html", !frm.is_new());

		if (frm.is_new()) return;

		frappe.contacts.render_address_and_contact(frm);

		frm.add_custom_button(
			__("Employees"),
			() => frappe.set_route("List", "Employee", { company: frm.doc.name }),
			__("View"),
		);
		frm.add_custom_button(
			__("Departments"),
			() => frappe.set_route("Tree", "Department", { company: frm.doc.name }),
			__("View"),
		);
		frm.add_custom_button(
			__("HR Ledger Accounts"),
			() => frappe.set_route("List", "HR Ledger Account", { company: frm.doc.name }),
			__("View"),
		);
	},
});
