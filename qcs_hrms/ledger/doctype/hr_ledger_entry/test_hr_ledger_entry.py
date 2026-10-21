# Copyright (c) 2026, Quark Cyber System FZC and Contributors
# See license.txt

import frappe
from frappe.utils import nowdate

from qcs_hrms.ledger.doctype.hr_ledger_account.test_hr_ledger_account import create_ledger_account
from qcs_hrms.ledger.setup import create_default_accounts
from qcs_hrms.ledger.utils import (
	LedgerImbalanceError,
	get_party_balance,
	get_voucher_balance,
	make_ledger_entries,
	make_reverse_ledger_entries,
	mark_entries_synced,
)
from qcs_hrms.organization.doctype.employee.test_employee import make_employee
from qcs_hrms.tests.utils import HRMSTestSuite

COMPANY = "_Test Company"


def get_entries(voucher_type, voucher_no, **filters):
	return frappe.get_all(
		"HR Ledger Entry",
		filters={"voucher_type": voucher_type, "voucher_no": voucher_no, **filters},
		fields=["*"],
		order_by="creation asc, name asc",
	)


class TestHRLedgerEntry(HRMSTestSuite):
	def setUp(self):
		self.accounts = create_default_accounts(COMPANY)
		self.expense = create_ledger_account("_Test Ledger Expense", COMPANY, "Expense")
		self.payable = self.accounts["Payroll Payable"]
		self.employee = make_employee("test_ledger_entry@example.com", COMPANY)
		# any existing document works as a voucher; use the employee record
		self.voucher = ("Employee", self.employee)

	def make_entries(self, debit=100, credit=100, **kwargs):
		voucher_type, voucher_no = self.voucher
		common = {
			"company": COMPANY,
			"posting_date": nowdate(),
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
		}
		return make_ledger_entries(
			[
				{**common, "account": self.expense, "debit": debit},
				{
					**common,
					"account": self.payable,
					"credit": credit,
					"party_type": "Employee",
					"party": self.employee,
					"against_voucher_type": voucher_type,
					"against_voucher": voucher_no,
				},
			],
			**kwargs,
		)

	def test_balanced_posting(self):
		names = self.make_entries()
		self.assertEqual(len(names), 2)

		entries = get_entries(*self.voucher)
		self.assertEqual(sum(e.debit for e in entries), sum(e.credit for e in entries))
		self.assertEqual(len({e.batch_id for e in entries}), 1)
		self.assertTrue(all(e.sync_status == "Pending" and not e.is_cancelled for e in entries))
		self.assertEqual(get_party_balance("Employee", self.employee, COMPANY), -100)
		self.assertEqual(get_voucher_balance(*self.voucher, exclude_own_entries=False), -100)

	def test_unbalanced_posting_is_rejected(self):
		self.assertRaises(LedgerImbalanceError, self.make_entries, debit=100, credit=90)
		self.assertFalse(get_entries(*self.voucher))

	def test_small_difference_is_rounded_off(self):
		self.make_entries(debit=100, credit=100.01)
		entries = get_entries(*self.voucher)
		round_off = [e for e in entries if e.account == self.accounts["Round Off"]]
		self.assertEqual(len(round_off), 1)
		self.assertEqual(round_off[0].debit, 0.01)

	def test_similar_entries_are_merged(self):
		voucher_type, voucher_no = self.voucher
		common = {"company": COMPANY, "voucher_type": voucher_type, "voucher_no": voucher_no}
		make_ledger_entries(
			[
				{**common, "account": self.expense, "debit": 60},
				{**common, "account": self.expense, "debit": 40},
				{**common, "account": self.payable, "credit": 100},
			]
		)
		entries = get_entries(*self.voucher)
		self.assertEqual(len(entries), 2)
		self.assertEqual([e.debit for e in entries if e.account == self.expense], [100])

	def test_cancellation_posts_reversal(self):
		self.make_entries()
		originals = get_entries(*self.voucher)
		# pretend the first posting already reached the external system
		mark_entries_synced([e.name for e in originals], "JV-0001")

		make_reverse_ledger_entries(*self.voucher)

		entries = get_entries(*self.voucher)
		self.assertEqual(len(entries), 4)
		self.assertTrue(all(e.is_cancelled for e in entries))

		reversals = [e for e in entries if e.is_reversal]
		self.assertEqual(len(reversals), 2)
		self.assertEqual(len({e.batch_id for e in reversals}), 1)
		for reversal in reversals:
			original = next(e for e in originals if e.name == reversal.reversal_of)
			self.assertEqual(reversal.debit, original.credit)
			self.assertEqual(reversal.credit, original.debit)
			# synced originals need their reversal pushed to the external system
			self.assertEqual(reversal.sync_status, "Pending")

		self.assertEqual(get_party_balance("Employee", self.employee, COMPANY), 0)
		# reversing again is a no-op
		self.assertEqual(make_reverse_ledger_entries(*self.voucher), [])

	def test_cancellation_of_unsynced_entries_is_skipped(self):
		self.make_entries()
		make_reverse_ledger_entries(*self.voucher)
		entries = get_entries(*self.voucher)
		self.assertTrue(all(e.sync_status == "Skipped" for e in entries))

	def test_entries_are_immutable(self):
		self.make_entries()
		entry = frappe.get_doc("HR Ledger Entry", get_entries(*self.voucher)[0].name)
		entry.remarks = "changed"
		self.assertRaises(frappe.PermissionError, entry.save)

		manual = frappe.get_doc(
			{"doctype": "HR Ledger Entry", "company": COMPANY, "account": self.expense, "debit": 1}
		)
		self.assertRaises(frappe.PermissionError, manual.insert, ignore_permissions=True)
