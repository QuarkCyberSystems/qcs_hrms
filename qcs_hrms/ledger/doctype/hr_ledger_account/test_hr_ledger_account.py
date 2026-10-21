# Copyright (c) 2026, Quark Cyber System FZC and Contributors
# See license.txt

import frappe

import qcs_hrms
from qcs_hrms.ledger.setup import create_default_accounts
from qcs_hrms.tests.utils import HRMSTestSuite


def create_ledger_account(
	account_name: str,
	company: str = "_Test Company",
	account_type: str | None = None,
	account_currency: str | None = None,
	account_number: str | None = None,
) -> str:
	"""Return the HR Ledger Account `account_name` for the company, creating it when missing."""
	name = frappe.db.get_value("HR Ledger Account", {"account_name": account_name, "company": company})
	if name:
		return name

	doc = frappe.get_doc(
		{
			"doctype": "HR Ledger Account",
			"account_name": account_name,
			"company": company,
			"account_type": account_type,
			"account_currency": account_currency or qcs_hrms.get_company_currency(company),
			"account_number": account_number,
		}
	).insert(ignore_permissions=True)
	return doc.name


class TestHRLedgerAccount(HRMSTestSuite):
	def test_autoname_uses_company_abbr(self):
		name = create_ledger_account("_Test Ledger Naming", account_type="Expense")
		abbr = frappe.get_cached_value("Company", "_Test Company", "abbr")
		self.assertEqual(name, f"_Test Ledger Naming - {abbr}")
		self.assertEqual(
			frappe.db.get_value("HR Ledger Account", name, "account_currency"),
			qcs_hrms.get_company_currency("_Test Company"),
		)

	def test_duplicate_account_number(self):
		create_ledger_account("_Test Numbered 1", account_number="9001")
		doc = frappe.get_doc(
			{
				"doctype": "HR Ledger Account",
				"account_name": "_Test Numbered 2",
				"company": "_Test Company",
				"account_number": "9001",
			}
		)
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_create_default_accounts_is_idempotent(self):
		first = create_default_accounts("_Test Company")
		second = create_default_accounts("_Test Company")
		self.assertEqual(first, second)
		self.assertEqual(
			frappe.db.get_value("HR Ledger Account", first["Employee Advances"], "account_type"), "Receivable"
		)
		for fieldname in ("default_payroll_payable_account", "default_bank_account", "round_off_account"):
			self.assertTrue(frappe.db.get_value("Company", "_Test Company", fieldname))
		self.assertTrue(frappe.db.exists("HR Ledger Account", first["Payroll Payable"]))
