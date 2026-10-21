# Copyright (c) 2026, Quark Cyber System FZC and Contributors
# See license.txt

import frappe

from qcs_hrms.api import ledger as api
from qcs_hrms.hr.doctype.expense_claim.test_expense_claim import (
	get_expense_account,
	get_payable_account,
	make_expense_claim,
)
from qcs_hrms.ledger.setup import create_default_accounts
from qcs_hrms.organization.doctype.employee.test_employee import make_employee
from qcs_hrms.tests.utils import HRMSTestSuite

COMPANY = "_Test Company"


class TestLedgerAPI(HRMSTestSuite):
	def setUp(self):
		frappe.set_user("Administrator")
		create_default_accounts(COMPANY)
		self.employee = make_employee("test_ledger_api@example.com", COMPANY)
		self.claim = make_expense_claim(
			get_payable_account(COMPANY), 800, 800, COMPANY, get_expense_account(), employee=self.employee
		)

	def get_claim_voucher(self, **kwargs):
		result = api.get_vouchers(company=COMPANY, voucher_type="Expense Claim", page_length=1000, **kwargs)
		return next((v for v in result["vouchers"] if v.voucher_no == self.claim.name), None)

	def test_get_accounts(self):
		accounts = api.get_accounts(company=COMPANY)
		self.assertIn(get_payable_account(COMPANY), [a.name for a in accounts])

	def test_vouchers_are_balanced_and_sync_flow(self):
		voucher = self.get_claim_voucher()
		self.assertTrue(voucher)
		self.assertTrue(voucher.is_balanced)
		self.assertEqual(voucher.total_debit, 800)
		self.assertEqual(voucher.sync_status, "Pending")
		self.assertEqual(len(voucher.lines), 2)

		result = api.mark_synced(vouchers=[voucher.batch_id], external_reference="EXT-1")
		self.assertEqual(result["updated"], 2)
		self.assertIsNone(self.get_claim_voucher())

		synced = self.get_claim_voucher(sync_status="Synced")
		self.assertEqual(synced.external_reference, "EXT-1")

		# cancelling a synced document produces a pending reversal voucher
		self.claim.reload()
		self.claim.cancel()
		reversal = self.get_claim_voucher()
		self.assertTrue(reversal.is_reversal)
		self.assertTrue(reversal.is_balanced)

	def test_mark_sync_failed(self):
		voucher = self.get_claim_voucher()
		entries = [line.name for line in voucher.lines]
		api.mark_sync_failed(entries=entries, error="Account not mapped")
		failed = self.get_claim_voucher(sync_status="Failed")
		self.assertEqual({line.sync_error for line in failed.lines}, {"Account not mapped"})

	def test_get_ledger_entries_paging(self):
		page = api.get_ledger_entries(company=COMPANY, voucher_no=self.claim.name, page_length=1)
		self.assertEqual(len(page["entries"]), 1)
		self.assertTrue(page["has_more"])
		page = api.get_ledger_entries(company=COMPANY, voucher_no=self.claim.name, start=1, page_length=1)
		self.assertFalse(page["has_more"])

	def test_outstanding_documents_and_record_payment(self):
		documents = api.get_outstanding_documents(self.employee, COMPANY)
		claim_row = next(d for d in documents if d["reference_name"] == self.claim.name)
		self.assertEqual(claim_row["outstanding_amount"], 800)
		self.assertEqual(claim_row["payment_type"], "Pay")

		result = api.record_payment(
			reference_doctype="Expense Claim",
			reference_name=self.claim.name,
			amount=800,
			reference_no="BANK-REF-1",
		)
		self.assertEqual(result["docstatus"], 1)
		self.assertEqual(len(result["ledger_entries"]), 2)

		self.claim.reload()
		self.assertEqual(self.claim.status, "Paid")
		balance = api.get_party_balance(self.employee, COMPANY, account=self.claim.payable_account)
		self.assertEqual(balance["balance"], 0)

	def test_invalid_sync_status_rejected(self):
		self.assertRaises(frappe.ValidationError, api.get_ledger_entries, sync_status="Unknown")
