# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe.utils import add_days, add_months, floor, flt, get_datetime, get_first_day, getdate

from qcs_hrms.organization.doctype.employee.test_employee import make_employee

from qcs_hrms.hr.doctype.attendance.attendance import mark_attendance
from qcs_hrms.hr.doctype.holiday_list_assignment.test_holiday_list_assignment import assign_holiday_list
from qcs_hrms.payroll.doctype.gratuity.gratuity import make_payment
from qcs_hrms.payroll.doctype.salary_slip.test_salary_slip import (
	create_account,
	make_deduction_salary_component,
	make_earning_salary_component,
	make_employee_salary_slip,
	make_holiday_list,
)
from qcs_hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip
from qcs_hrms.tests.utils import HRMSTestSuite


class TestGratuity(HRMSTestSuite):
	def setUp(self):
		for dt in ["Gratuity", "Salary Slip", "Additional Salary"]:
			frappe.db.delete(dt)

		self.date_of_joining = add_days(getdate(), -(6 * 365))
		self.relieving_date = getdate()
		self.employee = make_employee(
			"test_employee_gratuity@salary.com",
			company="_Test Company",
			date_of_joining=self.date_of_joining,
			relieving_date=self.relieving_date,
		)

		make_earning_salary_component(setup=True, test_tax=True, company_list=["_Test Company"])
		make_deduction_salary_component(setup=True, test_tax=True, company_list=["_Test Company"])
		make_holiday_list()

	@assign_holiday_list("Salary Slip Test Holiday List", "_Test Company")
	def test_gratuity_based_on_current_slab_via_additional_salary(self):
		"""
		Range	|	Fraction
		5-0		|	1
		"""
		sal_slip = create_salary_slip(self.employee)

		rule = setup_gratuity_rule("Rule Under Unlimited Contract on termination (UAE)")
		gratuity = create_gratuity(pay_via_salary_slip=1, employee=self.employee, rule=rule.name)

		# work experience calculation
		employee_total_workings_days = (
			get_datetime(self.relieving_date) - get_datetime(self.date_of_joining)
		).days
		experience = floor(employee_total_workings_days / rule.total_working_days_per_year)
		self.assertEqual(gratuity.current_work_experience, experience)

		# amount calculation
		component_amount = frappe.get_all(
			"Salary Detail",
			filters={
				"docstatus": 1,
				"parent": sal_slip.name,
				"parentfield": "earnings",
				"salary_component": "Basic Salary",
			},
			fields=["amount"],
			limit=1,
		)
		gratuity_amount = component_amount[0].amount * experience
		self.assertEqual(flt(gratuity_amount, 2), flt(gratuity.amount, 2))

		# additional salary creation (Pay via salary slip)
		self.assertTrue(frappe.db.exists("Additional Salary", {"ref_docname": gratuity.name}))

		# gratuity should be marked "Paid" on the next salary slip submission
		salary_slip = make_salary_slip("Test Gratuity", employee=self.employee)
		salary_slip.posting_date = getdate()
		salary_slip.insert()
		salary_slip.submit()

		gratuity.reload()
		self.assertEqual(gratuity.status, "Paid")

	@assign_holiday_list("Salary Slip Test Holiday List", "_Test Company")
	def test_gratuity_based_on_all_previous_slabs_via_hr_payment(self):
		"""
		Range   |   Fraction
		0-3     |   0.5
		3-6     |   1.0
		6-9		|	1.5
		"""
		sal_slip = create_salary_slip(self.employee)

		rule = setup_gratuity_rule("Rule Under Limited Contract (UAE)")
		rule.gratuity_rule_slabs = []
		for slab in [
			{"from_year": 0, "to_year": 3, "fraction_of_applicable_earnings": 0.5},
			{"from_year": 3, "to_year": 6, "fraction_of_applicable_earnings": 1.0},
			{"from_year": 6, "to_year": 9, "fraction_of_applicable_earnings": 1.5},
		]:
			new_slab = frappe.get_doc(
				{
					"doctype": "Gratuity Rule Slab",
					"from_year": slab["from_year"],
					"to_year": slab["to_year"],
					"fraction_of_applicable_earnings": slab["fraction_of_applicable_earnings"],
					"parent": rule.name,
					"parentfield": "gratuity_rule_slabs",
					"parenttype": "Gratuity Rule",
				}
			)
			rule.append("gratuity_rule_slabs", new_slab)
		rule.save()
		rule.reload()


		gratuity = create_gratuity(
			expense_account=get_gratuity_expense_account(),
			mode_of_payment="Cash",
			employee=self.employee,
			rule=rule.name,
		)

		# work experience calculation
		employee_total_workings_days = (
			get_datetime(self.relieving_date) - get_datetime(self.date_of_joining)
		).days
		experience = floor(employee_total_workings_days / rule.total_working_days_per_year)
		self.assertEqual(gratuity.current_work_experience, experience)

		# amount calculation
		component_amount = frappe.get_all(
			"Salary Detail",
			filters={
				"docstatus": 1,
				"parent": sal_slip.name,
				"parentfield": "earnings",
				"salary_component": "Basic Salary",
			},
			fields=["amount"],
			limit=1,
		)

		gratuity_amount = ((3 * 0.5) + (3 * 1.0)) * component_amount[0].amount
		self.assertEqual(flt(gratuity_amount, 2), flt(gratuity.amount, 2))
		self.assertEqual(gratuity.status, "Unpaid")

		# accrual: expense debited, payable credited against the gratuity with the employee as party
		ledger_entries = frappe.get_all(
			"HR Ledger Entry",
			filters={"voucher_type": "Gratuity", "voucher_no": gratuity.name, "is_cancelled": 0},
			fields=["account", "debit", "credit", "party", "against_voucher"],
		)
		expected = {
			gratuity.expense_account: (flt(gratuity.amount, 2), 0),
			gratuity.payable_account: (0, flt(gratuity.amount, 2)),
		}
		self.assertEqual(
			{d.account: (flt(d.debit, 2), flt(d.credit, 2)) for d in ledger_entries},
			expected,
		)
		payable_entry = next(d for d in ledger_entries if d.account == gratuity.payable_account)
		self.assertEqual(payable_entry.party, self.employee)
		self.assertEqual(payable_entry.against_voucher, gratuity.name)

		payment = make_payment("Gratuity", gratuity.name)
		payment.paid_from = get_bank_account()
		payment.reference_no = "123467"
		payment.reference_date = getdate()
		payment.submit()
		self.assertEqual(payment.paid_to, gratuity.payable_account)
		self.assertEqual(payment.references[0].reference_doctype, "Gratuity")

		gratuity.reload()
		self.assertEqual(gratuity.status, "Paid")
		self.assertEqual(flt(gratuity.paid_amount, 2), flt(gratuity.amount, 2))

		payment.cancel()
		gratuity.reload()
		self.assertEqual(gratuity.status, "Unpaid")
		self.assertEqual(gratuity.paid_amount, 0)

		# cancelling the gratuity reverses its ledger entries
		gratuity.cancel()
		self.assertFalse(
			frappe.db.exists(
				"HR Ledger Entry",
				{"voucher_type": "Gratuity", "voucher_no": gratuity.name, "is_cancelled": 0},
			)
		)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{
			"payroll_based_on": "Attendance",
			"consider_unmarked_attendance_as": "Present",
			"include_holidays_in_total_working_days": True,
		},
	)
	def test_gratuity_amount_consistent_irrespective_of_payment_days(self):
		date = getdate("2024-01-01")
		create_salary_slip(self.employee, date)

		setup_gratuity_rule("Rule Under Limited Contract (UAE)")

		gratuity = create_gratuity(
			expense_account=get_gratuity_expense_account(), mode_of_payment="Cash", employee=self.employee
		)
		self.assertEqual(gratuity.amount, 190000.0)

		# gratuity amount should be unaffected inspite of marking the employee absent for a day
		frappe.db.delete("Gratuity", gratuity.name)
		mark_attendance(self.employee, date, "Absent")
		gratuity = create_gratuity(
			expense_account=get_gratuity_expense_account(), mode_of_payment="Cash", employee=self.employee
		)
		self.assertEqual(gratuity.amount, 190000.0)

	def test_status_on_discard(self):
		create_salary_slip(self.employee)
		setup_gratuity_rule("Rule Under Limited Contract (UAE)")
		# create gratuity
		gratuity = create_gratuity(
			do_not_submit=True,
			expense_account=get_gratuity_expense_account(),
			mode_of_payment="Cash",
			employee=self.employee,
		)
		gratuity.discard()
		gratuity.reload()
		self.assertEqual(gratuity.status, "Cancelled")


def setup_gratuity_rule(name: str) -> dict:
	from qcs_hrms.regional.united_arab_emirates.setup import setup

	if not frappe.db.exists("Gratuity Rule", name):
		setup()

	rule = frappe.get_doc("Gratuity Rule", name)
	rule.applicable_earnings_component = []
	rule.append("applicable_earnings_component", {"salary_component": "Basic Salary"})
	rule.save()

	return rule


def create_gratuity(do_not_submit=False, **args):
	if args:
		args = frappe._dict(args)
	gratuity = frappe.new_doc("Gratuity")
	gratuity.employee = args.employee
	gratuity.posting_date = getdate()
	gratuity.gratuity_rule = args.rule or "Rule Under Limited Contract (UAE)"
	gratuity.pay_via_salary_slip = args.pay_via_salary_slip or 0
	if gratuity.pay_via_salary_slip:
		gratuity.payroll_date = getdate()
		gratuity.salary_component = "Performance Bonus"
	else:
		gratuity.expense_account = args.expense_account or get_gratuity_expense_account()
		gratuity.payable_account = args.payable_account or get_gratuity_payable_account()
		gratuity.mode_of_payment = args.mode_of_payment or "Cash"
		gratuity.cost_center = args.cost_center or "Main - _TC"

	gratuity.save()
	if do_not_submit:
		return gratuity
	gratuity.submit()

	return gratuity


def get_gratuity_expense_account(company="_Test Company"):
	return create_account("Gratuity Expense", company, account_type="Expense")


def get_gratuity_payable_account(company="_Test Company"):
	return create_account("Gratuity Payable", company, account_type="Payable")


def get_bank_account(company="_Test Company"):
	return create_account("_Test Bank", company, account_type="Bank")


def create_salary_slip(employee, posting_date=None):
	posting_date = posting_date or get_first_day(add_months(getdate(), -1))
	salary_slip = make_employee_salary_slip(employee, "Monthly", "Test Gratuity", posting_date=posting_date)
	salary_slip.start_date = posting_date
	salary_slip.end_date = None
	salary_slip.submit()

	return salary_slip
