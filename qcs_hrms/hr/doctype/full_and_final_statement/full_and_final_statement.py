# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form

from qcs_hrms.ledger.utils import (
	LedgerController,
	get_company_default,
	get_voucher_balance,
	make_hr_payment,
)


class FullandFinalStatement(LedgerController):
	"""On submit the statement posts a settlement journal to the HR ledger: every payable is debited and
	every receivable / asset recovery credited against its source document, and the net amount is moved
	to `payable_account` against this statement. An HR Payment against the statement then settles it.
	"""

	def before_insert(self):
		self.status = "Unpaid"
		self.get_outstanding_statements()

	def validate(self):
		self.validate_relieving_date()
		self.get_assets_statements()
		self.set_total_asset_recovery_cost()
		self.set_totals()
		if not self.payable_account and self.company:
			self.payable_account = get_company_default(self.company, "default_payroll_payable_account")

	def before_submit(self):
		self.validate_settlement("payables")
		self.validate_settlement("receivables")
		self.validate_assets()
		self.validate_accounts()

	def on_submit(self):
		self.make_ledger_entries()
		self.update_linked_documents()
		self.set_status(update=True)

	def on_cancel(self):
		self.ignore_linked_doctypes = ("HR Ledger Entry",)
		self.make_ledger_entries(cancel=True)
		self.update_linked_documents()
		self.set_status(update=True)

	def validate_accounts(self):
		missing = [
			_("Row {0} ({1}): {2}").format(row.idx, _(row.parentfield), row.component or row.reference_document)
			for row in (self.payables or []) + (self.receivables or [])
			if flt(row.amount) > 0 and not row.get("paid_via_salary_slip") and not row.account
		]
		missing += [
			_("Asset row {0}: {1}").format(row.idx, row.asset_name)
			for row in self.assets_allocated
			if row.action == "Recover Cost" and flt(row.cost) > 0 and not row.account
		]
		if missing:
			frappe.throw(
				_("Account is required for:") + "<br>" + "<br>".join(missing), title=_("Missing Accounts")
			)
		if flt(self.net_payable_amount) and not self.payable_account:
			frappe.throw(_("Please set the Settlement Account"), title=_("Missing Settlement Account"))

	def on_discard(self):
		self.db_set("status", "Cancelled")

	def validate_relieving_date(self):
		if not self.relieving_date:
			frappe.throw(
				_("Please set {0} for Employee {1}").format(
					frappe.bold(_("Relieving Date")),
					get_link_to_form("Employee", self.employee),
				),
				title=_("Missing Relieving Date"),
			)

	def validate_settlement(self, component_type):
		for data in self.get(component_type, []):
			if data.status == "Unsettled":
				frappe.throw(
					_("Settle all Payables and Receivables before submission"),
					title=_("Unsettled Transactions"),
				)

	def validate_assets(self):
		pending_returns = []

		for data in self.assets_allocated:
			if data.action == "Return":
				if data.status == "Owned":
					pending_returns.append(_("Row {0}: {1}").format(data.idx, frappe.bold(data.asset_name)))
			elif data.action == "Recover Cost":
				data.status = "Owned"

		if pending_returns:
			msg = _("All allocated assets should be returned before submission")
			msg += "<br><br>"
			msg += ", ".join(d for d in pending_returns)
			frappe.throw(msg, title=_("Pending Asset Returns"))

	@frappe.whitelist()
	def get_outstanding_statements(self):
		if not self.relieving_date:
			frappe.throw(
				_("Set Relieving Date for Employee: {0}").format(get_link_to_form("Employee", self.employee))
			)

		if not self.payables:
			self.add_withheld_salary_slips()
			components = self.get_payable_component()
			self.create_component_row(components, "payables")
		self.add_outstanding_employee_advances()
		self.create_component_row(self.get_receivable_component(), "receivables")
		self.get_assets_statements()

	def get_assets_statements(self):
		if not len(self.get("assets_allocated", [])):
			for data in self.get_assets_movement():
				self.append("assets_allocated", data)

	def set_total_asset_recovery_cost(self):
		total_cost = 0
		for data in self.assets_allocated:
			if data.action == "Recover Cost":
				if not data.description:
					data.description = _("Asset Recovery Cost for {0}").format(data.asset_name)
				total_cost += flt(data.cost)

		self.total_asset_recovery_cost = flt(total_cost, self.precision("total_asset_recovery_cost"))

	def set_totals(self):
		total_payable = sum(flt(row.amount) for row in self.payables)
		total_receivable = sum(flt(row.amount) for row in self.receivables)

		self.total_payable_amount = flt(total_payable, self.precision("total_payable_amount"))
		self.total_receivable_amount = flt(
			total_receivable + flt(self.total_asset_recovery_cost),
			self.precision("total_receivable_amount"),
		)
		settled_via_salary = sum(flt(row.amount) for row in self.payables if row.paid_via_salary_slip)
		self.net_payable_amount = flt(
			total_payable - settled_via_salary - self.total_receivable_amount,
			self.precision("net_payable_amount"),
		)

	def add_withheld_salary_slips(self):
		salary_slips = frappe.get_all(
			"Salary Slip",
			filters={
				"employee": self.employee,
				"status": "Withheld",
				"docstatus": ("!=", 2),
			},
			fields=["name", "net_pay"],
		)

		for slip in salary_slips:
			self.append(
				"payables",
				{
					"status": "Unsettled",
					"component": "Salary Slip",
					"reference_document_type": "Salary Slip",
					"reference_document": slip.name,
					"amount": slip.net_pay,
					"paid_via_salary_slip": 1,
				},
			)

	def add_outstanding_employee_advances(self):
		"""
		Reconciles receivables with the employee's outstanding advances: placeholder rows and other
		employees' advances are dropped, rows not marked Settled are refreshed to the current balance,
		and missing advances are added.
		"""
		advances = frappe.get_all(
			"Employee Advance",
			filters={"employee": self.employee, "docstatus": 1},
			fields=["name", "advance_account", "paid_amount", "claimed_amount", "return_amount"],
		)
		outstanding_advances = {}
		for advance in advances:
			advance.outstanding = (
				flt(advance.paid_amount) - flt(advance.claimed_amount) - flt(advance.return_amount)
			)
			if advance.outstanding > 0:
				outstanding_advances[advance.name] = advance

		self.receivables = [
			row
			for row in self.receivables
			if row.component != "Employee Advance"
			or (row.reference_document in outstanding_advances)
			or (not row.reference_document and not outstanding_advances)
		]

		referenced_advances = set()
		for row in self.receivables:
			advance = outstanding_advances.get(row.reference_document)
			if not advance:
				continue

			referenced_advances.add(advance.name)
			if row.status != "Settled":
				row.account = advance.advance_account
				row.amount = advance.outstanding

		for advance in outstanding_advances.values():
			if advance.name in referenced_advances:
				continue

			self.append(
				"receivables",
				{
					"status": "Unsettled",
					"component": "Employee Advance",
					"reference_document_type": "Employee Advance",
					"reference_document": advance.name,
					"account": advance.advance_account,
					"amount": advance.outstanding,
				},
			)

	def create_component_row(self, components, component_type):
		existing_components = {row.component for row in self.get(component_type)}

		for component in components:
			if component in existing_components:
				continue

			self.append(
				component_type,
				{
					"status": "Unsettled",
					"reference_document_type": component if component != "Bonus" else "Additional Salary",
					"component": component,
				},
			)

	def get_payable_component(self):
		return [
			"Gratuity",
			"Expense Claim",
			"Bonus",
			"Leave Encashment",
		]

	def get_receivable_component(self):
		return ["Employee Advance"]

	def get_assets_movement(self):
		# assets are tracked outside qcs_hrms; allocated assets are entered manually
		return []

	def get_ledger_entries(self):
		entries = []

		def add(account, debit=0, credit=0, against_type=None, against_name=None, party=True, remarks=None):
			entries.append(
				self.get_ledger_dict(
					{
						"account": account,
						"debit": flt(debit),
						"credit": flt(credit),
						"party_type": "Employee" if party else None,
						"party": self.employee if party else None,
						"against_voucher_type": against_type if against_name else None,
						"against_voucher": against_name,
						"against": self.employee,
						"remarks": remarks,
					}
				)
			)

		for row in self.payables:
			if flt(row.amount) > 0 and not row.paid_via_salary_slip:
				add(
					row.account,
					debit=row.amount,
					against_type=row.reference_document_type,
					against_name=row.reference_document,
					remarks=row.remark,
				)

		for row in self.receivables:
			if flt(row.amount) > 0:
				add(
					row.account,
					credit=row.amount,
					against_type=row.reference_document_type,
					against_name=row.reference_document,
					remarks=row.remark,
				)

		for row in self.assets_allocated:
			if row.action == "Recover Cost" and flt(row.cost) > 0:
				add(row.account, credit=row.cost, party=False, remarks=row.description)

		if entries and flt(self.net_payable_amount):
			net = flt(self.net_payable_amount)
			add(
				self.payable_account,
				credit=net if net > 0 else 0,
				debit=-net if net < 0 else 0,
				against_type=self.doctype,
				against_name=self.name,
				remarks=_("Full and Final settlement of {0}").format(self.employee),
			)

		return entries

	def get_settlement_balance(self) -> float:
		"""Ledger balance (debit - credit) of the statement's settlement entries and their payments."""
		return get_voucher_balance(self.doctype, self.name, exclude_own_entries=False)

	def get_hr_payment_party_account(self):
		return self.payable_account

	def get_hr_payment_outstanding_amount(self, payment_type=None):
		if self.docstatus != 1:
			return 0
		balance = self.get_settlement_balance()
		if payment_type == "Receive":
			return max(balance, 0)
		if payment_type == "Pay":
			return max(-balance, 0)
		return abs(balance)

	def set_status(self, update=False):
		if self.docstatus == 2:
			status = "Cancelled"
		elif self.docstatus == 1:
			precision = self.precision("net_payable_amount")
			balance = flt(self.get_settlement_balance(), precision)
			status = "Paid" if not balance else "Unpaid"
			paid_amount = flt(abs(flt(self.net_payable_amount)) - abs(balance), precision)
			if update:
				self.db_set("paid_amount", paid_amount)
			else:
				self.paid_amount = paid_amount
		else:
			status = "Unpaid"

		if update:
			self.db_set("status", status)
			self.notify_update()
		else:
			self.status = status

	def update_linked_documents(self):
		"""Refresh paid / returned amounts of the documents settled by this statement."""
		from qcs_hrms.hr.doctype.expense_claim.expense_claim import update_reimbursed_amount

		for row in (self.payables or []) + (self.receivables or []):
			if not (row.reference_document_type and row.reference_document) or row.paid_via_salary_slip:
				continue
			if not frappe.db.exists(row.reference_document_type, row.reference_document):
				continue

			doc = frappe.get_doc(row.reference_document_type, row.reference_document)
			if row.reference_document_type == "Expense Claim":
				update_reimbursed_amount(doc)
			elif row.reference_document_type in ("Employee Advance", "Leave Encashment"):
				doc.set_total_advance_paid()
			elif row.reference_document_type == "Gratuity":
				# Gratuity tracks payments itself; mirror upstream behaviour
				amount = flt(row.amount) if self.docstatus == 1 else 0
				doc.db_set("paid_amount", amount)
				doc.set_status(update=True)


@frappe.whitelist()
def get_account_and_amount(ref_doctype: str, ref_document: str, company: str) -> list | None:
	if not ref_doctype or not ref_document:
		return None

	frappe.has_permission(ref_doctype, "read", ref_document, throw=True)

	if ref_doctype == "Salary Slip":
		salary_details = frappe.db.get_value(
			"Salary Slip", ref_document, ["payroll_entry", "net_pay"], as_dict=1
		)
		amount = salary_details.net_pay
		payable_account = (
			frappe.db.get_value("Payroll Entry", salary_details.payroll_entry, "payroll_payable_account")
			if salary_details.payroll_entry
			else None
		)
		return [payable_account, amount]

	if ref_doctype == "Gratuity":
		payable_account, amount = frappe.db.get_value("Gratuity", ref_document, ["payable_account", "amount"])
		return [payable_account, amount]

	if ref_doctype == "Expense Claim":
		details = frappe.db.get_value(
			"Expense Claim",
			ref_document,
			["payable_account", "grand_total", "total_amount_reimbursed", "total_advance_amount"],
			as_dict=True,
		)
		payable_account = details.payable_account
		# grand_total is already net of the allocated advances
		amount = flt(details.grand_total) - flt(details.total_amount_reimbursed)
		return [payable_account, amount]

	if ref_doctype == "Employee Advance":
		details = frappe.db.get_value(
			"Employee Advance",
			ref_document,
			["advance_account", "paid_amount", "claimed_amount", "return_amount"],
			as_dict=1,
		)
		payment_account = details.advance_account
		amount = details.paid_amount - (details.claimed_amount + details.return_amount)
		return [payment_account, amount]

	if ref_doctype == "Leave Encashment":
		details = frappe.db.get_value(
			"Leave Encashment",
			ref_document,
			["encashment_amount", "paid_amount", "payable_account", "pay_via_payment_entry"],
			as_dict=True,
		)
		amount = flt(details.encashment_amount) - flt(details.paid_amount)
		# an encashment paid via HR Payment is booked to its own payable account
		payable_account = (
			details.pay_via_payment_entry and details.payable_account
		) or frappe.get_cached_value("Company", company, "default_payroll_payable_account")
		return [payable_account, amount]


def update_full_and_final_statement_status(payment_doc, reference_row, cancel=False):
	"""HR Payment reference handler: refresh the statement's paid status."""
	fnf = frappe.get_doc("Full and Final Statement", reference_row.reference_name)
	fnf.set_status(update=True)


@frappe.whitelist()
def make_payment(source_name: str) -> Document:
	"""Return a new HR Payment (Pay or Receive, depending on the net amount) settling the statement."""
	frappe.has_permission("Full and Final Statement", "read", source_name, throw=True)
	fnf = frappe.get_doc("Full and Final Statement", source_name)
	payment_type = "Receive" if fnf.get_settlement_balance() > 0 else "Pay"
	return make_hr_payment("Full and Final Statement", source_name, payment_type)
