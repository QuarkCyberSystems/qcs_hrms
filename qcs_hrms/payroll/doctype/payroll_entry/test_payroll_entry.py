# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from unittest.mock import patch


import frappe
from frappe.query_builder.functions import Sum
from frappe.utils import add_days, add_months, date_diff, flt, getdate, nowdate

from qcs_hrms.ledger.setup import create_default_accounts
from qcs_hrms.organization.doctype.employee.test_employee import make_employee

from qcs_hrms.hr.doctype.employee_advance.employee_advance import (
	create_return_through_additional_salary,
)
from qcs_hrms.payroll.doctype.payroll_entry.payroll_entry import (
	PayrollEntry,
	get_end_date,
	get_start_end_dates,
	set_match_conditions,
)
from qcs_hrms.payroll.doctype.salary_component.test_salary_component import create_salary_component
from qcs_hrms.payroll.doctype.salary_slip.test_salary_slip import (
	create_account,
	make_deduction_salary_component,
	make_earning_salary_component,
	mark_attendance,
	set_salary_component_account,
)
from qcs_hrms.payroll.doctype.salary_structure.test_salary_structure import (
	create_salary_structure_assignment,
	make_salary_structure,
)
from qcs_hrms.tests.test_utils import create_department
from qcs_hrms.tests.utils import HRMSTestSuite
from qcs_hrms.utils import get_date_range


class TestPayrollEntry(HRMSTestSuite):
	def setUp(self):
		make_earning_salary_component(setup=True, company_list=["_Test Company"])
		make_deduction_salary_component(setup=True, test_tax=False, company_list=["_Test Company"])

		frappe.db.set_value("Company", "_Test Company", "default_holiday_list", "_Test Holiday List")
		frappe.db.set_single_value("Payroll Settings", "email_salary_slip_to_employee", 0)
		create_default_accounts("_Test Company")
		frappe.db.set_value("HR Ledger Account", "Employee Advances - _TC", "account_type", "Receivable")
		# set default payable account
		default_account = frappe.db.get_value("Company", "_Test Company", "default_payroll_payable_account")
		if not default_account or default_account != "_Test Payroll Payable - _TC":
			create_account(
				account_name="_Test Payroll Payable",
				company="_Test Company",
				parent_account="Current Liabilities - _TC",
				account_type="Payable",
			)
			frappe.db.set_value(
				"Company", "_Test Company", "default_payroll_payable_account", "_Test Payroll Payable - _TC"
			)

		payroll_account = frappe.get_doc("HR Ledger Account", "_Test Payroll Payable - _TC")
		if payroll_account and payroll_account.account_type != "Payable":
			frappe.db.set_value("HR Ledger Account", "_Test Payroll Payable - _TC", "account_type", "Payable")

	def test_payroll_entry(self):
		company = frappe.get_doc("Company", "_Test Company")
		employee = frappe.db.get_value("Employee", {"company": "_Test Company"})
		setup_salary_structure(employee, company)

		dates = get_start_end_dates("Monthly", nowdate())
		make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company.default_payroll_payable_account,
			currency=company.default_currency,
			company=company.name,
			cost_center="Main - _TC",
		)

	def test_multi_currency_payroll_entry(self):
		company = frappe.get_doc("Company", "_Test Company")
		create_department("Accounts")
		employee = make_employee(
			"test_muti_currency_employee@payroll.com", company=company.name, department="Accounts - _TC"
		)
		salary_structure = "_Test Multi Currency Salary Structure"
		setup_salary_structure(employee, company, "USD", salary_structure)

		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company.default_payroll_payable_account,
			currency="USD",
			exchange_rate=70,
			company=company.name,
			cost_center="Main - _TC",
		)
		salary_slip = frappe.db.get_value("Salary Slip", {"payroll_entry": payroll_entry.name}, "name")
		salary_slip = frappe.get_doc("Salary Slip", salary_slip)

		# accrual ledger entries are posted in company currency
		entries = get_ledger_entries(payroll_entry.name)
		self.assertTrue(entries)
		self.assertEqual(flt(sum(d.debit for d in entries), 2), flt(salary_slip.base_gross_pay, 2))
		self.assertEqual(flt(sum(d.credit for d in entries), 2), flt(salary_slip.base_gross_pay, 2))

		# draft HR Payment in payroll currency, one reference per salary slip
		payments = get_linked_payments(payroll_entry.name)
		self.assertEqual(len(payments), 1)
		payment = frappe.get_doc("HR Payment", payments[0])
		self.assertEqual(payment.currency, "USD")
		self.assertEqual(payment.party, employee)
		self.assertEqual(flt(payment.paid_amount, 2), flt(salary_slip.net_pay, 2))
		self.assertEqual(flt(payment.base_paid_amount, 2), flt(salary_slip.base_net_pay, 2))
		self.assertEqual(payment.references[0].reference_doctype, "Salary Slip")
		self.assertEqual(payment.references[0].reference_name, salary_slip.name)

	def test_payroll_entry_with_employee_cost_center(self):
		department = create_department("Cost Center Test")

		employee1 = make_employee(
			"test_emp1@example.com",
			payroll_cost_center="_Test Cost Center - _TC",
			department=department,
			company="_Test Company",
		)
		employee2 = make_employee("test_emp2@example.com", department=department, company="_Test Company")

		create_assignments_with_cost_centers(employee1, employee2)

		dates = get_start_end_dates("Monthly", nowdate())
		pe = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account="_Test Payroll Payable - _TC",
			currency="INR",
			department=department,
			company="_Test Company",
			payment_account="Cash - _TC",
			cost_center="Main - _TC",
		)
		hle = frappe.qb.DocType("HR Ledger Entry")
		je_entries = (
			frappe.qb.from_(hle)
			.select(hle.account, hle.cost_center, Sum(hle.debit), Sum(hle.credit))
			.where(
				(hle.voucher_type == "Payroll Entry") & (hle.voucher_no == pe.name) & (hle.is_cancelled == 0)
			)
			.groupby(hle.account, hle.cost_center)
			.orderby(hle.account)
			.orderby(hle.cost_center)
		).run()
		je_entries = tuple((d[0], d[1], flt(d[2]), flt(d[3])) for d in je_entries)
		expected_je = (
			("_Test Payroll Payable - _TC", "Main - _TC", 0.0, 155600.0),
			("Salary - _TC", "_Test Cost Center - _TC", 124800.0, 0.0),
			("Salary - _TC", "_Test Cost Center 2 - _TC", 31200.0, 0.0),
			("Salary Deductions - _TC", "_Test Cost Center - _TC", 0.0, 320.0),
			("Salary Deductions - _TC", "_Test Cost Center 2 - _TC", 0.0, 80.0),
		)

		self.assertEqual(je_entries, expected_je)

	def test_employee_cost_center_breakup(self):
		"""Test only the latest salary structure assignment is considered for cost center breakup"""
		COMPANY = "_Test Company"
		COST_CENTERS = {"_Test Cost Center - _TC": 60, "_Test Cost Center 2 - _TC": 40}
		department = create_department("Cost Center Test")
		employee = make_employee("test_emp1@example.com", department=department, company=COMPANY)
		salary_structure = make_salary_structure(
			"_Test Salary Structure 2",
			"Monthly",
			employee,
			company=COMPANY,
		)

		# update cost centers in salary structure assignment for employee
		new_assignment = frappe.db.get_value(
			"Salary Structure Assignment",
			{"employee": employee, "salary_structure": salary_structure.name, "docstatus": 1},
			"name",
		)
		new_assignment = frappe.get_doc("Salary Structure Assignment", new_assignment)
		new_assignment.payroll_cost_centers = []
		for cost_center, percentage in COST_CENTERS.items():
			new_assignment.append(
				"payroll_cost_centers", {"cost_center": cost_center, "percentage": percentage}
			)
		new_assignment.save()

		# make an old salary structure assignment to test and ensure old cost center mapping is excluded
		old_assignment = frappe.copy_doc(new_assignment)
		old_assignment.from_date = add_months(new_assignment.from_date, -1)
		old_assignment.payroll_cost_centers = []
		old_assignment.append("payroll_cost_centers", {"cost_center": "Main - _TC", "percentage": 100})
		old_assignment.submit()

		dates = get_start_end_dates("Monthly", nowdate())
		pe = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account="_Test Payroll Payable - _TC",
			currency="INR",
			department=department,
			company="_Test Company",
			payment_account="Cash - _TC",
			cost_center="Main - _TC",
		)

		# only new cost center breakup is considered
		cost_centers = pe.get_payroll_cost_centers_for_employee(employee, "_Test Salary Structure 2")
		self.assertEqual(cost_centers, COST_CENTERS)

	def test_get_end_date(self):
		self.assertEqual(get_end_date("2017-01-01", "monthly"), {"end_date": "2017-01-31"})
		self.assertEqual(get_end_date("2017-02-01", "monthly"), {"end_date": "2017-02-28"})
		self.assertEqual(get_end_date("2017-02-01", "fortnightly"), {"end_date": "2017-02-14"})
		self.assertEqual(get_end_date("2017-02-01", "bimonthly"), {"end_date": ""})
		self.assertEqual(get_end_date("2017-01-01", "bimonthly"), {"end_date": ""})
		self.assertEqual(get_end_date("2020-02-15", "bimonthly"), {"end_date": ""})
		self.assertEqual(get_end_date("2017-02-15", "monthly"), {"end_date": "2017-03-14"})
		self.assertEqual(get_end_date("2017-02-15", "daily"), {"end_date": "2017-02-15"})

	def test_salary_slip_operation_queueing(self):
		company = "_Test Company"
		company_doc = frappe.get_doc("Company", company)
		employee = make_employee("test_employee@payroll.com", company=company)
		setup_salary_structure(employee, company_doc)

		# enqueue salary slip creation via payroll entry
		# Payroll Entry status should change to Queued
		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = get_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company=company_doc.name,
			cost_center="Main - _TC",
		)
		frappe.flags.enqueue_payroll_entry = True
		payroll_entry.submit()
		payroll_entry.reload()

		self.assertEqual(payroll_entry.status, "Queued")
		frappe.flags.enqueue_payroll_entry = False

	def test_salary_slip_operation_failure(self):
		company = "_Test Company"
		company_doc = frappe.get_doc("Company", company)
		employee = make_employee("test_employee@payroll.com", company=company)

		salary_structure = make_salary_structure(
			"_Test Salary Structure",
			"Monthly",
			employee,
			company=company,
			currency=company_doc.default_currency,
		)

		# reset account in component to test submission failure
		component = frappe.get_doc("Salary Component", salary_structure.earnings[0].salary_component)
		component.accounts = []
		component.save()

		# salary slip submission via payroll entry
		# Payroll Entry status should change to Failed because of the missing account setup
		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = get_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company=company_doc.name,
			cost_center="Main - _TC",
		)

		# set employee as Inactive to check creation failure
		frappe.db.set_value("Employee", employee, "status", "Inactive")
		payroll_entry.submit()
		payroll_entry.reload()
		self.assertEqual(payroll_entry.status, "Failed")
		self.assertIsNotNone(payroll_entry.error_message)

		frappe.db.set_value("Employee", employee, "status", "Active")

		payroll_entry.create_salary_slips()
		payroll_entry.submit()
		payroll_entry.submit_salary_slips()
		payroll_entry.reload()
		self.assertEqual(payroll_entry.status, "Failed")
		self.assertIsNotNone(payroll_entry.error_message)

		# set accounts
		for data in frappe.get_all("Salary Component", pluck="name"):
			set_salary_component_account(data, company_list=[company])

		# Payroll Entry successful, status should change to Submitted

		payroll_entry.create_salary_slips()
		payroll_entry.submit()
		payroll_entry.submit_salary_slips()
		payroll_entry.reload()

		self.assertEqual(payroll_entry.status, "Submitted")
		self.assertEqual(payroll_entry.error_message, "")

	def test_payroll_entry_cancellation(self):
		company_doc = frappe.get_doc("Company", "_Test Company")
		employee = make_employee("test_employee@payroll.com", company=company_doc.name)

		setup_salary_structure(employee, company_doc)
		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company=company_doc.name,
			cost_center="Main - _TC",
			payment_account="Cash - _TC",
		)
		submit_bank_entry(payroll_entry.name)

		salary_slip = frappe.db.get_value("Salary Slip", {"payroll_entry": payroll_entry.name}, "name")
		self.assertIsNotNone(salary_slip)

		# accrual posted, salary slip paid via the submitted HR Payment
		self.assertTrue(get_ledger_entries(payroll_entry.name))
		payments = get_linked_payments(payroll_entry.name, docstatus=1)
		self.assertEqual(len(payments), 1)
		self.assertEqual(frappe.db.get_value("Salary Slip", salary_slip, "hr_payment"), payments[0])
		payroll_entry.reload()
		self.assertEqual(payroll_entry.status, "Paid")

		# cannot cancel while a payment is linked
		self.assertRaises(frappe.ValidationError, payroll_entry.cancel)
		cancel_linked_payments(payroll_entry.name)
		payroll_entry.reload()
		self.assertEqual(payroll_entry.status, "Submitted")
		self.assertFalse(frappe.db.get_value("Salary Slip", salary_slip, "hr_payment"))

		frappe.flags.enqueue_payroll_entry = True
		payroll_entry.cancel()
		frappe.flags.enqueue_payroll_entry = False
		self.assertEqual(payroll_entry.status, "Cancelled")

		salary_slip = frappe.db.get_value("Salary Slip", {"payroll_entry": payroll_entry.name}, "name")
		self.assertIsNone(salary_slip)

		# accrual entries cancelled and reversed
		self.assertFalse(get_ledger_entries(payroll_entry.name))
		cancelled = get_ledger_entries(payroll_entry.name, is_cancelled=1)
		self.assertTrue(cancelled)
		self.assertEqual(
			flt(
				sum(d.debit - d.credit for d in cancelled if d.account == payroll_entry.payroll_payable_account)
			),
			0,
		)

	def test_payroll_entry_cancellation_with_hr_manager(self):
		company_doc = frappe.get_doc("Company", "_Test Company")
		employee = make_employee("test_hr_manager_employee@payroll.com", company=company_doc.name)

		setup_salary_structure(employee, company_doc)
		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company=company_doc.name,
			cost_center="Main - _TC",
			payment_account="Cash - _TC",
		)

		hr_user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "test_hr_manager@payroll.com",
				"first_name": "Test HR Manager",
				"enabled": 1,
			}
		).insert(ignore_if_duplicate=True)
		hr_user.add_roles("HR Manager")
		frappe.set_user(hr_user.name)

		payroll_entry.submit()
		self.assertEqual(payroll_entry.status, "Submitted")

		delete_draft_payments(payroll_entry.name)
		payroll_entry.cancel()
		self.assertEqual(payroll_entry.status, "Cancelled")

		frappe.set_user("Administrator")

	def test_payroll_entry_status(self):
		company_doc = frappe.get_doc("Company", "_Test Company")
		employee = make_employee("test_employee@payroll.com", company=company_doc.name)

		setup_salary_structure(employee, company_doc)
		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = get_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company=company_doc.name,
			cost_center="Main - _TC",
		)
		payroll_entry.submit()
		self.assertEqual(payroll_entry.status, "Submitted")

		payroll_entry.cancel()
		self.assertEqual(payroll_entry.status, "Cancelled")

	def test_payroll_entry_cancellation_against_cancelled_payment(self):
		company_doc = frappe.get_doc("Company", "_Test Company")
		employee = make_employee("test_pe_cancellation@payroll.com", company=company_doc.name)

		setup_salary_structure(employee, company_doc)
		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company=company_doc.name,
			cost_center="Main - _TC",
			payment_account="Cash - _TC",
		)

		submit_bank_entry(payroll_entry.name)

		# cancel the payments
		for payment in get_linked_payments(payroll_entry.name, docstatus=1):
			payment_doc = frappe.get_doc("HR Payment", payment)
			self.assertEqual(payment_doc.cost_center, payroll_entry.cost_center)
			payment_doc.cancel()

		# cancel the salary slip
		salary_slip = frappe.db.get_value("Salary Slip", {"payroll_entry": payroll_entry.name}, "name")
		salary_slip = frappe.get_doc("Salary Slip", salary_slip)
		salary_slip.cancel()

		payroll_entry.reload()
		payroll_entry.cancel()
		self.assertEqual(payroll_entry.status, "Cancelled")

	def test_payroll_accrual_ledger_entries_with_employee_party(self):
		company_doc = frappe.get_doc("Company", "_Test Company")
		employee = make_employee(
			"test_payroll_accrual_ledger_with_employee_party@payroll.com", company=company_doc.name
		)

		setup_salary_structure(employee, company_doc)

		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company=company_doc.name,
			cost_center="Main - _TC",
		)

		salary_slip = frappe.get_doc("Salary Slip", {"payroll_entry": payroll_entry.name})
		self.assertTrue(salary_slip.accrual_posted)

		entries = get_ledger_entries(payroll_entry.name)
		self.assertEqual(flt(sum(d.debit for d in entries), 2), flt(sum(d.credit for d in entries), 2))

		payable_rows = [d for d in entries if d.account == company_doc.default_payroll_payable_account]
		self.assertEqual(len(payable_rows), 1)
		self.assertEqual(payable_rows[0].party_type, "Employee")
		self.assertEqual(payable_rows[0].party, employee)
		self.assertEqual(payable_rows[0].against_voucher_type, "Salary Slip")
		self.assertEqual(payable_rows[0].against_voucher, salary_slip.name)
		self.assertEqual(flt(payable_rows[0].credit, 2), flt(salary_slip.net_pay, 2))

		# expense / deduction rows carry no party
		for row in entries:
			if row.account != company_doc.default_payroll_payable_account:
				self.assertFalse(row.party)

	def test_advance_deduction_in_accrual_ledger_entries(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			make_employee_advance,
			make_payment_entry,
		)

		company_doc = frappe.get_doc("Company", "_Test Company")
		employee = make_employee("test_employee@payroll.com", company=company_doc.name)

		setup_salary_structure(employee, company_doc)

		# create employee advance
		advance = make_employee_advance(employee, {"repay_unclaimed_amount_from_salary": 1})
		make_payment_entry(advance)
		advance.reload()

		# return advance through additional salary (deduction)
		component = create_salary_component("Advance Salary - Deduction", **{"type": "Deduction"})
		component.append(
			"accounts",
			{"company": company_doc.name, "account": "Employee Advances - _TC"},
		)
		component.save()

		additional_salary = create_return_through_additional_salary(advance)
		additional_salary.salary_component = component.name
		additional_salary.payroll_date = nowdate()
		additional_salary.amount = advance.paid_amount
		additional_salary.submit()

		# payroll entry
		dates = get_start_end_dates("Monthly", nowdate())
		make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company=company_doc.name,
			cost_center="Main - _TC",
		)

		# check advance deduction entry correctly mapped in accrual entry
		deduction_entry = frappe.get_all(
			"HR Ledger Entry",
			fields=["account", "party", "debit", "credit"],
			filters={
				"voucher_type": "Payroll Entry",
				"against_voucher_type": "Employee Advance",
				"against_voucher": advance.name,
				"is_cancelled": 0,
			},
		)[0]

		expected_entry = {
			"account": advance.advance_account,
			"party": employee,
			"debit": 0.0,
			"credit": advance.paid_amount,
		}

		self.assertEqual(deduction_entry, expected_entry)

	def test_bank_entry_creates_hr_payment_per_employee(self):
		department = create_department("Cost Center Test")
		employee1 = make_employee(
			"test_emp1@example.com",
			payroll_cost_center="_Test Cost Center - _TC",
			department=department,
			company="_Test Company",
		)
		employee2 = make_employee("test_emp2@example.com", department=department, company="_Test Company")

		create_assignments_with_cost_centers(employee1, employee2)

		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account="_Test Payroll Payable - _TC",
			currency="INR",
			department=department,
			company="_Test Company",
			payment_account="Cash - _TC",
			cost_center="Main - _TC",
		)
		payroll_entry.reload()
		# already created by make_payroll_entry
		self.assertEqual(payroll_entry.make_bank_entry(), [])

		payments = [
			frappe.get_doc("HR Payment", name) for name in get_linked_payments(payroll_entry.name, docstatus=0)
		]
		self.assertEqual(len(payments), 2)
		self.assertEqual({p.party for p in payments}, {employee1, employee2})

		for payment in payments:
			self.assertEqual(payment.payment_type, "Pay")
			self.assertEqual(payment.party_type, "Employee")
			self.assertEqual(payment.paid_from, "Cash - _TC")
			self.assertEqual(payment.paid_to, "_Test Payroll Payable - _TC")
			self.assertEqual(payment.paid_amount, 77800.0)
			self.assertEqual(len(payment.references), 1)
			self.assertEqual(payment.references[0].reference_doctype, "Salary Slip")
			self.assertEqual(
				frappe.db.get_value("Salary Slip", payment.references[0].reference_name, "employee"),
				payment.party,
			)

		payments[0].submit()
		payroll_entry.reload()
		self.assertEqual(payroll_entry.status, "Submitted")

		payments[1].submit()
		payroll_entry.reload()
		self.assertEqual(payroll_entry.status, "Paid")
		for payment in payments:
			self.assertEqual(
				frappe.db.get_value("Salary Slip", payment.references[0].reference_name, "hr_payment"),
				payment.name,
			)

		# payroll payable settled per salary slip
		hle = frappe.qb.DocType("HR Ledger Entry")
		for payment in payments:
			balance = (
				frappe.qb.from_(hle)
				.select(Sum(hle.debit - hle.credit))
				.where(
					(hle.account == "_Test Payroll Payable - _TC")
					& (hle.against_voucher_type == "Salary Slip")
					& (hle.against_voucher == payment.references[0].reference_name)
					& (hle.is_cancelled == 0)
				)
			).run()[0][0]
			self.assertEqual(flt(balance), 0)

		payments[1].cancel()
		payroll_entry.reload()
		self.assertEqual(payroll_entry.status, "Submitted")
		self.assertFalse(
			frappe.db.get_value("Salary Slip", payments[1].references[0].reference_name, "hr_payment")
		)

	def test_validate_attendance(self):
		company = frappe.get_doc("Company", "_Test Company")
		employee = frappe.db.get_value("Employee", {"company": "_Test Company"})
		setup_salary_structure(employee, company)

		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = get_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company.default_payroll_payable_account,
			currency=company.default_currency,
			company=company.name,
			cost_center="Main - _TC",
		)

		# case 1: validate unmarked attendance
		payroll_entry.validate_attendance = True
		employees = payroll_entry.get_employees_with_unmarked_attendance()
		self.assertEqual(employees[0]["employee"], employee)

		# case 2: employee should not be flagged for remaining payroll days for a mid-month relieving date
		relieving_date = add_days(payroll_entry.start_date, 15)
		frappe.db.set_value("Employee", employee, "relieving_date", relieving_date)

		for date in get_date_range(payroll_entry.start_date, relieving_date):
			mark_attendance(employee, date, "Present", ignore_validate=True)

		employees = payroll_entry.get_employees_with_unmarked_attendance()
		self.assertFalse(employees)

		# case 3: employee should not flagged for remaining payroll days
		frappe.db.set_value("Employee", employee, "relieving_date", None)

		for date in get_date_range(add_days(relieving_date, 1), payroll_entry.end_date):
			mark_attendance(employee, date, "Present", ignore_validate=True)

		employees = payroll_entry.get_employees_with_unmarked_attendance()
		self.assertFalse(employees)

	def test_validate_attendance_with_holiday_list_assignment(self):
		from qcs_hrms.hr.doctype.holiday_list_assignment.test_holiday_list_assignment import (
			create_holiday_list_assignment,
		)
		from qcs_hrms.payroll.doctype.salary_slip.test_salary_slip import make_holiday_list

		company = frappe.get_doc("Company", "_Test Company")
		employee = make_employee("test_validate_attendance_hla@payroll.com", company=company.name)
		setup_salary_structure(employee, company)

		dates = get_start_end_dates("Monthly", nowdate())
		weekend_off_list = make_holiday_list(
			"Test Payroll Entry Weekend Off",
			dates.start_date,
			dates.end_date,
			weekly_off_days=["Saturday", "Sunday"],
		)
		create_holiday_list_assignment("Employee", employee, weekend_off_list, from_date=dates.start_date)

		payroll_entry = get_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company.default_payroll_payable_account,
			currency=company.default_currency,
			company=company.name,
			cost_center="Main - _TC",
		)
		payroll_entry.validate_attendance = True

		unmarked = next(
			d for d in payroll_entry.get_employees_with_unmarked_attendance() if d["employee"] == employee
		)
		weekend_days = len(
			[d for d in get_date_range(dates.start_date, dates.end_date) if getdate(d).weekday() >= 5]
		)
		payroll_days = date_diff(dates.end_date, dates.start_date) + 1
		self.assertEqual(unmarked["unmarked_days"], payroll_days - weekend_days)

	@HRMSTestSuite.change_settings(
		"Payroll Settings",
		{
			"payroll_based_on": "Attendance",
			"consider_unmarked_attendance_as": "Absent",
			# holidays would otherwise be paid days, giving employee2 a non-zero net pay
			"include_holidays_in_total_working_days": 0,
			"consider_marked_attendance_on_holidays": 1,
		},
	)
	def test_skip_bank_entry_for_employees_with_zero_amount(self):
		company_doc = frappe.get_doc("Company", "_Test Company")
		employee1 = make_employee("test_employee11@payroll.com", company=company_doc.name)
		employee2 = make_employee("test_employee12@payroll.com", company=company_doc.name)

		setup_salary_structure(employee1, company_doc)
		setup_salary_structure(employee2, company_doc)

		dates = get_start_end_dates("Monthly", nowdate())
		for date in get_date_range(dates.start_date, dates.end_date):
			mark_attendance(employee1, date, "Present", ignore_validate=True)

		payroll_entry = get_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company=company_doc.name,
			cost_center="Main - _TC",
		)
		payroll_entry.submit()
		payroll_entry.submit_salary_slips()
		self.assertTrue(get_ledger_entries(payroll_entry.name))

		# employee2 has no attendance, hence zero net pay: no payment for them
		payments = payroll_entry.make_bank_entry()
		self.assertEqual(len(payments), 1)
		self.assertEqual(frappe.db.get_value("HR Payment", payments[0], "party"), employee1)

	def test_component_exclusion_from_accounting_entries(self):
		company = frappe.get_doc("Company", "_Test Company")
		employee = make_employee("exclude_component_test@payroll.com", company=company.name)

		# Create Salary Components
		basic = create_salary_component("Basic", **{"type": "Earning"})
		basic.append("accounts", {"company": company.name, "account": "Salary - _TC"})
		basic.save()

		esi = create_salary_component(
			"ESI", **{"type": "Deduction", "do_not_include_in_total": 1, "do_not_include_in_accounts": 1}
		)
		esi.append("accounts", {"company": company.name, "account": "Salary - _TC"})
		esi.save()

		# Create Salary structure with both components
		make_salary_structure(
			"Test Salary Structure",
			"Monthly",
			employee,
			company=company.name,
			other_details={
				"earnings": [{"salary_component": basic.name, "amount": 20000}],
				"deductions": [
					{
						"salary_component": esi.name,
						"amount": 200,
						"do_not_include_in_total": 1,
						"do_not_include_in_accounts": 1,
					}
				],
			},
		)

		# Create Payroll entry
		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company.default_payroll_payable_account,
			currency=company.default_currency,
			company=company.name,
			cost_center="Main - _TC",
		)

		# Get and verify salary slip & jv
		salary_slip = frappe.get_doc("Salary Slip", {"payroll_entry": payroll_entry.name})

		self.assertAlmostEqual(salary_slip.gross_pay, 20000.0, places=2)

		# Deductions table should include ESI
		self.assertTrue(any(row.salary_component == esi.name for row in salary_slip.deductions))

		# verify ledger entries & accounts
		entries = get_ledger_entries(payroll_entry.name)
		self.assertTrue(entries, "Ledger entries not created")
		self.assertEqual(salary_slip.gross_pay, sum(d.debit for d in entries))

		accounts = [d.account for d in entries]
		self.assertIn("Salary - _TC", accounts)
		self.assertIn(company.default_payroll_payable_account, accounts)
		self.assertNotIn("ESIC Payable - _TC", accounts, "ESIC component wrongly included in ledger")

	def setup_employer_contribution_component(self):
		expense_account = create_account("Employer PF Contribution", "_Test Company", account_type="Expense")
		liability_account = create_account("Employer PF Payable", "_Test Company", account_type="Liability")

		employer_pf = create_salary_component("Test Employer PF", **{"type": "Employer Contribution"})
		employer_pf.accounts = []
		employer_pf.append(
			"accounts",
			{
				"company": "_Test Company",
				"account": expense_account,
				"liability_account": liability_account,
			},
		)
		employer_pf.save()

		return employer_pf, expense_account, liability_account

	def test_employer_contribution_ledger_entries(self):
		company = frappe.get_doc("Company", "_Test Company")
		department = create_department("EC JV Test")
		employee = make_employee(
			"employer_contribution_jv@payroll.com", company=company.name, department=department
		)

		employer_pf, expense_account, liability_account = self.setup_employer_contribution_component()

		make_salary_structure(
			"Test Salary Structure Employer Contribution",
			"Monthly",
			employee,
			company=company.name,
			currency=company.default_currency,
			other_details={
				"employer_contributions": [{"salary_component": employer_pf.name, "amount": 5000}]
			},
		)

		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company.default_payroll_payable_account,
			currency=company.default_currency,
			company=company.name,
			cost_center="Main - _TC",
			department=department,
		)

		entries = get_ledger_entries(payroll_entry.name)
		self.assertEqual(flt(sum(d.debit for d in entries), 2), flt(sum(d.credit for d in entries), 2))

		debit_rows = [d for d in entries if d.account == expense_account]
		self.assertEqual(sum(d.debit for d in debit_rows), 5000)
		self.assertFalse(any(d.credit for d in debit_rows))

		credit_rows = [d for d in entries if d.account == liability_account]
		self.assertEqual(len(credit_rows), 1)
		self.assertEqual(credit_rows[0].credit, 5000)
		self.assertFalse(credit_rows[0].party)

		# employer contributions are not part of the employee's payable
		salary_slip = frappe.get_doc("Salary Slip", {"payroll_entry": payroll_entry.name})
		payable = sum(
			d.credit - d.debit for d in entries if d.account == company.default_payroll_payable_account
		)
		self.assertEqual(flt(payable, 2), flt(salary_slip.net_pay, 2))

		# cancelling the payroll entry reverses the employer contribution entries
		delete_draft_payments(payroll_entry.name)
		payroll_entry.reload()
		payroll_entry.cancel()
		self.assertFalse(get_ledger_entries(payroll_entry.name))
		self.assertTrue(
			frappe.db.exists(
				"HR Ledger Entry",
				{"voucher_no": payroll_entry.name, "account": liability_account, "is_cancelled": 1},
			)
		)

	def test_employer_contribution_liability_for_multiple_employees(self):
		company = frappe.get_doc("Company", "_Test Company")
		department = create_department("EC Tagging Test")
		emp1 = make_employee("ec_jv_tagging1@payroll.com", company=company.name, department=department)
		emp2 = make_employee("ec_jv_tagging2@payroll.com", company=company.name, department=department)

		employer_pf, expense_account, liability_account = self.setup_employer_contribution_component()

		structure = make_salary_structure(
			"Test Salary Structure EC Tagging",
			"Monthly",
			emp1,
			company=company.name,
			currency=company.default_currency,
			other_details={
				"employer_contributions": [{"salary_component": employer_pf.name, "amount": 5000}]
			},
		)
		create_salary_structure_assignment(
			emp2, structure.name, company=company.name, currency=company.default_currency
		)

		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company.default_payroll_payable_account,
			currency=company.default_currency,
			company=company.name,
			cost_center="Main - _TC",
			department=department,
		)

		entries = get_ledger_entries(payroll_entry.name)
		credit_rows = [d for d in entries if d.account == liability_account]
		# the liability is owed to the authority, not the employee: merged and without party
		self.assertEqual(len(credit_rows), 1)
		self.assertFalse(credit_rows[0].party)
		self.assertEqual(credit_rows[0].credit, 10000)

		debit_row = next(d for d in entries if d.account == expense_account)
		self.assertEqual(debit_row.debit, 10000)
		self.assertFalse(debit_row.party)

		# the payable is tagged per employee
		payable_parties = {d.party for d in entries if d.account == company.default_payroll_payable_account}
		self.assertEqual(payable_parties, {emp1, emp2})

	def test_employer_contribution_ledger_balances_with_cost_center_split_rounding(self):
		company = frappe.get_doc("Company", "_Test Company")
		department = create_department("EC Rounding Test")
		employee = make_employee("ec_jv_rounding@payroll.com", company=company.name, department=department)

		employer_pf, expense_account, liability_account = self.setup_employer_contribution_component()

		structure = make_salary_structure(
			"Test Salary Structure EC Rounding",
			"Monthly",
			employee,
			company=company.name,
			currency=company.default_currency,
			other_details={
				"employer_contributions": [{"salary_component": employer_pf.name, "amount": 0.01}]
			},
		)

		ssa = frappe.db.get_value(
			"Salary Structure Assignment",
			{"employee": employee, "salary_structure": structure.name, "docstatus": 1},
			"name",
		)
		ssa_doc = frappe.get_doc("Salary Structure Assignment", ssa)
		ssa_doc.payroll_cost_centers = []
		ssa_doc.append("payroll_cost_centers", {"cost_center": "_Test Cost Center - _TC", "percentage": 50})
		ssa_doc.append("payroll_cost_centers", {"cost_center": "_Test Cost Center 2 - _TC", "percentage": 50})
		ssa_doc.save()

		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = make_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company.default_payroll_payable_account,
			currency=company.default_currency,
			company=company.name,
			cost_center="Main - _TC",
			department=department,
		)

		# 0.005 + 0.005 rounded per row would not equal the 0.01 credit
		entries = get_ledger_entries(payroll_entry.name)
		self.assertEqual(flt(sum(d.debit for d in entries if d.account == expense_account), 2), 0.01)
		self.assertEqual(flt(sum(d.credit for d in entries if d.account == liability_account), 2), 0.01)

	def test_employee_benefits_accruals_in_salary_slip(self):
		"""Test to verify
		- employee flexible benefits of accrual payout methods are fetched into salary slip
		- employee benefit ledger entries are created for each component
		- accrual earning components are excluded from earnings and added to accrued_benefts instead
		- additional salary for accrual component is included in totals and benefit ledger entries are created
		- unclaimed benefits and benefit type of "Accrue and Payout at end of Payroll Perod" are paid out in final month of payroll period
		"""
		from qcs_hrms.payroll.doctype.salary_slip.test_salary_slip import (
			create_salary_slips_for_payroll_period,
			make_payroll_period,
		)

		frappe.db.set_value("Company", "_Test Company", "default_holiday_list", "_Test Holiday List")

		make_payroll_period(company="_Test Company")
		emp = make_employee(
			"test_employee_benefits@salary.com",
			company="_Test Company",
			date_of_joining="2021-01-01",
		)
		payroll_period = frappe.get_last_doc("Payroll Period", filters={"company": "_Test Company"})

		make_salary_structure(
			"Test Benefit Accrual",
			"Monthly",
			company="_Test Company",
			employee=emp,
			payroll_period=payroll_period,
			base=65000,
			include_flexi_benefits=True,
			test_accrual_component=True,
			test_tax=True,
		)

		# Create and submit payroll entry for first month of payroll period
		first_month_start = payroll_period.start_date
		first_month_end = add_months(first_month_start, 1)
		company_doc = frappe.get_doc("Company", "_Test Company")

		payroll_entry = make_payroll_entry(
			start_date=first_month_start,
			end_date=first_month_end,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company="_Test Company",
			cost_center="Main - _TC",
		)
		salary_slip = frappe.get_doc("Salary Slip", {"payroll_entry": payroll_entry.name})

		# Check if employee benefits have been fetched to accrued benefits table
		self.assertTrue(salary_slip.accrued_benefits)
		accrual_payout_methods = [
			"Accrue and payout at end of payroll period",
			"Accrue per cycle, pay only on claim",
		]
		for benefit in salary_slip.accrued_benefits:
			if benefit.salary_component != "Accrued Earnings":
				payout_method = frappe.db.get_value(
					"Salary Component", benefit.salary_component, "payout_method"
				)
				self.assertIn(payout_method, accrual_payout_methods)
			else:
				self.assertEqual(benefit.amount, 1000)

		# Check if employee benefit ledger entries have been created for each component
		for benefit_row in salary_slip.accrued_benefits:
			self.assertTrue(
				frappe.db.exists(
					"Employee Benefit Ledger",
					{"salary_slip": salary_slip.name, "salary_component": benefit_row.salary_component},
				)
			)

		earnings_list = [earning.salary_component for earning in salary_slip.earnings]
		self.assertNotIn(
			"Accrued Earnings", earnings_list
		)  # "Accrued Earnings component should not be in earnings table but in accrued benefits")

		# Check if Employee Benefit Ledger exists for Accrued Earnings Component
		self.assertTrue(
			frappe.db.exists(
				"Employee Benefit Ledger",
				{"salary_slip": salary_slip.name, "salary_component": "Accrued Earnings"},
			)
		)

		# Create additional salary for accrual component for second month of payroll period
		second_month_start = add_months(first_month_start, 1)
		second_month_end = add_months(first_month_start, 2)

		additional_salary = frappe.get_doc(
			{
				"doctype": "Additional Salary",
				"employee": emp,
				"salary_component": "Accrued Earnings",
				"amount": 1000,
				"payroll_date": second_month_end,
				"company": "_Test Company",
				"overwrite_salary_structure_amount": 0,
			}
		)
		additional_salary.insert()
		additional_salary.submit()

		next_month_payroll_entry = make_payroll_entry(
			start_date=second_month_start,
			end_date=second_month_end,
			payable_account=company_doc.default_payroll_payable_account,
			currency=company_doc.default_currency,
			company="_Test Company",
			cost_center="Main - _TC",
		)
		next_salary_slip = frappe.get_doc("Salary Slip", {"payroll_entry": next_month_payroll_entry.name})

		# Payout against accrual component as additional salary is recorded in Employee Benefit Ledger
		self.assertTrue(
			frappe.db.exists(
				"Employee Benefit Ledger",
				{
					"salary_slip": next_salary_slip.name,
					"salary_component": "Accrued Earnings",
					"transaction_type": "Payout",
				},
			)
		)

		frappe.db.delete("Salary Slip", {"employee": emp})
		frappe.db.delete("Employee Benefit Ledger")

		# check if unclaimed benefits and benefit type of "Accrue and Payout at end of Payroll Perod" are paid out in final month of payroll period
		create_salary_slips_for_payroll_period(emp, "Test Benefit Accrual", payroll_period)

		salary_slip = frappe.get_all(
			"Salary Slip", filters={"employee": emp}, order_by="posting_date desc", limit=1, pluck="name"
		)
		salary_slip = frappe.get_doc("Salary Slip", salary_slip[0])
		earnings_components = {earning.salary_component: earning.amount for earning in salary_slip.earnings}

		self.assertEqual(
			earnings_components.get("Internet Reimbursement"),
			12000,
		)
		self.assertEqual(
			earnings_components.get("Mediclaim Allowance"),
			24000,
		)

	def test_status_on_discard(self):
		company = frappe.get_doc("Company", "_Test Company")
		employee = frappe.db.get_value("Employee", {"company": "_Test Company"})
		setup_salary_structure(employee, company)

		dates = get_start_end_dates("Monthly", nowdate())
		payroll_entry = get_payroll_entry(
			start_date=dates.start_date,
			end_date=dates.end_date,
			payable_account=company.default_payroll_payable_account,
			currency=company.default_currency,
			company=company.name,
			cost_center="Main - _TC",
		)
		payroll_entry.discard()
		payroll_entry.reload()
		self.assertEqual(payroll_entry.status, "Cancelled")

	def test_set_match_conditions_maps_permission_doctype_to_employee_field(self):
		employee = frappe.qb.DocType("Employee")
		query = frappe.qb.from_(employee).select(employee.name)

		with patch(
			"qcs_hrms.payroll.doctype.payroll_entry.payroll_entry.get_match_cond",
			return_value=[{"Employee Grade": ["A"], "Unknown Doctype": ["Ignored"]}],
		):
			query = set_match_conditions(query, employee)

		query_sql = query.get_sql()
		self.assertIn("grade", query_sql)
		self.assertIn("'A'", query_sql)
		self.assertNotIn("Employee Grade", query_sql)
		self.assertNotIn("Unknown Doctype", query_sql)


def get_payroll_entry(**args):
	args = frappe._dict(args)

	payroll_entry: PayrollEntry = frappe.new_doc("Payroll Entry")
	payroll_entry.company = args.company or "_Test Company"
	payroll_entry.start_date = args.start_date or "2016-11-01"
	payroll_entry.end_date = args.end_date or "2016-11-30"
	payroll_entry.payment_account = get_payment_account()
	payroll_entry.posting_date = nowdate()
	payroll_entry.payroll_frequency = "Monthly"
	payroll_entry.branch = args.branch or None
	payroll_entry.department = args.department or None
	payroll_entry.payroll_payable_account = args.payable_account
	payroll_entry.currency = args.currency
	payroll_entry.exchange_rate = args.exchange_rate or 1

	if args.cost_center:
		payroll_entry.cost_center = args.cost_center

	if args.payment_account:
		payroll_entry.payment_account = args.payment_account

	payroll_entry.fill_employee_details()
	payroll_entry.insert()

	return payroll_entry


def make_payroll_entry(**args):
	payroll_entry = get_payroll_entry(**args)
	payroll_entry.submit()
	payroll_entry.submit_salary_slips()
	# get_sal_slip_list(ss_status=1) excludes slips whose accrual is posted, i.e. all of them by now
	if frappe.db.exists("Salary Slip", {"payroll_entry": payroll_entry.name, "docstatus": 1}):
		payroll_entry.make_bank_entry()

	return payroll_entry


def get_payment_account(company="_Test Company"):
	create_default_accounts(company)
	return frappe.get_value(
		"HR Ledger Account",
		{"account_type": "Cash", "company": company, "disabled": 0},
		"name",
	)


def setup_salary_structure(employee, company_doc, currency=None, salary_structure=None):
	for data in frappe.get_all("Salary Component", pluck="name"):
		if not frappe.db.get_value(
			"Salary Component Account", {"parent": data, "company": company_doc.name}, "name"
		):
			set_salary_component_account(data)

	return make_salary_structure(
		salary_structure or "_Test Salary Structure",
		"Monthly",
		employee,
		company=company_doc.name,
		currency=(currency or company_doc.default_currency),
	)


def create_assignments_with_cost_centers(employee1, employee2):
	company = frappe.get_doc("Company", "_Test Company")
	setup_salary_structure(employee1, company)
	ss = setup_salary_structure(employee2, company, salary_structure="_Test Salary Structure 2")

	# update cost centers in salary structure assignment for employee2
	ssa = frappe.db.get_value(
		"Salary Structure Assignment",
		{"employee": employee2, "salary_structure": ss.name, "docstatus": 1},
		"name",
	)

	ssa_doc = frappe.get_doc("Salary Structure Assignment", ssa)
	ssa_doc.payroll_cost_centers = []
	ssa_doc.append("payroll_cost_centers", {"cost_center": "_Test Cost Center - _TC", "percentage": 60})
	ssa_doc.append("payroll_cost_centers", {"cost_center": "_Test Cost Center 2 - _TC", "percentage": 40})
	ssa_doc.save()


def submit_bank_entry(payroll_entry_id):
	"""Submit the draft HR Payments created by Make Bank Entry"""
	for payment in get_linked_payments(payroll_entry_id, docstatus=0):
		payment_doc = frappe.get_doc("HR Payment", payment)
		payment_doc.reference_no = "123456"
		payment_doc.reference_date = nowdate()
		payment_doc.submit()


def cancel_linked_payments(payroll_entry_id):
	for payment in get_linked_payments(payroll_entry_id, docstatus=1):
		frappe.get_doc("HR Payment", payment).cancel()


def delete_draft_payments(payroll_entry_id):
	for payment in get_linked_payments(payroll_entry_id, docstatus=0):
		frappe.delete_doc("HR Payment", payment, force=True)


def get_linked_payments(payroll_entry_id, docstatus=None) -> list[str]:
	"""HR Payments referencing the salary slips of a Payroll Entry"""
	Payment = frappe.qb.DocType("HR Payment")
	Reference = frappe.qb.DocType("HR Payment Reference")
	SalarySlip = frappe.qb.DocType("Salary Slip")

	query = (
		frappe.qb.from_(Payment)
		.join(Reference)
		.on((Reference.parent == Payment.name) & (Reference.parenttype == "HR Payment"))
		.join(SalarySlip)
		.on((Reference.reference_doctype == "Salary Slip") & (Reference.reference_name == SalarySlip.name))
		.select(Payment.name)
		.distinct()
		.where(SalarySlip.payroll_entry == payroll_entry_id)
		.orderby(Payment.name)
	)
	if docstatus is not None:
		query = query.where(Payment.docstatus == docstatus)

	return query.run(pluck=True)


def get_ledger_entries(payroll_entry_id, is_cancelled=0) -> list[dict]:
	"""Accrual HR Ledger Entries posted against a Payroll Entry"""
	return frappe.get_all(
		"HR Ledger Entry",
		filters={"voucher_type": "Payroll Entry", "voucher_no": payroll_entry_id, "is_cancelled": is_cancelled},
		fields=[
			"account",
			"cost_center",
			"debit",
			"credit",
			"party_type",
			"party",
			"against_voucher_type",
			"against_voucher",
		],
		order_by="account, cost_center, party",
	)
