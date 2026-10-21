# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import flt, get_link_to_form

import qcs_hrms
from qcs_hrms.hr.utils import validate_active_employee
from qcs_hrms.ledger.utils import get_default_bank_cash_account, make_hr_payment


class EmployeeAdvanceOverPayment(frappe.ValidationError):
	pass


class EmployeeAdvance(Document):
	def validate(self):
		validate_active_employee(self.employee)
		self.validate_advance_account_currency()
		self.validate_advance_account_type()
		self.set_status()
		self.set_pending_amount()

	def before_submit(self):
		if not self.get("advance_account"):
			default_advance_account = frappe.db.get_value(
				"Company", self.company, "default_employee_advance_account"
			)
			same_currency = self.currency == qcs_hrms.get_company_currency(self.company)

			if default_advance_account and same_currency:
				self.advance_account = default_advance_account
				return

			if not same_currency:
				frappe.throw(
					_("Please set the Advance Account {0} or in {1}").format(
						get_link_to_form("Employee Advance", self.name + "#advance_account", _("here")),
						get_link_to_form("Employee", self.employee + "#salary_information", self.employee),
					),
					title=_("Advance Account Required"),
				)

			frappe.throw(
				_(
					"Advance Account is mandatory. Please set the {0} in the Company {1} and submit this document."
				).format(
					get_link_to_form(
						"Company", self.company + "#hr_and_payroll_tab", "Default Employee Advance Account"
					),
					frappe.bold(self.company),
				),
				title=_("Missing Advance Account"),
			)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("HR Ledger Entry",)
		self.set_status(update=True)

	def on_update(self):
		self.publish_update()

	def after_delete(self):
		self.publish_update()

	def publish_update(self):
		employee_user = frappe.db.get_value("Employee", self.employee, "user_id", cache=True)
		qcs_hrms.refetch_resource("hrms:employee_advance_balance", employee_user)

	def validate_advance_account_type(self):
		if not self.advance_account:
			return

		account_type = frappe.db.get_value("HR Ledger Account", self.advance_account, "account_type")
		if not account_type or (account_type != "Receivable"):
			frappe.throw(
				_("Employee advance account {0} should be of type {1}.").format(
					get_link_to_form("HR Ledger Account", self.advance_account), frappe.bold(_("Receivable"))
				)
			)

	def validate_advance_account_currency(self):
		if self.currency and self.advance_account:
			account_currency = frappe.db.get_value("HR Ledger Account", self.advance_account, "account_currency")
			if self.currency != account_currency:
				frappe.throw(
					_(
						"Advance Account {} currency should be same as Salary Currency of Employee {}. Please select same currency Advance Account"
					).format(frappe.bold(self.advance_account), frappe.bold(self.employee))
				)

	def set_status(self, update=False):
		precision = self.precision("paid_amount")
		total_amount = flt(flt(self.claimed_amount) + flt(self.return_amount), precision)
		status = None

		if self.docstatus == 0:
			status = "Draft"
		elif self.docstatus == 1:
			if flt(self.claimed_amount) > 0 and flt(self.claimed_amount, precision) == flt(
				self.paid_amount, precision
			):
				status = "Claimed"
			elif flt(self.return_amount) > 0 and flt(self.return_amount, precision) == flt(
				self.paid_amount, precision
			):
				status = "Returned"
			elif (
				flt(self.claimed_amount) > 0
				and (flt(self.return_amount) > 0)
				and total_amount == flt(self.paid_amount, precision)
			):
				status = "Partly Claimed and Returned"
			elif flt(self.paid_amount) > 0 and flt(self.advance_amount, precision) == flt(
				self.paid_amount, precision
			):
				status = "Paid"
			elif flt(self.paid_amount) > 0:
				status = "Partially Paid"
			else:
				status = "Unpaid"
		elif self.docstatus == 2:
			status = "Cancelled"

		if update:
			self.db_set("status", status)
			self.publish_update()
			self.notify_update()
		else:
			self.status = status

	def on_discard(self):
		self.db_set("status", "Cancelled")

	def set_total_advance_paid(self):
		"""Recompute paid and returned amounts from the HR ledger.

		Debits on the advance account against this advance are payments; credits (other than the
		adjustments booked by Expense Claims, tracked as claimed_amount) are returns, whether made via
		HR Payment, payroll deduction or Full and Final Statement.
		"""
		le = frappe.qb.DocType("HR Ledger Entry")
		totals = (
			frappe.qb.from_(le)
			.select(
				Sum(le.debit_in_account_currency).as_("paid_amount"),
				Sum(le.debit).as_("base_paid_amount"),
				Sum(le.credit_in_account_currency).as_("return_amount"),
			)
			.where(
				(le.company == self.company)
				& (le.is_cancelled == 0)
				& (le.against_voucher_type == self.doctype)
				& (le.against_voucher == self.name)
				& (le.party_type == "Employee")
				& (le.party == self.employee)
				& (le.voucher_type != "Expense Claim")
			)
		).run(as_dict=True)[0]

		precision = self.precision("paid_amount")
		paid_amount = flt(totals.paid_amount, precision)
		if paid_amount > flt(self.advance_amount, precision):
			frappe.throw(
				_("Paid Amount cannot be greater than requested advance amount"),
				EmployeeAdvanceOverPayment,
			)

		precision = self.precision("return_amount")
		return_amount = flt(totals.return_amount, precision)

		if return_amount > 0 and return_amount > flt(paid_amount - flt(self.claimed_amount), precision):
			frappe.throw(_("Return amount cannot be greater than unclaimed amount"))

		self.db_set("paid_amount", paid_amount)
		self.db_set("return_amount", return_amount)
		self.db_set("base_paid_amount", flt(totals.base_paid_amount))
		self.set_status(update=True)
		self.set_pending_amount(update=True)

	def get_hr_payment_party_account(self):
		return self.advance_account or frappe.db.get_value(
			"Company", self.company, "default_employee_advance_account"
		)

	def get_hr_payment_outstanding_amount(self, payment_type=None):
		if self.docstatus != 1:
			return 0
		if payment_type == "Receive":
			return max(
				flt(self.paid_amount) - flt(self.claimed_amount) - flt(self.return_amount),
				0,
			)
		return max(flt(self.advance_amount) - flt(self.paid_amount), 0)

	def update_claimed_amount(self):
		ec = frappe.qb.DocType("Expense Claim")
		eca = frappe.qb.DocType("Expense Claim Advance")

		claimed_amount = (
			frappe.qb.from_(ec)
			.join(eca)
			.on(ec.name == eca.parent)
			.select(Sum(eca.allocated_amount))
			.where(
				(eca.employee_advance == self.name)
				& (eca.allocated_amount > 0)
				& (ec.approval_status == "Approved")
				& (ec.docstatus == 1)
			)
		).run()[0][0] or 0
		frappe.db.set_value("Employee Advance", self.name, "claimed_amount", flt(claimed_amount))
		self.reload()
		self.set_status(update=True)

	def set_pending_amount(self, update=False):
		Advance = frappe.qb.DocType("Employee Advance")
		pending_amount = (
			frappe.qb.from_(Advance)
			.select(Sum(Advance.advance_amount - Advance.paid_amount))
			.where(
				(Advance.employee == self.employee)
				& (Advance.docstatus == 1)
				& (Advance.posting_date <= self.posting_date)
				& (Advance.status.isin(["Unpaid", "Partially Paid"]))
			)
		).run()[0][0] or 0.0

		if update:
			self.db_set("pending_amount", pending_amount)
		else:
			self.pending_amount = pending_amount


def update_employee_advance_for_payment(payment_doc, reference_row, cancel=False):
	"""HR Payment reference handler: refresh paid / returned amounts of the advance."""
	frappe.get_doc("Employee Advance", reference_row.reference_name).set_total_advance_paid()


def update_advances_for_voucher(voucher_type: str, voucher_no: str):
	"""Refresh every Employee Advance a voucher (e.g. Payroll Entry) posted ledger entries against.

	Call after posting or reversing ledger entries that credit/debit employee advance accounts.
	"""
	advances = frappe.get_all(
		"HR Ledger Entry",
		filters={
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
			"against_voucher_type": "Employee Advance",
		},
		pluck="against_voucher",
		distinct=True,
	)
	for advance in advances:
		frappe.get_doc("Employee Advance", advance).set_total_advance_paid()


@frappe.whitelist()
def create_return_through_additional_salary(doc: str | dict | Document) -> Document:
	import json

	if isinstance(doc, str):
		doc = frappe._dict(json.loads(doc))

	additional_salary = frappe.new_doc("Additional Salary")
	additional_salary.employee = doc.employee
	additional_salary.currency = doc.currency
	additional_salary.overwrite_salary_structure_amount = 0
	additional_salary.amount = doc.paid_amount - doc.claimed_amount
	additional_salary.company = doc.company
	additional_salary.ref_doctype = doc.doctype
	additional_salary.ref_docname = doc.name

	return additional_salary


@frappe.whitelist()
def make_payment(source_name: str) -> Document:
	"""Return a new HR Payment prefilled to pay out the advance."""
	frappe.has_permission("Employee Advance", "read", source_name, throw=True)
	return make_hr_payment("Employee Advance", source_name, "Pay")


@frappe.whitelist()
def make_return_entry(
	employee: str,
	company: str,
	employee_advance_name: str,
	return_amount: str | float,
	advance_account: str,
	currency: str,
	mode_of_payment: str | None = None,
	bank_account: str | None = None,
) -> dict:
	"""Return a new (unsaved) HR Payment of type Receive for the unclaimed advance amount."""
	frappe.has_permission("Employee Advance", "read", employee_advance_name, throw=True)
	bank_cash_account = get_same_currency_bank_cash_account(company, currency, bank_account)

	if not bank_cash_account:
		frappe.msgprint(
			_(
				"Couldn't automatically pick a Bank/Cash Account for this return. Please set one in the"
				" payment, or set a Default Cash Account in the {0} to avoid this next time."
			).format(get_link_to_form("Company", company, _("Company"))),
			title=_("Bank/Cash Account Not Set"),
			indicator="orange",
		)

	payment = make_hr_payment(
		"Employee Advance", employee_advance_name, "Receive", flt(return_amount), bank_cash_account
	)
	payment.paid_from = advance_account or payment.paid_from
	payment.mode_of_payment = mode_of_payment
	payment.remarks = _("Return against Employee Advance: {0}").format(employee_advance_name)
	return payment.as_dict()


def get_same_currency_bank_cash_account(company, currency, account=None):
	if account:
		return account

	company_currency = qcs_hrms.get_company_currency(company)
	if currency == company_currency:
		return get_default_bank_cash_account(company, "Cash") or get_default_bank_cash_account(
			company, "Bank"
		)

	accounts = frappe.get_all(
		"HR Ledger Account",
		filters={
			"company": company,
			"account_currency": currency,
			"account_type": ["in", ["Cash", "Bank"]],
			"disabled": 0,
		},
		pluck="name",
		limit=1,
	)
	if not accounts:
		frappe.throw(
			_("No Bank/Cash Account found for currency {0}. Please create one under company {1}.").format(
				frappe.bold(currency), company
			),
			title=_("Account Not Found"),
		)
	return accounts[0]


@frappe.whitelist()
def get_employee_advance_return(employee_advance: str):
	frappe.has_permission("Employee Advance", ptype="read", doc=employee_advance, throw=True)

	AdditionalSalary = frappe.qb.DocType("Additional Salary")
	return_entries = (
		frappe.qb.from_(AdditionalSalary)
		.select(Sum(AdditionalSalary.amount).as_("total_return_scheduled"))
		.where(
			(AdditionalSalary.ref_doctype == "Employee Advance")
			& (AdditionalSalary.ref_docname == employee_advance)
			& (AdditionalSalary.docstatus == 1)
		)
	).run(as_dict=True)

	total_return_scheduled = flt(return_entries[0].total_return_scheduled)
	already_returned = flt(frappe.db.get_value("Employee Advance", employee_advance, "return_amount"))
	pending_scheduled = max(0, total_return_scheduled - already_returned)

	return {
		"total_return_scheduled": pending_scheduled,
		"has_return_scheduled": bool(pending_scheduled),
	}
