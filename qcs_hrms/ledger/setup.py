# Copyright (c) 2026, Quark Cyber System FZC and contributors
# For license information, please see license.txt

import frappe

import qcs_hrms

# (account_name, account_type, Company default fieldname or None)
DEFAULT_ACCOUNTS = (
	("Payroll Payable", "Payable", "default_payroll_payable_account"),
	("Expense Claims Payable", "Payable", "default_expense_claim_payable_account"),
	("Employee Advances", "Receivable", "default_employee_advance_account"),
	("Salaries", "Expense", "default_salary_expense_account"),
	("Employee Expenses", "Expense", None),
	("Bank", "Bank", "default_bank_account"),
	("Cash", "Cash", "default_cash_account"),
	("Round Off", "Round Off", "round_off_account"),
)


def create_default_accounts(company: str) -> dict:
	"""Create the minimal HR ledger accounts for a company and fill empty Company defaults.

	Idempotent: existing accounts (matched by name and company) are reused.
	Returns {account_name: HR Ledger Account name}.
	"""
	if not frappe.db.exists("Company", company):
		return {}

	currency = qcs_hrms.get_company_currency(company)
	company_meta = frappe.get_meta("Company")
	accounts, defaults = {}, {}

	for account_name, account_type, default_field in DEFAULT_ACCOUNTS:
		name = frappe.db.get_value(
			"HR Ledger Account", {"account_name": account_name, "company": company}, "name"
		)
		if not name:
			doc = frappe.get_doc(
				{
					"doctype": "HR Ledger Account",
					"account_name": account_name,
					"company": company,
					"account_type": account_type,
					"account_currency": currency,
				}
			)
			doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
			name = doc.name
		accounts[account_name] = name

		if default_field and company_meta.has_field(default_field):
			if not frappe.db.get_value("Company", company, default_field):
				defaults[default_field] = name

	if defaults:
		frappe.db.set_value("Company", company, defaults)
		frappe.clear_document_cache("Company", company)

	return accounts
