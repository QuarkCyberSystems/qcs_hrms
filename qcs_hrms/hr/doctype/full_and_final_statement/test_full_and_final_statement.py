# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
from frappe.utils import add_days, now_datetime, today

from qcs_hrms.hr.doctype.full_and_final_statement.full_and_final_statement import make_payment
from qcs_hrms.ledger.doctype.hr_ledger_account.test_hr_ledger_account import create_ledger_account
from qcs_hrms.ledger.setup import create_default_accounts
from qcs_hrms.organization.doctype.employee.test_employee import make_employee
from qcs_hrms.tests.utils import HRMSTestSuite


class TestFullandFinalStatement(HRMSTestSuite):
	def setUp(self):
		self.setup_fnf()

	def setup_fnf(self):
		self.accounts = create_default_accounts("_Test Company")
		self.employee = make_employee(
			"test_fnf@example.com", company="_Test Company", relieving_date=add_days(today(), 30)
		)
		self.fnf = create_full_and_final_statement(self.employee)
		add_allocated_asset(self.fnf)

	def test_check_bootstraped_data(self):
		payables_bootstraped_component = [
			"Gratuity",
			"Expense Claim",
			"Bonus",
			"Leave Encashment",
		]

		receivable_bootstraped_component = self.fnf.get_receivable_component()

		# checking payables and receivables bootstraped value
		self.assertEqual([payable.component for payable in self.fnf.payables], payables_bootstraped_component)
		self.assertEqual(
			[receivable.component for receivable in self.fnf.receivables], receivable_bootstraped_component
		)

		# allocated assets are entered manually
		self.assertEqual([asset.asset_name for asset in self.fnf.assets_allocated], ["Macbook Pro"])

	def test_asset_cost(self):
		self.fnf.receivables[0].amount = 50000

		self.fnf.assets_allocated[0].action = "Recover Cost"
		self.fnf.save()

		self.assertEqual(self.fnf.assets_allocated[0].actual_cost, 100000.0)
		self.assertEqual(self.fnf.assets_allocated[0].cost, 100000.0)
		self.assertEqual(self.fnf.total_asset_recovery_cost, 100000.0)
		self.assertEqual(self.fnf.total_receivable_amount, 150000.0)

	def test_settlement_ledger_entries_and_payment(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			make_employee_advance,
			make_payment_entry,
		)

		advance = make_employee_advance(self.employee, {"advance_amount": 50000})
		make_payment_entry(advance)

		self.fnf.get_outstanding_statements()
		self.fnf.assets_allocated[0].action = "Recover Cost"
		self.fnf.assets_allocated[0].account = create_ledger_account("Asset Recovery", "_Test Company", "Income")
		for row in self.fnf.payables + self.fnf.receivables:
			row.status = "Settled"
		self.fnf.save()
		self.assertEqual(self.fnf.net_payable_amount, -150000.0)
		self.fnf.submit()

		entries = frappe.get_all(
			"HR Ledger Entry",
			filters={"voucher_type": self.fnf.doctype, "voucher_no": self.fnf.name, "is_cancelled": 0},
			fields=["account", "debit", "credit", "against_voucher"],
		)
		advance_entry = next(e for e in entries if e.against_voucher == advance.name)
		self.assertEqual(advance_entry.credit, 50000.0)
		settlement = next(e for e in entries if e.against_voucher == self.fnf.name)
		self.assertEqual(settlement.account, self.fnf.payable_account)
		self.assertEqual(settlement.debit, 150000.0)
		self.assertEqual(sum(e.debit for e in entries), sum(e.credit for e in entries))

		advance.reload()
		self.assertEqual(advance.return_amount, advance.paid_amount)
		self.assertEqual(advance.status, "Returned")

		# employee owes the company: settled by an HR Payment of type Receive
		self.fnf.reload()
		self.assertEqual(self.fnf.status, "Unpaid")
		payment = make_payment(self.fnf.name)
		self.assertEqual(payment.payment_type, "Receive")
		self.assertEqual(payment.paid_amount, 150000.0)
		payment.insert()
		payment.submit()

		self.fnf.reload()
		self.assertEqual(self.fnf.status, "Paid")

		payment.cancel()
		self.fnf.reload()
		self.assertEqual(self.fnf.status, "Unpaid")

		self.fnf.cancel()
		advance.reload()
		self.assertEqual(advance.return_amount, 0)
		self.assertEqual(advance.status, "Paid")

	def test_employee_advance_added_after_bootstrap(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			make_employee_advance,
			make_payment_entry,
		)

		# placeholder rows already exist from bootstrap
		self.assertTrue(any(row.component == "Employee Advance" for row in self.fnf.receivables))

		advance = make_employee_advance(self.employee)
		make_payment_entry(advance)
		advance.reload()

		self.fnf.get_outstanding_statements()
		advance_rows = [row for row in self.fnf.receivables if row.component == "Employee Advance"]
		self.assertEqual([row.reference_document for row in advance_rows], [advance.name])
		self.assertEqual(advance_rows[0].amount, advance.paid_amount)

		# calling again neither duplicates the advance nor re-adds the placeholder
		self.fnf.get_outstanding_statements()
		advance_rows = [row for row in self.fnf.receivables if row.component == "Employee Advance"]
		self.assertEqual([row.reference_document for row in advance_rows], [advance.name])

	def test_employee_advance_row_refreshed_to_current_balance(self):
		from qcs_hrms.hr.doctype.employee_advance.employee_advance import make_return_entry
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			make_employee_advance,
			make_payment_entry,
		)

		advance = make_employee_advance(self.employee)
		make_payment_entry(advance)
		advance.reload()
		self.fnf.get_outstanding_statements()
		advance_row = next(row for row in self.fnf.receivables if row.reference_document == advance.name)
		self.assertEqual(advance_row.amount, advance.paid_amount)

		# a partial return after the row was created reduces the outstanding balance
		return_entry = frappe.get_doc(
			make_return_entry(
				employee=advance.employee,
				company=advance.company,
				employee_advance_name=advance.name,
				return_amount=300,
				advance_account=advance.advance_account,
				mode_of_payment=advance.mode_of_payment,
				currency=advance.currency,
			)
		)
		return_entry.insert()
		return_entry.submit()
		advance.reload()
		self.assertEqual(advance.return_amount, 300)

		self.fnf.get_outstanding_statements()
		advance_row = next(row for row in self.fnf.receivables if row.reference_document == advance.name)
		self.assertEqual(advance_row.amount, advance.paid_amount - 300)

		# a row with a blank status is refreshed like an unsettled one
		advance_row.status = None
		advance_row.amount = 100
		self.fnf.get_outstanding_statements()
		advance_row = next(row for row in self.fnf.receivables if row.reference_document == advance.name)
		self.assertEqual(advance_row.amount, advance.paid_amount - 300)

		# a row the user has already settled is left as it is
		advance_row.status = "Settled"
		advance_row.amount = 100
		self.fnf.get_outstanding_statements()
		advance_row = next(row for row in self.fnf.receivables if row.reference_document == advance.name)
		self.assertEqual(advance_row.amount, 100)

	def test_employee_advance_rows_follow_employee_change(self):
		from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import (
			make_employee_advance,
			make_payment_entry,
		)

		advance = make_employee_advance(self.employee)
		make_payment_entry(advance)
		self.fnf.get_outstanding_statements()
		self.assertIn(advance.name, [row.reference_document for row in self.fnf.receivables])

		other_employee = make_employee(
			"test_fnf_other@example.com", company="_Test Company", relieving_date=add_days(today(), 30)
		)
		self.fnf.employee = other_employee
		self.fnf.relieving_date = add_days(today(), 30)
		self.fnf.get_outstanding_statements()

		advance_rows = [row for row in self.fnf.receivables if row.component == "Employee Advance"]
		self.assertEqual(len(advance_rows), 1)
		self.assertFalse(advance_rows[0].reference_document)

	def test_status_on_discard(self):
		self.fnf.discard()
		self.fnf.reload()
		self.assertEqual(self.fnf.status, "Cancelled")


def create_full_and_final_statement(employee):
	fnf = frappe.new_doc("Full and Final Statement")
	fnf.employee = employee
	fnf.transaction_date = today()
	fnf.save()
	return fnf


def add_allocated_asset(fnf, cost=100000.0):
	fnf.append(
		"assets_allocated",
		{
			"asset_name": "Macbook Pro",
			"date": now_datetime(),
			"actual_cost": cost,
			"cost": cost,
			"action": "Return",
			"status": "Owned",
		},
	)
	fnf.save()
	return fnf
