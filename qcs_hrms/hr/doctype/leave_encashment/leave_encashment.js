// Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Leave Encashment", {
	onload: function (frm) {
		// Ignore cancellation of doctype on cancel all.
		frm.ignore_doctypes_on_cancel_all = ["Leave Ledger Entry"];
	},
	setup: function (frm) {
		frm.set_query("leave_type", function () {
			return {
				filters: {
					allow_encashment: 1,
				},
			};
		});
		frm.set_query("leave_period", function () {
			return {
				filters: {
					is_active: 1,
				},
			};
		});

		frm.set_query("payable_account", function () {
			if (!frm.doc.employee) {
				frappe.msgprint(__("Please select employee first"));
			}
			let company_currency = qcs_hrms.get_currency(frm.doc.company);
			let currencies = [company_currency];
			if (frm.doc.currency && frm.doc.currency != company_currency) {
				currencies.push(frm.doc.currency);
			}

			return {
				filters: {
					company: frm.doc.company,
					account_currency: ["in", currencies],
					account_type: "Payable",
					disabled: 0,
				},
			};
		});

		frm.set_query("expense_account", function () {
			return {
				filters: {
					company: frm.doc.company,
					account_type: "Expense",
					disabled: 0,
				},
			};
		});
	},
	refresh: function (frm) {
		cur_frm.set_intro("");
		if (frm.doc.__islocal && !frappe.user_roles.includes("Employee")) {
			frm.set_intro(__("Fill the form and save it"));
		}

		if (
			frm.doc.docstatus === 1 &&
			frm.doc.pay_via_payment_entry == 1 &&
			frm.doc.status !== "Paid" &&
			frappe.model.can_create("HR Payment")
		) {
			frm.add_custom_button(
				__("Record Payment"),
				function () {
					frm.events.make_payment_entry(frm);
				},
				__("Create"),
			);
		}

		qcs_hrms.leave_utils.add_view_ledger_button(frm);
	},
	employee: function (frm) {
		if (frm.doc.employee) {
			frappe.run_serially([
				() => frm.trigger("get_employee_currency"),
				() => frm.trigger("get_leave_details_for_encashment"),
			]);
		}
	},
	company: function (frm) {
		frm.trigger("set_leave_period");
	},
	set_leave_period: function (frm) {
		if (!frm.doc.company || frm.doc.leave_period) return;

		const date = frm.doc.encashment_date || frappe.datetime.get_today();
		frappe.db.get_value(
			"Leave Period",
			{
				company: frm.doc.company,
				is_active: 1,
				from_date: ["<=", date],
				to_date: [">=", date],
			},
			"name",
			(r) => {
				if (r && r.name) frm.set_value("leave_period", r.name);
			},
		);
	},
	leave_type: function (frm) {
		frm.trigger("get_leave_details_for_encashment");
	},
	encashment_date: function (frm) {
		frm.trigger("get_leave_details_for_encashment");
	},
	get_leave_details_for_encashment: function (frm) {
		frm.set_value("actual_encashable_days", 0);
		frm.set_value("encashment_days", 0);

		if (frm.doc.docstatus === 0 && frm.doc.employee && frm.doc.leave_type) {
			return frappe.call({
				method: "get_leave_details_for_encashment",
				doc: frm.doc,
				callback: function (r) {
					frm.refresh_fields();
				},
			});
		}
	},

	get_employee_currency: function (frm) {
		frappe.call({
			method: "qcs_hrms.payroll.doctype.salary_structure_assignment.salary_structure_assignment.get_employee_currency",
			args: {
				employee: frm.doc.employee,
			},
			callback: function (r) {
				if (r.message) {
					frm.set_value("currency", r.message);
					frm.refresh_fields();
				}
			},
		});
	},
	make_payment_entry: function (frm) {
		return frappe.call({
			method: "qcs_hrms.ledger.doctype.hr_payment.hr_payment.make_payment_for",
			args: {
				reference_doctype: frm.doc.doctype,
				reference_name: frm.doc.name,
			},
			callback: function (r) {
				var doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			},
		});
	},
});
