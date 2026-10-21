# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe import _dict
from frappe.utils import flt, nowdate

import qcs_hrms
from qcs_hrms.hr.doctype.employee_advance.employee_advance import (
	EmployeeAdvanceOverPayment,
	create_return_through_additional_salary,
	make_return_entry,
)
from qcs_hrms.hr.doctype.expense_claim.expense_claim import get_advances, get_expense_claim
from qcs_hrms.hr.doctype.expense_claim.test_expense_claim import (
	get_expense_account,
	get_payable_account,
	make_expense_claim,
)
from qcs_hrms.ledger.doctype.hr_ledger_account.test_hr_ledger_account import create_ledger_account
from qcs_hrms.ledger.setup import create_default_accounts
from qcs_hrms.ledger.utils import make_hr_payment
from qcs_hrms.organization.doctype.employee.test_employee import make_employee
from qcs_hrms.payroll.doctype.payroll_entry.payroll_entry import get_start_end_dates
from qcs_hrms.payroll.doctype.payroll_entry.test_payroll_entry import make_payroll_entry, setup_salary_structure
from qcs_hrms.payroll.doctype.salary_component.test_salary_component import create_salary_component
from qcs_hrms.payroll.doctype.salary_structure.test_salary_structure import make_salary_structure
from qcs_hrms.tests.utils import HRMSTestSuite


class TestEmployeeAdvance(HRMSTestSuite):
	def setUp(self):
		frappe.db.delete("Employee Advance")
		create_default_accounts("_Test Company")

	def test_paid_amount_and_status(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name)

		make_payment_entry(advance)
		advance.reload()

		self.assertEqual(advance.paid_amount, 1000)
		self.assertEqual(advance.status, "Paid")

		# try making over payment
		payment = make_hr_payment("Employee Advance", advance.name, "Pay", 1000)
		self.assertRaises(frappe.ValidationError, payment.insert)

	def test_over_payment_from_other_ledger_postings(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name)
		make_payment_entry(advance)

		# a second payment posted directly to the ledger is caught when the advance is refreshed
		from qcs_hrms.ledger.utils import make_ledger_entries

		make_ledger_entries(
			[
				{
					"company": advance.company,
					"voucher_type": "Employee",
					"voucher_no": employee_name,
					"account": advance.advance_account,
					"debit": 100,
					"party_type": "Employee",
					"party": employee_name,
					"against_voucher_type": "Employee Advance",
					"against_voucher": advance.name,
				},
				{
					"company": advance.company,
					"voucher_type": "Employee",
					"voucher_no": employee_name,
					"account": get_bank_account(advance.company),
					"credit": 100,
				},
			]
		)
		advance.reload()
		self.assertRaises(EmployeeAdvanceOverPayment, advance.set_total_advance_paid)

	def test_paid_amount_on_payment_cancellation(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name)
		payment = make_payment_entry(advance)
		advance.reload()

		self.assertEqual(advance.paid_amount, 1000)
		self.assertEqual(advance.status, "Paid")

		payment.cancel()
		advance.reload()

		self.assertEqual(advance.paid_amount, 0)
		self.assertEqual(advance.status, "Unpaid")

		advance.cancel()
		advance.reload()
		self.assertEqual(advance.status, "Cancelled")

	def test_payment_posts_ledger_entries(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name)
		payment = make_payment_entry(advance)

		entries = frappe.get_all(
			"HR Ledger Entry",
			filters={"voucher_type": "HR Payment", "voucher_no": payment.name, "is_cancelled": 0},
			fields=["account", "debit", "credit", "party", "against_voucher_type", "against_voucher"],
		)
		advance_entry = next(e for e in entries if e.account == advance.advance_account)
		self.assertEqual(advance_entry.debit, 1000)
		self.assertEqual(advance_entry.party, employee_name)
		self.assertEqual(
			(advance_entry.against_voucher_type, advance_entry.against_voucher),
			("Employee Advance", advance.name),
		)
		self.assertEqual(sum(e.debit for e in entries), sum(e.credit for e in entries))

	def test_claimed_status(self):
		# CLAIMED Status check, full amount claimed
		payable_account = get_payable_account("_Test Company")
		claim = make_expense_claim(
			payable_account, 1000, 1000, "_Test Company", get_expense_account(), do_not_submit=True
		)

		advance = make_employee_advance(claim.employee)
		make_payment_entry(advance)

		claim = get_advances_for_claim(claim, advance.name)
		claim.save()
		claim.submit()

		advance.reload()
		self.assertEqual(advance.claimed_amount, 1000)
		self.assertEqual(advance.status, "Claimed")

		# advance should not be shown in claims
		advances = get_advances(claim)
		advances = [entry.employee_advance for entry in advances]
		self.assertTrue(advance.name not in advances)

		# cancel claim; status should be Paid
		claim.reload()
		claim.cancel()
		advance.reload()
		self.assertEqual(advance.claimed_amount, 0)
		self.assertEqual(advance.status, "Paid")

	def test_partly_claimed_and_returned_status(self):
		payable_account = get_payable_account("_Test Company")

		# PARTLY CLAIMED AND RETURNED status check
		# 500 Claimed, 500 Returned
		claim = make_expense_claim(
			payable_account, 500, 500, "_Test Company", get_expense_account(), do_not_submit=True
		)

		advance = make_employee_advance(claim.employee)
		make_payment_entry(advance)

		claim = get_advances_for_claim(claim, advance.name, amount=500)
		claim.save()
		claim.submit()

		advance.reload()
		self.assertEqual(advance.claimed_amount, 500)
		self.assertEqual(advance.status, "Paid")

		entry = make_return_entry(
			employee=advance.employee,
			company=advance.company,
			employee_advance_name=advance.name,
			return_amount=flt(advance.paid_amount - advance.claimed_amount),
			advance_account=advance.advance_account,
			mode_of_payment=advance.mode_of_payment,
			currency=advance.currency,
		)

		entry = frappe.get_doc(entry)
		self.assertEqual(entry.doctype, "HR Payment")
		self.assertEqual(entry.payment_type, "Receive")
		entry.insert()
		entry.submit()

		advance.reload()
		self.assertEqual(advance.return_amount, 500)
		self.assertEqual(advance.status, "Partly Claimed and Returned")

		# advance should not be shown in claims
		advances = get_advances(claim)
		advances = [entry.employee_advance for entry in advances]
		self.assertTrue(advance.name not in advances)

		# Cancel return entry; status should change to PAID
		entry.cancel()
		advance.reload()
		self.assertEqual(advance.return_amount, 0)
		self.assertEqual(advance.status, "Paid")

		# advance should be shown in claims
		advances = get_advances(claim)
		advances = [entry.employee_advance for entry in advances]
		self.assertTrue(advance.name in advances)

	def test_additional_salary_based_advance_repayment_flow(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name, {"repay_unclaimed_amount_from_salary": 1})
		make_payment_entry(advance)

		args = {"type": "Deduction"}
		create_salary_component("Advance Salary - Deduction", **args)
		make_salary_structure(
			"Test Additional Salary for Advance Return",
			"Monthly",
			employee=employee_name,
			company="_Test Company",
		)

		# additional salary for 700 first
		advance.reload()
		additional_salary = create_return_through_additional_salary(advance)
		additional_salary.salary_component = "Advance Salary - Deduction"
		additional_salary.payroll_date = nowdate()
		additional_salary.amount = 700
		additional_salary.insert()
		additional_salary.submit()

		# additional salary for remaining 300
		additional_salary = create_return_through_additional_salary(advance)
		additional_salary.salary_component = "Advance Salary - Deduction"
		additional_salary.payroll_date = nowdate()
		additional_salary.amount = 300
		additional_salary.insert()
		additional_salary.submit()

		# Employee Advance should not be updated directly
		advance.reload()
		self.assertEqual(advance.return_amount, 0)
		self.assertEqual(advance.status, "Paid")

		# should not allow scheduling more than available amount
		additional_salary = create_return_through_additional_salary(advance)
		additional_salary.salary_component = "Advance Salary - Deduction"
		additional_salary.payroll_date = nowdate()
		additional_salary.amount = 100

		self.assertRaises(frappe.ValidationError, additional_salary.insert)

	def test_advance_return_on_payroll_submission(self):
		company_doc = frappe.get_doc("Company", "_Test Company")
		employee = make_employee("test_repay_unclaimed_amount@payroll.com", company=company_doc.name)

		advance = make_employee_advance(employee, {"repay_unclaimed_amount_from_salary": 1})
		make_payment_entry(advance)
		advance.reload()

		payroll_details = create_payroll_for_advance_return(employee, company_doc, advance)
		salary_slip_name = frappe.db.get_value(
			"Salary Slip",
			{"payroll_entry": payroll_details.payroll_entry, "employee": employee},
			"name",
		)
		self.assertIsNotNone(salary_slip_name)
		salary_slip = frappe.get_doc("Salary Slip", salary_slip_name)

		deduction_row = next(
			(row for row in salary_slip.deductions if row.salary_component == payroll_details.advance_component),
			None,
		)
		self.assertIsNotNone(deduction_row, "Salary advance deduction not found")
		self.assertEqual(flt(deduction_row.amount), flt(advance.paid_amount))
		self.assertEqual(deduction_row.additional_salary, payroll_details.additional_salary)

		# the payroll posts a credit to the advance account against the advance
		deduction_entry = frappe.get_all(
			"HR Ledger Entry",
			fields=["account", "party", "credit"],
			filters={
				"against_voucher_type": "Employee Advance",
				"against_voucher": advance.name,
				"voucher_type": ["!=", "HR Payment"],
				"is_cancelled": 0,
			},
		)
		self.assertEqual(
			deduction_entry,
			[{"account": advance.advance_account, "party": employee, "credit": advance.paid_amount}],
		)

		advance.reload()
		self.assertEqual(advance.return_amount, advance.paid_amount)
		self.assertEqual(advance.status, "Returned")

	def test_payment_against_advance(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name)

		make_payment_entry(advance, 700)
		advance.reload()
		self.assertEqual(advance.status, "Partially Paid")
		self.assertEqual(advance.paid_amount, 700)

		payment = make_payment_entry(advance, 300)
		advance.reload()
		self.assertEqual(advance.status, "Paid")
		self.assertEqual(advance.paid_amount, 1000)

		payment.cancel()
		advance.reload()
		self.assertEqual(advance.status, "Partially Paid")
		self.assertEqual(advance.paid_amount, 700)

	def test_expense_claim_against_partially_paid_advance(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name)
		make_payment_entry(advance, 700)
		advance.reload()

		self.assertEqual(advance.status, "Partially Paid")

		currency, cost_center = frappe.db.get_value(
			"Company", "_Test Company", ["default_currency", "cost_center"]
		)
		claim = get_expense_claim(advance.name)  # create claim from employee advance form
		claim.update(
			{
				"payable_account": get_payable_account("_Test Company"),
				"currency": currency,
				"exchange_rate": 1,
				"approval_status": "Approved",
			}
		)
		claim.append(
			"expenses",
			{
				"expense_type": "Travel",
				"default_account": get_expense_account(),
				"amount": 1000,
				"sanctioned_amount": 1000,
				"cost_center": cost_center,
			},
		)
		claim.save()
		claim.submit()

		self.assertEqual(len(claim.advances), 1)
		self.assertEqual(claim.advances[0].employee_advance, advance.name)
		self.assertEqual(claim.advances[0].advance_paid, 700)
		self.assertEqual(claim.advances[0].allocated_amount, 700)
		self.assertEqual(claim.total_claimed_amount, 1000)
		self.assertEqual(claim.grand_total, 300)

		advance.reload()
		self.assertEqual(advance.claimed_amount, 700)
		self.assertEqual(advance.status, "Claimed")

	def test_precision(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name)
		make_payment_entry(advance)

		# PARTLY CLAIMED AND RETURNED
		payable_account = get_payable_account("_Test Company")
		claim = make_expense_claim(
			payable_account, 650.35, 619.34, "_Test Company", get_expense_account(), do_not_submit=True
		)

		claim = get_advances_for_claim(claim, advance.name, amount=619.34)
		claim.save()
		claim.submit()

		advance.reload()
		self.assertEqual(advance.status, "Paid")

		entry = make_return_entry(
			employee=advance.employee,
			company=advance.company,
			employee_advance_name=advance.name,
			return_amount=advance.paid_amount - advance.claimed_amount,
			advance_account=advance.advance_account,
			mode_of_payment=advance.mode_of_payment,
			currency=advance.currency,
		)

		entry = frappe.get_doc(entry)
		entry.insert()
		entry.submit()

		advance.reload()
		# precision is respected
		self.assertEqual(advance.return_amount, 380.66)
		self.assertEqual(advance.status, "Partly Claimed and Returned")

	def test_pending_amount(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")

		advance1 = make_employee_advance(employee_name)
		make_payment_entry(advance1, 500)
		advance1.reload()
		self.assertEqual(advance1.status, "Partially Paid")

		advance2 = make_employee_advance(employee_name)
		# 1000 - 500
		self.assertEqual(advance2.pending_amount, 500)
		make_payment_entry(advance2, 700)

		advance3 = make_employee_advance(employee_name)
		# (1000 - 500) + (1000 - 700)
		self.assertEqual(advance3.pending_amount, 800)

	def test_cancel_blocked_by_submitted_payment(self):
		employee_name = make_employee("_T@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name)
		make_payment_entry(advance, 1000)

		advance.reload()
		self.assertRaises(frappe.LinkExistsError, advance.cancel)

	def test_employee_advance_when_different_company_currency(self):
		employee = make_employee("test_adv_in_company_currency@example.com", "_Test Company")

		advance_account = create_advance_account("Employee Advance (USD)", "USD")

		advance = make_employee_advance(
			employee, {"currency": "USD", "exchange_rate": 80, "advance_account": advance_account}
		)
		make_payment_entry(advance, 1000)
		advance.reload()

		self.assertEqual(advance.status, "Paid")
		self.assertEqual(advance.paid_amount, 1000)

	def test_employee_advance_when_different_advance_currency(self):
		employee = make_employee("test_adv_in_advance_currency@example.com", "_Test Company")
		advance = make_employee_advance(employee, {"currency": "USD", "exchange_rate": 80}, do_not_submit=True)
		self.assertRaises(frappe.ValidationError, advance.save)

	def test_multicurrency_advance(self):
		advance_account = create_advance_account("Employee Advance (USD)", "USD")
		employee = make_employee(
			"test_adv_in_multicurrency@example.com",
			"_Test Company",
			salary_currency="USD",
			employee_advance_account=advance_account,
		)
		advance = make_employee_advance(employee)
		self.assertEqual(advance.status, "Unpaid")

		payment = make_payment_entry(advance, advance.advance_amount)
		advance.reload()
		self.assertEqual(advance.status, "Paid")
		self.assertEqual(payment.currency, "USD")
		self.assertEqual(payment.paid_amount, advance.paid_amount)

		expected_base_paid = flt(
			advance.paid_amount * payment.exchange_rate,
			advance.precision("base_paid_amount"),
		)
		self.assertEqual(advance.base_paid_amount, expected_base_paid)
		self.assertEqual(payment.base_paid_amount, expected_base_paid)

	def test_status_on_discard(self):
		employee_name = make_employee("Test_status@employee.advance", "_Test Company")
		advance = make_employee_advance(employee_name, do_not_submit=True)
		advance.insert()
		advance.reload()
		self.assertEqual(advance.status, "Draft")
		advance.discard()
		advance.reload()
		self.assertEqual(advance.status, "Cancelled")


def get_bank_account(company="_Test Company"):
	return create_default_accounts(company)["Bank"]


def make_payment_entry(advance, amount=None):
	"""Pay out (or reimburse) a document through a submitted HR Payment."""
	payment = make_hr_payment(advance.doctype, advance.name, "Pay", amount)
	payment.reference_no = "1"
	payment.reference_date = nowdate()
	payment.insert()
	payment.submit()
	return payment


def make_employee_advance(employee_name, args=None, do_not_submit=False):
	emp_details = frappe.db.get_value(
		"Employee", employee_name, ["salary_currency", "employee_advance_account"], as_dict=True
	)
	default_advance_account = create_default_accounts("_Test Company")["Employee Advances"]

	doc = frappe.new_doc("Employee Advance")
	doc.employee = employee_name
	doc.company = "_Test Company"
	doc.purpose = "For site visit"
	doc.currency = emp_details.salary_currency or qcs_hrms.get_company_currency("_Test Company")
	doc.advance_amount = 1000
	doc.posting_date = nowdate()
	doc.advance_account = emp_details.employee_advance_account or default_advance_account

	if args:
		doc.update(args)

	if do_not_submit:
		return doc
	doc.insert()
	doc.submit()
	return doc


def get_advances_for_claim(claim, advance_name, amount=None):
	advances = get_advances(claim, advance_name)
	claim.advances = []
	for advance in advances:
		if amount:
			advance.allocated_amount = amount
		claim.append("advances", advance)
	return claim


def create_advance_account(account_name, account_currency):
	return create_ledger_account(
		account_name, "_Test Company", account_type="Receivable", account_currency=account_currency
	)


def create_payroll_for_advance_return(employee, company, advance, return_amount=None, component_account=None):
	component_account = component_account or create_default_accounts(company.name)["Employee Advances"]

	# Advance deduction component
	component = create_salary_component(
		"Advance Salary",
		**{"type": "Deduction"},
	)
	component.set("accounts", [])
	component.append("accounts", {"company": company.name, "account": component_account})
	component.save()

	setup_salary_structure(employee, company)

	# Create Additional Salary for repayment
	additional_salary = create_return_through_additional_salary(advance)
	additional_salary.salary_component = component.name
	additional_salary.payroll_date = nowdate()
	additional_salary.amount = return_amount or advance.paid_amount
	additional_salary.submit()

	# Process payroll
	dates = get_start_end_dates("Monthly", nowdate())
	payroll_entry = make_payroll_entry(
		start_date=dates.start_date,
		end_date=dates.end_date,
		payable_account=company.default_payroll_payable_account,
		currency=company.default_currency,
		company=company.name,
		cost_center="Main - _TC",
	)
	return _dict(
		{
			"payroll_entry": payroll_entry.name,
			"advance_component": component.name,
			"additional_salary": additional_salary.name,
		}
	)
