# Copyright (c) 2026, Quark Cyber System FZC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

import qcs_hrms


class HRLedgerAccount(Document):
	def autoname(self):
		abbr = frappe.get_cached_value("Company", self.company, "abbr")
		self.name = f"{self.account_name.strip()} - {abbr}" if abbr else self.account_name.strip()

	def validate(self):
		if not self.account_currency:
			self.account_currency = qcs_hrms.get_company_currency(self.company)
		self.validate_account_number()
		self.validate_currency_change()

	def validate_account_number(self):
		if not self.account_number:
			return
		duplicate = frappe.db.get_value(
			"HR Ledger Account",
			{"account_number": self.account_number, "company": self.company, "name": ("!=", self.name)},
		)
		if duplicate:
			frappe.throw(
				_("Account Number {0} is already used in account {1}").format(
					frappe.bold(self.account_number), frappe.bold(duplicate)
				)
			)

	def validate_currency_change(self):
		if self.is_new() or not self.has_value_changed("account_currency"):
			return
		if frappe.db.exists("HR Ledger Entry", {"account": self.name}):
			frappe.throw(_("Currency can not be changed after ledger entries are posted to this account"))

	def on_trash(self):
		if frappe.db.exists("HR Ledger Entry", {"account": self.name}):
			frappe.throw(_("Account with existing ledger entries can not be deleted. Disable it instead."))
