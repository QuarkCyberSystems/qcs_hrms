# Copyright (c) 2026, Quark Cyber System FZC and Contributors
# See license.txt

import frappe
from frappe.utils import nowdate

from qcs_hrms.hr.doctype.employee_advance.test_employee_advance import make_employee_advance
from qcs_hrms.hr.doctype.expense_claim.test_expense_claim import (
	get_expense_account,
	get_payable_account,
	make_expense_claim,
)
from qcs_hrms.ledger.doctype.hr_payment.hr_payment import HRPaymentAllocationError
from qcs_hrms.ledger.setup import create_default_accounts
from qcs_hrms.ledger.utils import get_outstanding_amount, get_party_balance, make_hr_payment
from qcs_hrms.organization.doctype.employee.test_employee import make_employee
from qcs_hrms.tests.utils import HRMSTestSuite

COMPANY = "_Test Company"


class TestHRPayment(HRMSTestSuite):
	def setUp(self):
		self.accounts = create_default_accounts(COMPANY)
		self.employee = make_employee("test_hr_payment@example.com", COMPANY)

	def make_claim(self, amount=1000):
		return make_expense_claim(
			get_payable_account(COMPANY), amount, amount, COMPANY, get_expense_account(), employee=self.employee
		)

	def get_entries(self, payment, **filters):
		return frappe.get_all(
			"HR Ledger Entry",
			filters={"voucher_type": "HR Payment", "voucher_no": payment.name, **filters},
			fields=["*"],
		)

	def test_pay_posts_balanced_entries_and_allocates(self):
		claim = self.make_claim()
		payment = make_hr_payment("Expense Claim", claim.name, "Pay", 600)
		self.assertEqual(payment.paid_from, self.accounts["Bank"])
		self.assertEqual(payment.paid_to, claim.payable_account)
		payment.insert()
		self.assertEqual(payment.references[0].outstanding_amount, 1000)
		payment.submit()

		entries = self.get_entries(payment)
		self.assertEqual(len(entries), 2)
		self.assertEqual(sum(e.debit for e in entries), 600)
		self.assertEqual(sum(e.credit for e in entries), 600)
		party_entry = next(e for e in entries if e.account == claim.payable_account)
		self.assertEqual(party_entry.debit, 600)
		self.assertEqual(party_entry.party, self.employee)
		self.assertEqual(
			(party_entry.against_voucher_type, party_entry.against_voucher), ("Expense Claim", claim.name)
		)
		bank_entry = next(e for e in entries if e.account == self.accounts["Bank"])
		self.assertEqual(bank_entry.credit, 600)

		claim.reload()
		self.assertEqual(claim.total_amount_reimbursed, 600)
		self.assertEqual(claim.status, "Partially Paid")
		self.assertEqual(get_outstanding_amount("Expense Claim", claim.name, "Pay"), 400)
		self.assertEqual(get_party_balance("Employee", self.employee, COMPANY, claim.payable_account), -400)

	def test_allocation_cannot_exceed_outstanding(self):
		claim = self.make_claim(500)
		payment = make_hr_payment("Expense Claim", claim.name, "Pay")
		payment.references[0].allocated_amount = 600
		payment.paid_amount = 600
		self.assertRaises(HRPaymentAllocationError, payment.insert)

	def test_total_allocation_cannot_exceed_paid_amount(self):
		claim = self.make_claim(500)
		payment = make_hr_payment("Expense Claim", claim.name, "Pay")
		payment.paid_amount = 400
		self.assertRaises(HRPaymentAllocationError, payment.insert)

	def test_unallocated_amount_is_posted_without_reference(self):
		claim = self.make_claim(500)
		payment = make_hr_payment("Expense Claim", claim.name, "Pay")
		payment.paid_amount = 700
		payment.insert()
		payment.submit()
		self.assertEqual(payment.unallocated_amount, 200)

		party_entries = self.get_entries(payment, party=self.employee)
		self.assertEqual(sorted(e.debit for e in party_entries), [200, 500])
		self.assertEqual([e.against_voucher for e in party_entries if e.debit == 200], [None])

	def test_cancellation_reverses_entries_and_references(self):
		claim = self.make_claim()
		payment = make_hr_payment("Expense Claim", claim.name, "Pay")
		payment.insert()
		payment.submit()
		claim.reload()
		self.assertEqual(claim.status, "Paid")

		payment.cancel()
		self.assertEqual(payment.status, "Cancelled")
		self.assertFalse(self.get_entries(payment, is_cancelled=0))
		self.assertEqual(len(self.get_entries(payment, is_reversal=1)), 2)

		claim.reload()
		self.assertEqual(claim.status, "Unpaid")
		self.assertEqual(claim.total_amount_reimbursed, 0)
		self.assertEqual(get_outstanding_amount("Expense Claim", claim.name, "Pay"), 1000)

	def test_reference_of_other_employee_is_rejected(self):
		other = make_employee("test_hr_payment_other@example.com", COMPANY)
		claim = self.make_claim()
		payment = make_hr_payment("Expense Claim", claim.name, "Pay")
		payment.party = other
		self.assertRaises(frappe.ValidationError, payment.insert)

	def test_receive_against_employee_advance(self):
		advance = make_employee_advance(self.employee)
		pay = make_hr_payment("Employee Advance", advance.name, "Pay")
		pay.insert()
		pay.submit()

		advance.reload()
		self.assertEqual(advance.status, "Paid")
		self.assertEqual(get_outstanding_amount("Employee Advance", advance.name, "Receive"), 1000)

		receive = make_hr_payment("Employee Advance", advance.name, "Receive", 1000)
		self.assertEqual(receive.paid_from, advance.advance_account)
		receive.reference_date = nowdate()
		receive.insert()
		receive.submit()

		entries = self.get_entries(receive)
		party_entry = next(e for e in entries if e.account == advance.advance_account)
		self.assertEqual(party_entry.credit, 1000)

		advance.reload()
		self.assertEqual(advance.return_amount, 1000)
		self.assertEqual(advance.status, "Returned")
		self.assertEqual(get_party_balance("Employee", self.employee, COMPANY, advance.advance_account), 0)

	def test_handler_hook_is_called(self):
		calls = []

		def handler(payment_doc, reference_row, cancel):
			calls.append((payment_doc.name, reference_row.reference_name, cancel))

		claim = self.make_claim()
		payment = make_hr_payment("Expense Claim", claim.name, "Pay")
		payment.insert()

		frappe.local.test_hr_payment_handler = handler
		hooks = {"Expense Claim": ["qcs_hrms.ledger.doctype.hr_payment.test_hr_payment.call_test_handler"]}
		original = frappe.get_hooks

		def get_hooks(hook=None, *args, **kwargs):
			if hook == "qcs_hrms_payment_reference_handlers":
				return hooks
			return original(hook, *args, **kwargs)

		frappe.get_hooks = get_hooks
		try:
			payment.submit()
			payment.cancel()
		finally:
			frappe.get_hooks = original
			del frappe.local.test_hr_payment_handler

		self.assertEqual(calls, [(payment.name, claim.name, False), (payment.name, claim.name, True)])


def call_test_handler(payment_doc, reference_row, cancel):
	frappe.local.test_hr_payment_handler(payment_doc, reference_row, cancel)
