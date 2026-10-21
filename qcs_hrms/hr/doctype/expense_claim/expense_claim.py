# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import get_workflow_name
from frappe.utils import cstr, flt, get_link_to_form

import qcs_hrms
from qcs_hrms.hr.utils import set_employee_name, share_doc_with_approver, validate_active_employee
from qcs_hrms.ledger.utils import (
	LedgerController,
	get_company_default,
	get_default_bank_cash_account,
	get_paid_amount_from_ledger,
	get_rate,
	make_hr_payment,
)
from qcs_hrms.mixins.pwa_notifications import PWANotificationsMixin


class InvalidExpenseApproverError(frappe.ValidationError):
	pass


class ExpenseApproverIdentityError(frappe.ValidationError):
	pass


ROLES_ALLOWED_TO_CLAIM_FOR_OTHERS = {"HR User", "HR Manager", "Expense Approver"}


class MismatchError(frappe.ValidationError):
	pass


class ExpenseClaim(LedgerController, PWANotificationsMixin):
	def onload(self):
		self.set_onload(
			"self_expense_approval_not_allowed",
			frappe.db.get_single_value("HR Settings", "prevent_self_expense_approval"),
		)

	def after_insert(self):
		self.notify_approver()

	def validate(self):
		validate_active_employee(self.employee)
		self.validate_employee_scope()
		set_employee_name(self)
		self.set_company_currency_if_multi_currency_disabled()
		self.validate_sanctioned_amount()
		self.calculate_total_amount()
		self.validate_advances()
		self.set_expense_account(validate=True)
		self.calculate_taxes()
		self.set_status()
		self.validate_company_and_department()
		self.set_payment_account()

		if flt(self.grand_total) > 0 and self.total_advance_amount:
			self.is_paid = 0

	def set_status(self, update=False):
		status = {"0": "Draft", "1": "Submitted", "2": "Cancelled"}[cstr(self.docstatus or 0)]

		precision = self.precision("grand_total")

		if self.docstatus == 1:
			if self.approval_status == "Approved":
				if (
					# set as paid
					self.is_paid
					or (
						flt(self.total_sanctioned_amount) > 0
						and (
							# grand total is reimbursed
							(flt(self.grand_total, precision) == flt(self.total_amount_reimbursed, precision))
							# grand total (to be paid) is 0 since linked advances already cover the claimed amount
							or (flt(self.grand_total, precision) == 0)
						)
					)
				):
					status = "Paid"
				elif flt(self.total_sanctioned_amount) > 0:
					if flt(self.total_amount_reimbursed, precision) > 0:
						status = "Partially Paid"
					else:
						status = "Unpaid"
			elif self.approval_status == "Rejected":
				status = "Rejected"

		if update:
			self.db_set("status", status)
			self.publish_update()
			self.notify_update()
		else:
			self.status = status

	def validate_employee_scope(self):
		if not self.employee:
			return

		employee_company, employee_user = frappe.db.get_value(
			"Employee", self.employee, ["company", "user_id"]
		)
		if not self.company:
			self.company = employee_company
		elif self.company != employee_company:
			frappe.throw(
				_("Employee {0} does not belong to company: {1}").format(self.employee, self.company),
				exc=MismatchError,
			)

		if self.flags.ignore_permissions:
			return

		if employee_user != frappe.session.user and not (
			set(frappe.get_roles()) & ROLES_ALLOWED_TO_CLAIM_FOR_OTHERS
		):
			frappe.throw(
				_("You are not allowed to create or modify an Expense Claim for Employee {0}").format(
					self.employee
				),
				frappe.PermissionError,
			)

	def validate_company_and_department(self):
		if self.department:
			company = frappe.db.get_value("Department", self.department, "company")
			if company and self.company != company:
				frappe.throw(
					_("Department {0} does not belong to company: {1}").format(self.department, self.company),
					exc=MismatchError,
				)

	def validate_for_self_approval(self):
		self_expense_approval_not_allowed = frappe.db.get_single_value(
			"HR Settings", "prevent_self_expense_approval"
		)
		employee_user = frappe.db.get_value("Employee", self.employee, "user_id")
		if (
			self_expense_approval_not_allowed
			and employee_user == frappe.session.user
			and not get_workflow_name("Expense Claim")
		):
			frappe.throw(_("Self-approval for Expense Claims is not allowed"))

	def on_update(self):
		share_doc_with_approver(self, self.expense_approver)
		self.publish_update()
		self.notify_approval_status()

	def after_delete(self):
		self.publish_update()

	def on_discard(self):
		self.db_set("status", "Cancelled")
		self.db_set("approval_status", "Cancelled")

	def before_submit(self):
		self.validate_for_self_approval()

	def publish_update(self):
		employee_user = frappe.db.get_value("Employee", self.employee, "user_id", cache=True)
		qcs_hrms.refetch_resource("hrms:my_claims", employee_user)
		qcs_hrms.refetch_resource("hrms:team_claims")

	def on_submit(self):
		if self.approval_status == "Draft":
			frappe.throw(_("""Approval Status must be 'Approved' or 'Rejected'"""))

		self.set_exchange_gain_loss()
		self.make_ledger_entries()
		update_reimbursed_amount(self)
		self.update_claimed_amount_in_employee_advance()

	def on_update_after_submit(self):
		if self.ledger_fields_changed():
			# accounts / cost centers changed after submission: reverse and re-post the ledger entries
			self.make_ledger_entries(cancel=True)
			self.make_ledger_entries()

	def ledger_fields_changed(self) -> bool:
		before = self.get_doc_before_save()
		if not before:
			return False

		def snapshot(doc):
			return (
				doc.get("cost_center"),
				tuple((d.name, d.get("cost_center")) for d in doc.get("expenses") or []),
				tuple((d.name, d.get("account_head"), d.get("cost_center")) for d in doc.get("taxes") or []),
			)

		return snapshot(before) != snapshot(self)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("HR Ledger Entry",)
		self.make_ledger_entries(cancel=True)

		update_reimbursed_amount(self)

		self.update_claimed_amount_in_employee_advance()
		self.publish_update()

	def update_claimed_amount_in_employee_advance(self):
		for d in self.get("advances"):
			frappe.get_doc("Employee Advance", d.employee_advance).update_claimed_amount()

	def make_ledger_entries(self, cancel=False):
		if cancel or flt(self.total_sanctioned_amount) > 0:
			super().make_ledger_entries(cancel=cancel)

	def get_ledger_entries(self):
		ledger_entries = []
		self.validate_account_details()
		expense_accounts = ",".join({d.default_account for d in self.expenses if d.default_account})

		# payable entry
		if self.grand_total:
			ledger_entries.append(
				self.get_ledger_dict(
					{
						"account": self.payable_account,
						"credit": self.base_grand_total,
						"credit_in_account_currency": self.grand_total,
						"against": expense_accounts,
						"party_type": "Employee",
						"party": self.employee,
						"against_voucher_type": self.doctype,
						"against_voucher": self.name,
						"cost_center": self.cost_center,
					},
					account_currency=self.currency,
				)
			)

		# expense entries
		for data in self.expenses:
			ledger_entries.append(
				self.get_ledger_dict(
					{
						"account": data.default_account,
						"debit": data.base_sanctioned_amount,
						"debit_in_account_currency": data.sanctioned_amount,
						"against": self.employee,
						"cost_center": data.cost_center or self.cost_center,
					},
					account_currency=self.currency,
					item=data,
				)
			)

		# advances adjusted against this claim, booked at the rate at which the advance was paid
		for data in self.advances:
			if not flt(data.allocated_amount):
				continue
			ledger_entries.append(
				self.get_ledger_dict(
					{
						"account": data.advance_account,
						"credit": flt(data.base_allocated_amount) - flt(data.exchange_gain_loss),
						"credit_in_account_currency": data.allocated_amount,
						"against": expense_accounts,
						"party_type": "Employee",
						"party": self.employee,
						"against_voucher_type": "Employee Advance",
						"against_voucher": data.employee_advance,
						"cost_center": self.cost_center,
					},
					account_currency=self.currency,
					item=data,
				)
			)

		if flt(self.total_exchange_gain_loss):
			gain_loss = flt(self.total_exchange_gain_loss)
			ledger_entries.append(
				self.get_ledger_dict(
					{
						"account": self.gain_loss_account,
						"credit": gain_loss if gain_loss > 0 else 0,
						"debit": -gain_loss if gain_loss < 0 else 0,
						"against": self.employee,
						"cost_center": self.cost_center,
						"remarks": _("Exchange gain/loss on employee advances"),
					}
				)
			)

		self.add_tax_ledger_entries(ledger_entries)

		if self.is_paid and self.grand_total:
			# payment made along with the claim
			ledger_entries.append(
				self.get_ledger_dict(
					{
						"account": self.bank_or_cash_account,
						"credit": self.base_grand_total,
						"credit_in_account_currency": self.grand_total,
						"against": self.employee,
						"cost_center": self.cost_center,
					},
					account_currency=self.currency,
				)
			)

			ledger_entries.append(
				self.get_ledger_dict(
					{
						"account": self.payable_account,
						"party_type": "Employee",
						"party": self.employee,
						"against": self.bank_or_cash_account,
						"debit": self.base_grand_total,
						"debit_in_account_currency": self.grand_total,
						"against_voucher": self.name,
						"against_voucher_type": self.doctype,
						"cost_center": self.cost_center,
					},
					account_currency=self.currency,
				)
			)

		return ledger_entries

	def add_tax_ledger_entries(self, ledger_entries):
		for tax in self.get("taxes"):
			ledger_entries.append(
				self.get_ledger_dict(
					{
						"account": tax.account_head,
						"debit": tax.base_tax_amount,
						"debit_in_account_currency": tax.tax_amount,
						"against": self.employee,
						"cost_center": tax.cost_center or self.cost_center,
					},
					account_currency=self.currency,
					item=tax,
				)
			)

	def set_exchange_gain_loss(self):
		"""Difference between the advance valued at this claim's rate and at the rate it was paid."""
		total = 0
		for advance in self.get("advances"):
			gain_loss = 0
			if advance.exchange_rate and advance.base_allocated_amount:
				gain_loss = flt(
					flt(advance.base_allocated_amount) - flt(advance.allocated_amount) * flt(advance.exchange_rate),
					self.precision("total_exchange_gain_loss"),
				)
			advance.exchange_gain_loss = gain_loss
			if advance.name:
				advance.db_set("exchange_gain_loss", gain_loss)
			total += gain_loss

		self.total_exchange_gain_loss = total
		if total and not self.gain_loss_account:
			self.gain_loss_account = get_company_default(
				self.company, "exchange_gain_loss_account"
			) or get_company_default(self.company, "round_off_account", throw=True)
		self.db_set(
			{"total_exchange_gain_loss": total, "gain_loss_account": self.gain_loss_account if total else None}
		)

	def validate_account_details(self):
		for data in self.advances:
			self.validate_advance_linkage(data)

		if self.is_paid and not self.bank_or_cash_account:
			frappe.throw(_("Bank / Cash Account is required to make a payment"))

	def set_payment_account(self):
		if self.is_paid and not self.bank_or_cash_account and self.company:
			self.bank_or_cash_account = get_default_bank_cash_account(
				self.company, "Cash"
			) or get_default_bank_cash_account(self.company, "Bank")

	def get_hr_payment_party_account(self):
		return self.payable_account

	def get_hr_payment_outstanding_amount(self, payment_type=None):
		if self.docstatus != 1 or self.approval_status != "Approved":
			return 0
		if payment_type == "Receive":
			# refund of an amount reimbursed through HR Payments
			return 0 if self.is_paid else flt(self.total_amount_reimbursed)
		return max(get_outstanding_amount_for_claim(self), 0)

	def set_company_currency_if_multi_currency_disabled(self):
		if frappe.db.get_single_value("HR Settings", "enable_multi_currency_expense_claim"):
			return

		self.currency = qcs_hrms.get_company_currency(self.company)
		self.exchange_rate = 1.0
		for advance in self.get("advances"):
			advance.exchange_rate = 1.0

	def calculate_total_amount(self):
		self.total_claimed_amount = 0
		self.total_sanctioned_amount = 0

		for d in self.get("expenses"):
			self.round_floats_in(d)

			if self.approval_status == "Rejected":
				d.sanctioned_amount = 0.0

			self.total_claimed_amount += flt(d.amount)
			self.total_sanctioned_amount += flt(d.sanctioned_amount)
			self.set_base_fields_amount(d, ["amount", "sanctioned_amount"])

		self.set_base_fields_amount(self, ["total_sanctioned_amount", "total_claimed_amount"])

	def set_base_fields_amount(self, doc, fields, exchange_rate=None):
		"""set values in base currency"""
		for f in fields:
			val = flt(
				flt(doc.get(f), doc.precision(f))
				* flt(exchange_rate if exchange_rate else self.exchange_rate),
				doc.precision("base_" + f),
			)
			doc.set("base_" + f, val)

	@frappe.whitelist()
	def calculate_taxes(self):
		self.total_taxes_and_charges = 0
		for tax in self.taxes:
			self.round_floats_in(tax)

			if tax.rate:
				tax.tax_amount = flt(
					flt(self.total_sanctioned_amount) * flt(flt(tax.rate) / 100),
					tax.precision("tax_amount"),
				)

			tax.total = flt(tax.tax_amount) + flt(self.total_sanctioned_amount)
			self.total_taxes_and_charges += flt(tax.tax_amount)
			self.set_base_fields_amount(tax, ["tax_amount", "total"])

		self.round_floats_in(self, ["total_taxes_and_charges"])

		self.grand_total = (
			flt(self.total_sanctioned_amount)
			+ flt(self.total_taxes_and_charges)
			- flt(self.total_advance_amount)
		)
		self.round_floats_in(self, ["grand_total"])
		self.set_base_fields_amount(self, ["grand_total"])

	def validate_advances(self):
		self.total_advance_amount = 0
		precision = self.precision("total_advance_amount")

		for d in self.get("advances"):
			self.validate_advance_row(d, precision)

			self.total_advance_amount += flt(d.allocated_amount)
			self.set_base_fields_amount(d, ["advance_paid", "unclaimed_amount"], d.exchange_rate)
			self.set_base_fields_amount(d, ["allocated_amount"])

		if self.total_advance_amount:
			self.validate_total_advance_amount(precision)

	def validate_advance_row(self, d, precision):
		advance_details = frappe.db.get_value(
			"Employee Advance",
			d.employee_advance,
			["employee", "currency", "advance_account", "paid_amount"],
			as_dict=True,
		)
		if not advance_details or self.employee != advance_details.employee:
			frappe.throw(_("Selected employee advance is not of employee {}").format(self.employee))

		validate_employee_advance_currency_and_account(self, d.employee_advance, advance_details)
		self.validate_advance_linkage(d)

		self.round_floats_in(d)
		if d.allocated_amount and flt(d.allocated_amount) > flt(
			flt(d.unclaimed_amount) - flt(d.return_amount), precision
		):
			frappe.throw(
				_("Row {0}# Allocated amount {1} cannot be greater than unclaimed amount {2}").format(
					d.idx, d.allocated_amount, d.unclaimed_amount
				)
			)

	def validate_advance_linkage(self, d):
		# advance_account is set by get_advances; a row added without it cannot be booked
		if d.allocated_amount and not d.advance_account:
			frappe.throw(
				_("Row {0}: {1} is required against advance {2}.").format(
					d.idx,
					frappe.bold(_("Advance Account")),
					frappe.bold(d.employee_advance),
				)
			)

	def validate_total_advance_amount(self, precision):
		self.round_floats_in(self, ["total_advance_amount"])
		amount_with_taxes = flt(
			(flt(self.total_sanctioned_amount, precision) + flt(self.total_taxes_and_charges, precision)),
			precision,
		)
		self.set_base_fields_amount(self, ["total_advance_amount"])

		if flt(self.total_advance_amount, precision) > amount_with_taxes:
			frappe.throw(_("Total advance amount cannot be greater than total sanctioned amount"))

	def validate_sanctioned_amount(self):
		for d in self.get("expenses"):
			if flt(d.sanctioned_amount) > flt(d.amount):
				frappe.throw(
					_("Sanctioned Amount cannot be greater than Claim Amount in Row {0}.").format(d.idx)
				)

	def set_expense_account(self, validate=False):
		for expense in self.expenses:
			if not expense.default_account or not validate:
				expense.default_account = get_expense_claim_account(expense.expense_type, self.company)[
					"account"
				]


def update_reimbursed_amount(doc):
	total_amount_reimbursed = get_total_reimbursed_amount(doc)

	doc.total_amount_reimbursed = total_amount_reimbursed
	frappe.db.set_value("Expense Claim", doc.name, "total_amount_reimbursed", total_amount_reimbursed)

	doc.set_status(update=True)


def get_total_reimbursed_amount(doc):
	if doc.is_paid:
		# No need to check for cancelled state here as it will anyways update status as cancelled
		return doc.grand_total

	# payments (HR Payment, Full and Final Statement, ...) posted against the claim in the HR ledger
	return get_paid_amount_from_ledger(doc.doctype, doc.name)


def get_outstanding_amount_for_claim(claim):
	precision = frappe.get_precision("Expense Claim", "grand_total")

	if isinstance(claim, str):
		claim = frappe.db.get_value(
			"Expense Claim",
			claim,
			(
				"total_sanctioned_amount",
				"total_taxes_and_charges",
				"total_amount_reimbursed",
				"total_advance_amount",
			),
			as_dict=True,
		)

	outstanding_amt = (
		flt(claim.total_sanctioned_amount)
		+ flt(claim.total_taxes_and_charges)
		- flt(claim.total_amount_reimbursed)
		- flt(claim.total_advance_amount)
	)

	return flt(outstanding_amt, precision)


@frappe.whitelist()
def get_expense_claim_account_and_cost_center(expense_claim_type: str, company: str) -> dict:
	data = get_expense_claim_account(expense_claim_type, company)
	cost_center = get_company_default(company, "cost_center")

	return {"account": data.get("account"), "cost_center": cost_center}


@frappe.whitelist()
def get_exchange_rate(from_currency: str, to_currency: str, transaction_date: str | None = None) -> float:
	return get_rate(from_currency, to_currency, transaction_date)


@frappe.whitelist()
def get_expense_claim_account(expense_claim_type: str, company: str) -> dict:
	account = frappe.db.get_value(
		"Expense Claim Account", {"parent": expense_claim_type, "company": company}, "default_account"
	)
	if not account:
		frappe.throw(
			_("Set the default account for the {0} {1}").format(
				frappe.bold(_("Expense Claim Type")),
				get_link_to_form("Expense Claim Type", expense_claim_type),
			)
		)

	return {"account": account}


def validate_employee_advance_currency_and_account(
	expense_claim: Document, employee_advance: str, advance_details: dict | None = None
) -> None:
	if advance_details is None:
		advance_details = frappe.db.get_value(
			"Employee Advance",
			{"name": employee_advance, "employee": expense_claim.employee},
			["currency", "advance_account", "paid_amount"],
			as_dict=True,
		)
	if not advance_details:
		return

	if expense_claim.currency and advance_details.currency != expense_claim.currency:
		frappe.throw(
			_(
				"Employee Advance {0} is in currency {1} and can only be claimed in an Expense Claim of the same currency. This Expense Claim is in {2}."
			).format(
				frappe.bold(employee_advance),
				frappe.bold(advance_details.currency),
				frappe.bold(expense_claim.currency),
			)
		)

	account_type = frappe.db.get_value("HR Ledger Account", advance_details.advance_account, "account_type")
	if advance_details.advance_account and account_type != "Receivable":
		frappe.throw(
			_(
				"Employee Advance {0} is linked to account {1}, which is not of type Receivable. Please correct the account type."
			).format(frappe.bold(employee_advance), frappe.bold(advance_details.advance_account))
		)


@frappe.whitelist()
def get_advances(expense_claim: str | dict | Document, advance_id: str | None = None):
	import json

	if isinstance(expense_claim, str):
		expense_claim = frappe._dict(json.loads(expense_claim))
	expense_claim_doc = frappe.get_doc(expense_claim)
	frappe.has_permission("Employee", "read", expense_claim_doc.employee, throw=True)
	expense_claim_doc.advances = []

	advance = frappe.qb.DocType("Employee Advance")

	query = frappe.qb.from_(advance).select(
		advance.name,
		advance.purpose,
		advance.posting_date,
		advance.paid_amount,
		advance.base_paid_amount,
		advance.claimed_amount,
		advance.return_amount,
		advance.advance_account,
		advance.currency,
	)

	if not advance_id:
		query = query.where(
			(advance.docstatus == 1)
			& (advance.employee == expense_claim_doc.employee)
			& (advance.paid_amount > 0)
			& (advance.status.notin(["Claimed", "Returned", "Partly Claimed and Returned"]))
		)
		# advance can only be adjusted in its own currency
		if expense_claim_doc.currency:
			query = query.where(advance.currency == expense_claim_doc.currency)
	else:
		query = query.where((advance.name == advance_id) & (advance.employee == expense_claim_doc.employee))
		validate_employee_advance_currency_and_account(expense_claim_doc, advance_id)

	advances = query.run(as_dict=True)

	for advance in advances:
		get_expense_claim_advances(expense_claim_doc, advance)
	return expense_claim_doc.advances


@frappe.whitelist()
def get_expense_claim(employee_advance: str | dict) -> Document:
	frappe.has_permission("Employee Advance", "read", employee_advance, throw=True)
	if isinstance(employee_advance, str):
		employee_advance = frappe.get_doc("Employee Advance", employee_advance)

	company = employee_advance.company
	default_cost_center = frappe.get_cached_value("Company", company, "cost_center")

	expense_claim = frappe.new_doc("Expense Claim")
	expense_claim.company = company
	expense_claim.currency = employee_advance.currency
	expense_claim.employee = employee_advance.employee
	expense_claim.payable_account = (
		get_default_payable_account(company)
		if employee_advance.currency == qcs_hrms.get_company_currency(company)
		else None
	)
	expense_claim.cost_center = default_cost_center
	expense_claim.is_paid = 1 if flt(employee_advance.paid_amount) else 0
	get_expense_claim_advances(expense_claim, employee_advance)
	return expense_claim


@frappe.whitelist()
def get_default_payable_account(company: str) -> str | None:
	frappe.has_permission("Company", "read", company, throw=True)
	return frappe.get_cached_value(
		"Company", company, "default_expense_claim_payable_account"
	) or frappe.get_cached_value("Company", company, "default_payroll_payable_account")


def get_expense_claim_advances(expense_claim, employee_advance):
	paid_amount = flt(employee_advance.paid_amount)
	if paid_amount <= 0:
		return

	claimed_amount = flt(employee_advance.claimed_amount)
	return_amount = flt(employee_advance.return_amount)
	unclaimed_amount = paid_amount - claimed_amount
	allocated_amount = get_allocation_amount(
		paid_amount=paid_amount, claimed_amount=claimed_amount, return_amount=return_amount
	)
	base_paid_amount = flt(employee_advance.get("base_paid_amount")) or paid_amount
	exchange_rate = flt(base_paid_amount / paid_amount, 9) if paid_amount else 1

	expense_claim.append(
		"advances",
		{
			"advance_account": employee_advance.advance_account,
			"employee_advance": employee_advance.name,
			"posting_date": employee_advance.posting_date,
			"advance_paid": paid_amount,
			"base_advance_paid": base_paid_amount,
			"unclaimed_amount": unclaimed_amount,
			"allocated_amount": allocated_amount,
			"return_amount": return_amount,
			"exchange_rate": exchange_rate,
		},
	)


def update_payment_for_expense_claim(payment_doc, reference_row, cancel=False):
	"""HR Payment reference handler: refresh the reimbursed amount and status of the claim."""
	expense_claim = frappe.get_doc("Expense Claim", reference_row.reference_name)
	update_reimbursed_amount(expense_claim)


@frappe.whitelist()
def make_payment(source_name: str, payment_type: str = "Pay") -> Document:
	"""Return a new HR Payment prefilled to reimburse the claim."""
	frappe.has_permission("Expense Claim", "read", source_name, throw=True)
	return make_hr_payment("Expense Claim", source_name, payment_type)


@frappe.whitelist()
def get_allocation_amount(
	paid_amount: str | float | None = None,
	claimed_amount: str | float | None = None,
	return_amount: str | float | None = None,
	unclaimed_amount: str | float | None = None,
) -> float | None:
	if unclaimed_amount is not None and return_amount is not None:
		return flt(unclaimed_amount) - flt(return_amount)
	elif paid_amount is not None and claimed_amount is not None and return_amount is not None:
		return flt(paid_amount) - (flt(claimed_amount) + flt(return_amount))
	else:
		frappe.throw(_("Invalid parameters provided. Please pass the required arguments."))
