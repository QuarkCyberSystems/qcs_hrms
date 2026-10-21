# Copyright (c) 2026, Quark Cyber System FZC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

import qcs_hrms
from qcs_hrms.ledger.utils import LedgerController, get_rate, get_reference_details, make_hr_payment


class HRPaymentAllocationError(frappe.ValidationError):
	pass


class HRPayment(LedgerController):
	"""Payment to (Pay) or receipt from (Receive) a party, optionally allocated against HR documents.

	Pay:     Dr paid_to (party account)  /  Cr paid_from (bank/cash)
	Receive: Dr paid_to (bank/cash)      /  Cr paid_from (party account)
	"""

	def validate(self):
		self.set_party_name()
		self.set_defaults()
		self.validate_accounts()
		self.validate_references()
		self.set_amounts()
		self.set_status()

	def set_party_name(self):
		if not (self.party_type and self.party):
			return
		meta = frappe.get_meta(self.party_type)
		name_field = next(
			(f for f in ("employee_name", "full_name", "title", meta.title_field) if f and meta.has_field(f)), None
		)
		self.party_name = frappe.db.get_value(self.party_type, self.party, name_field) if name_field else self.party

	def set_defaults(self):
		company_currency = qcs_hrms.get_company_currency(self.company)
		if not self.currency:
			self.currency = company_currency
		if self.currency == company_currency:
			self.exchange_rate = 1
		elif not flt(self.exchange_rate) or flt(self.exchange_rate) == 1:
			self.exchange_rate = get_rate(self.currency, company_currency, self.posting_date)

	def validate_accounts(self):
		if self.paid_from == self.paid_to:
			frappe.throw(_("Paid From and Paid To accounts can not be the same"))

		company_currency = qcs_hrms.get_company_currency(self.company)
		for fieldname in ("paid_from", "paid_to"):
			account = frappe.get_cached_value(
				"HR Ledger Account",
				self.get(fieldname),
				["company", "disabled", "account_currency"],
				as_dict=True,
			)
			label = _(self.meta.get_label(fieldname))
			if not account:
				frappe.throw(_("{0} account {1} does not exist").format(label, self.get(fieldname)))
			if account.company != self.company:
				frappe.throw(
					_("{0} account {1} does not belong to company {2}").format(
						label, frappe.bold(self.get(fieldname)), self.company
					)
				)
			if account.disabled:
				frappe.throw(_("{0} account {1} is disabled").format(label, frappe.bold(self.get(fieldname))))
			if account.account_currency not in (self.currency, company_currency):
				frappe.throw(
					_("{0} account {1} is in {2}; it must be in {3} or {4}").format(
						label, frappe.bold(self.get(fieldname)), account.account_currency, self.currency, company_currency
					)
				)

	def validate_references(self):
		seen = set()
		for row in self.references:
			key = (row.reference_doctype, row.reference_name)
			if key in seen:
				frappe.throw(_("Row {0}: duplicate reference {1} {2}").format(row.idx, *key))
			seen.add(key)

			details = get_reference_details(row.reference_doctype, row.reference_name, self.payment_type)
			if details.docstatus != 1:
				frappe.throw(_("Row {0}: {1} {2} must be submitted").format(row.idx, *key))
			if details.company and details.company != self.company:
				frappe.throw(_("Row {0}: {1} {2} belongs to another company").format(row.idx, *key))
			if details.party and (details.party_type, details.party) != (self.party_type, self.party):
				frappe.throw(
					_("Row {0}: {1} {2} belongs to {3} {4}, not {5}").format(
						row.idx, *key, details.party_type, details.party, self.party
					)
				)
			if details.currency and details.currency != self.currency:
				frappe.throw(
					_("Row {0}: {1} {2} is in currency {3}, but the payment is in {4}").format(
						row.idx, *key, details.currency, self.currency
					)
				)

			row.currency = self.currency
			if self.docstatus == 0 or self._action == "submit":
				row.outstanding_amount = details.outstanding_amount

			precision = row.precision("allocated_amount")
			if flt(row.allocated_amount, precision) <= 0:
				frappe.throw(_("Row {0}: Allocated Amount must be greater than zero").format(row.idx))
			if flt(row.allocated_amount, precision) > flt(row.outstanding_amount, precision):
				frappe.throw(
					_("Row {0}: Allocated Amount {1} can not be greater than outstanding amount {2} of {3} {4}").format(
						row.idx, row.allocated_amount, row.outstanding_amount, *key
					),
					HRPaymentAllocationError,
				)

	def set_amounts(self):
		precision = self.precision("paid_amount")
		self.total_allocated_amount = flt(sum(flt(r.allocated_amount) for r in self.references), precision)
		if flt(self.paid_amount, precision) <= 0:
			frappe.throw(_("Paid Amount must be greater than zero"))
		if self.total_allocated_amount > flt(self.paid_amount, precision):
			frappe.throw(
				_("Total allocated amount {0} can not be greater than the paid amount {1}").format(
					self.total_allocated_amount, self.paid_amount
				),
				HRPaymentAllocationError,
			)
		self.unallocated_amount = flt(self.paid_amount - self.total_allocated_amount, precision)
		self.base_paid_amount = flt(
			flt(self.paid_amount) * flt(self.exchange_rate or 1), self.precision("base_paid_amount")
		)

	def set_status(self, update=False):
		status = {0: "Draft", 1: "Submitted", 2: "Cancelled"}[self.docstatus or 0]
		if update:
			self.db_set("status", status)
		else:
			self.status = status

	def on_submit(self):
		self.set_status(update=True)
		self.make_ledger_entries()
		self.update_references(cancel=False)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("HR Ledger Entry",)
		self.set_status(update=True)
		self.make_ledger_entries(cancel=True)
		self.update_references(cancel=True)

	def update_references(self, cancel=False):
		handlers = frappe.get_hooks("qcs_hrms_payment_reference_handlers")
		if not isinstance(handlers, dict):
			handlers = {}
		for row in self.references:
			for handler in handlers.get(row.reference_doctype) or []:
				frappe.get_attr(handler)(self, row, cancel)

	def get_ledger_entries(self):
		rate = flt(self.exchange_rate) or 1
		party_account = self.paid_to if self.payment_type == "Pay" else self.paid_from
		bank_account = self.paid_from if self.payment_type == "Pay" else self.paid_to
		party_side = "debit" if self.payment_type == "Pay" else "credit"
		bank_side = "credit" if party_side == "debit" else "debit"
		remarks = self.remarks or _("{0} {1}").format(_(self.payment_type), self.party_name or self.party)

		entries = []

		def party_entry(amount, against_type=None, against_name=None):
			entries.append(
				self.get_ledger_dict(
					{
						"account": party_account,
						"party_type": self.party_type,
						"party": self.party,
						"against": bank_account,
						party_side: amount * rate,
						f"{party_side}_in_account_currency": amount,
						"against_voucher_type": against_type,
						"against_voucher": against_name,
						"remarks": remarks,
					},
					account_currency=self.currency,
				)
			)

		for row in self.references:
			party_entry(flt(row.allocated_amount), row.reference_doctype, row.reference_name)
		if flt(self.unallocated_amount):
			party_entry(flt(self.unallocated_amount))

		entries.append(
			self.get_ledger_dict(
				{
					"account": bank_account,
					"against": self.party,
					bank_side: flt(self.base_paid_amount),
					f"{bank_side}_in_account_currency": flt(self.paid_amount),
					"remarks": remarks,
				},
				account_currency=self.currency,
			)
		)
		return entries


@frappe.whitelist()
def get_reference_outstanding(reference_doctype: str, reference_name: str, payment_type: str = "Pay") -> dict:
	frappe.has_permission(reference_doctype, "read", reference_name, throw=True)
	return get_reference_details(reference_doctype, reference_name, payment_type)


@frappe.whitelist()
def make_payment_for(
	reference_doctype: str, reference_name: str, payment_type: str = "Pay", amount: float | None = None
):
	"""Return a new (unsaved) HR Payment prefilled against the given document (for "Record Payment")."""
	frappe.has_permission(reference_doctype, "read", reference_name, throw=True)
	frappe.has_permission("HR Payment", "create", throw=True)
	return make_hr_payment(reference_doctype, reference_name, payment_type, amount)
