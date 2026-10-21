# Copyright (c) 2026, Quark Cyber System FZC
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, formatdate, getdate, nowdate


class CurrencyExchange(Document):
	def autoname(self):
		if not self.date:
			self.date = nowdate()

		purpose = "Selling-Buying"
		if not cint(self.for_buying) and cint(self.for_selling):
			purpose = "Selling"
		elif cint(self.for_buying) and not cint(self.for_selling):
			purpose = "Buying"

		self.name = f"{formatdate(getdate(self.date), 'yyyy-MM-dd')}-{self.from_currency}-{self.to_currency}-{purpose}"

	def validate(self):
		self.validate_value("exchange_rate", ">", 0)

		if self.from_currency == self.to_currency:
			frappe.throw(_("From Currency and To Currency cannot be same"))

		if not cint(self.for_buying) and not cint(self.for_selling):
			frappe.throw(_("Currency Exchange must be applicable for Buying or for Selling."))
