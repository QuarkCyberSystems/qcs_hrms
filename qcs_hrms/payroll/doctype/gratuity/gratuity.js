// Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Gratuity", {
	setup: function (frm) {
		frm.set_query("salary_component", function () {
			return {
				filters: {
					type: "Earning",
				},
			};
		});

		frm.set_query("expense_account", function () {
			return {
				filters: {
					account_type: ["in", ["Expense", "Liability"]],
					disabled: 0,
					company: frm.doc.company,
				},
			};
		});

		frm.set_query("payable_account", function () {
			return {
				filters: {
					account_type: ["in", ["Payable", "Liability"]],
					disabled: 0,
					company: frm.doc.company,
				},
			};
		});
	},
	refresh: function (frm) {
		if (frm.doc.docstatus == 1 && !frm.doc.pay_via_salary_slip && frm.doc.status == "Unpaid") {
			frm.add_custom_button(__("Create Payment"), function () {
				return frappe.call({
					method: "qcs_hrms.payroll.doctype.gratuity.gratuity.make_payment",
					args: {
						dt: frm.doc.doctype,
						dn: frm.doc.name,
					},
					callback: function (r) {
						var doclist = frappe.model.sync(r.message);
						frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
					},
				});
			});
		}
	},

	employee: function (frm) {
		frm.events.calculate_work_experience_and_amount(frm);
	},

	gratuity_rule: function (frm) {
		frm.events.calculate_work_experience_and_amount(frm);
	},

	calculate_work_experience_and_amount: function (frm) {
		if (frm.doc.employee && frm.doc.gratuity_rule) {
			frm.call("calculate_work_experience_and_amount").then((r) => {
				frm.set_value("current_work_experience", r.message["current_work_experience"]);
				frm.set_value("amount", r.message["amount"]);
			});
		}
	},
});
