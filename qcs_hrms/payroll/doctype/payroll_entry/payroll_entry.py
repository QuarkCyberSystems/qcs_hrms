# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import datetime
import json

from dateutil.relativedelta import relativedelta

import frappe
from frappe import _
from frappe.desk.reportview import get_match_cond
from frappe.model.document import Document
from frappe.query_builder.functions import Coalesce, Count
from frappe.utils import (
	DATE_FORMAT,
	add_days,
	add_to_date,
	cint,
	comma_and,
	date_diff,
	flt,
	get_link_to_form,
	getdate,
)

import qcs_hrms
from qcs_hrms.hr.doctype.employee_advance.employee_advance import update_advances_for_voucher
from qcs_hrms.ledger.utils import make_ledger_entries
from qcs_hrms.payroll.doctype.salary_withholding.salary_withholding import link_payment_in_salary_withholdings
from qcs_hrms.utils.compat import get_fiscal_year
from qcs_hrms.utils.holiday_list import (
	clip_holiday_list_ranges,
	get_holiday_list_ranges_for_employees,
	get_holidays_in_ranges_map,
)


class PayrollEntry(Document):
	def onload(self):
		if self.docstatus == 0 and not self.salary_slips_created and self.employees:
			[employees_eligible_for_overtime, unsubmitted_overtime_slips] = self.get_overtime_slip_details()
			overtime_step = None
			if unsubmitted_overtime_slips:
				overtime_step = "Submit"
			elif employees_eligible_for_overtime:
				overtime_step = "Create"

			self.overtime_step = overtime_step

		if not self.docstatus == 1 or self.salary_slips_submitted:
			return

		# check if salary slips were manually submitted
		entries = frappe.db.count("Salary Slip", {"payroll_entry": self.name, "docstatus": 1})
		if cint(entries) == len(self.employees):
			self.set_onload("submitted_ss", True)

	def validate(self):
		self.number_of_employees = len(self.employees)
		self.set_status()

	def set_status(self, status=None, update=False):
		if not status:
			status = {0: "Draft", 1: "Submitted", 2: "Cancelled"}[self.docstatus or 0]

		if update:
			self.db_set("status", status)
		else:
			self.status = status

	def before_submit(self):
		self.validate_existing_salary_slips()
		self.validate_payroll_payable_account()
		if self.get_employees_with_unmarked_attendance():
			frappe.throw(_("Cannot submit. Attendance is not marked for some employees."))

	def on_submit(self):
		self.set_status(update=True, status="Submitted")
		self.create_salary_slips()

	def validate_existing_salary_slips(self):
		if not self.employees:
			return

		existing_salary_slips = []
		SalarySlip = frappe.qb.DocType("Salary Slip")

		existing_salary_slips = (
			frappe.qb.from_(SalarySlip)
			.select(SalarySlip.employee, SalarySlip.name)
			.where(
				(SalarySlip.employee.isin([emp.employee for emp in self.employees]))
				& (SalarySlip.start_date == self.start_date)
				& (SalarySlip.end_date == self.end_date)
				& (SalarySlip.docstatus != 2)
			)
		).run(as_dict=True)

		if len(existing_salary_slips):
			msg = _("Salary Slip already exists for {0} for the given dates").format(
				comma_and([frappe.bold(d.employee) for d in existing_salary_slips])
			)
			msg += "<br><br>"
			msg += _("Reference: {0}").format(
				comma_and([get_link_to_form("Salary Slip", d.name) for d in existing_salary_slips])
			)
			frappe.throw(
				msg,
				title=_("Duplicate Entry"),
			)

	def validate_payroll_payable_account(self):
		payroll_payable_account_type = frappe.db.get_value(
			"HR Ledger Account", self.payroll_payable_account, "account_type"
		)
		if payroll_payable_account_type != "Payable":
			frappe.throw(
				_(
					"Account type should be set {0} for payroll payable account {1}, please set and try again"
				).format(
					frappe.bold("Payable"),
					frappe.bold(get_link_to_form("HR Ledger Account", self.payroll_payable_account)),
				)
			)

	def before_cancel(self):
		# validate before the docstatus is written, so a refused cancel leaves the document submitted
		self.validate_no_linked_payments()

	def on_cancel(self):
		self.ignore_linked_doctypes = ("HR Ledger Entry", "Salary Slip")

		# reverse the accrual first: deleting a salary slip is only allowed once every ledger entry
		# against it is cancelled
		self.cancel_ledger_entries()
		self.delete_linked_salary_slips()

		# reset flags & update status
		self.db_set("salary_slips_created", 0)
		self.db_set("salary_slips_submitted", 0)
		self.set_status(update=True, status="Cancelled")
		self.db_set("error_message", "")

	def on_discard(self):
		self.db_set("status", "Cancelled")

	def cancel(self):
		if len(self.get_linked_salary_slips()) > 50:
			msg = _("Payroll Entry cancellation is queued. It may take a few minutes")
			msg += "<br>"
			msg += _(
				"In case of any error during this background process, the system will add a comment about the error on this Payroll Entry and revert to the Submitted status"
			)
			frappe.msgprint(
				msg,
				indicator="blue",
				title=_("Cancellation Queued"),
			)
			self.queue_action("cancel", timeout=3000)
		else:
			self._cancel()

	def delete_linked_salary_slips(self):
		salary_slips = self.get_linked_salary_slips()

		# cancel & delete salary slips
		for salary_slip in salary_slips:
			if salary_slip.docstatus == 1:
				frappe.get_doc("Salary Slip", salary_slip.name).cancel()
			delete_salary_slip(salary_slip.name)

	def validate_no_linked_payments(self):
		"""Submitted HR Payments block cancellation; draft ones are deleted"""
		payments = self.get_linked_payments(docstatus=1)
		if payments:
			frappe.throw(
				_("Cannot cancel Payroll Entry as it is linked with HR Payment(s) {0}. Cancel them first.").format(
					comma_and([get_link_to_form("HR Payment", payment) for payment in payments])
				),
				title=_("Linked Payments"),
			)

		for payment in self.get_linked_payments(docstatus=0):
			frappe.delete_doc("HR Payment", payment, ignore_permissions=True)

	def get_linked_salary_slips(self):
		return frappe.get_all("Salary Slip", {"payroll_entry": self.name}, ["name", "docstatus"])

	def make_filters(self):
		filters = frappe._dict(
			company=self.company,
			branch=self.branch,
			department=self.department,
			designation=self.designation,
			grade=self.grade,
			currency=self.currency,
			start_date=self.start_date,
			end_date=self.end_date,
			payroll_payable_account=self.payroll_payable_account,
			payroll_frequency=self.payroll_frequency,
		)

		return filters

	@frappe.whitelist()
	def fill_employee_details(self) -> list[dict] | None:
		filters = self.make_filters()
		employees = get_employee_list(filters=filters, as_dict=True, ignore_match_conditions=True)
		self.set("employees", [])

		if not employees:
			error_msg = _(
				"No employees found for the mentioned criteria:<br>Company: {0}<br> Currency: {1}<br>Payroll Payable Account: {2}"
			).format(
				frappe.bold(self.company),
				frappe.bold(self.currency),
				frappe.bold(self.payroll_payable_account),
			)
			if self.branch:
				error_msg += "<br>" + _("Branch: {0}").format(frappe.bold(self.branch))
			if self.department:
				error_msg += "<br>" + _("Department: {0}").format(frappe.bold(self.department))
			if self.designation:
				error_msg += "<br>" + _("Designation: {0}").format(frappe.bold(self.designation))
			if self.start_date:
				error_msg += "<br>" + _("Start date: {0}").format(frappe.bold(self.start_date))
			if self.end_date:
				error_msg += "<br>" + _("End date: {0}").format(frappe.bold(self.end_date))
			frappe.throw(error_msg, title=_("No employees found"))

		self.set("employees", employees)
		self.number_of_employees = len(self.employees)
		self.update_employees_with_withheld_salaries()

		return self.get_employees_with_unmarked_attendance()

	def update_employees_with_withheld_salaries(self):
		withheld_salaries = get_salary_withholdings(self.start_date, self.end_date, pluck="employee")

		for employee in self.employees:
			if employee.employee in withheld_salaries:
				employee.is_salary_withheld = 1

	@frappe.whitelist(methods=["POST"])
	def create_salary_slips(self) -> None:
		"""
		Creates salary slip for selected employees if already not created
		"""
		self.check_permission("write")
		employees = [emp.employee for emp in self.employees]

		if employees:
			args = frappe._dict(
				{
					"payroll_frequency": self.payroll_frequency,
					"start_date": self.start_date,
					"end_date": self.end_date,
					"company": self.company,
					"posting_date": self.posting_date,
					"deduct_tax_for_unsubmitted_tax_exemption_proof": self.deduct_tax_for_unsubmitted_tax_exemption_proof,
					"payroll_entry": self.name,
					"exchange_rate": self.exchange_rate,
					"currency": self.currency,
				}
			)
			if len(employees) > 30 or frappe.flags.enqueue_payroll_entry:
				self.db_set("status", "Queued")
				frappe.enqueue(
					create_salary_slips_for_employees,
					timeout=3000,
					employees=employees,
					args=args,
					publish_progress=False,
				)
				frappe.msgprint(
					_("Salary Slip creation is queued. It may take a few minutes"),
					alert=True,
					indicator="blue",
				)
			else:
				create_salary_slips_for_employees(employees, args, publish_progress=False)
				# since this method is called via frm.call this doc needs to be updated manually
				self.reload()

	def get_sal_slip_list(self, ss_status, as_dict=False):
		"""
		Returns list of salary slips based on selected criteria
		"""

		ss = frappe.qb.DocType("Salary Slip")
		ss_list = (
			frappe.qb.from_(ss)
			.select(ss.name, ss.salary_structure)
			.where(
				(ss.docstatus == ss_status)
				& (ss.start_date >= self.start_date)
				& (ss.end_date <= self.end_date)
				& (ss.payroll_entry == self.name)
				& (Coalesce(ss.accrual_posted, 0) == 0)
			)
		).run(as_dict=as_dict)

		return ss_list

	@frappe.whitelist(methods=["POST"])
	def submit_salary_slips(self) -> None:
		self.check_permission("write")
		salary_slips = self.get_sal_slip_list(ss_status=0)

		if len(salary_slips) > 30 or frappe.flags.enqueue_payroll_entry:
			self.db_set("status", "Queued")
			frappe.enqueue(
				submit_salary_slips_for_employees,
				timeout=3000,
				payroll_entry=self,
				salary_slips=salary_slips,
				publish_progress=False,
			)
			frappe.msgprint(
				_("Salary Slip submission is queued. It may take a few minutes"),
				alert=True,
				indicator="blue",
			)
		else:
			submit_salary_slips_for_employees(self, salary_slips, publish_progress=False)

	def email_salary_slip(self, submitted_ss):
		if frappe.db.get_single_value("Payroll Settings", "email_salary_slip_to_employee"):
			for ss in submitted_ss:
				ss.email_salary_slip()

	def get_salary_component_account(self, salary_component):
		account = frappe.db.get_value(
			"Salary Component Account",
			{"parent": salary_component, "company": self.company},
			"account",
			cache=True,
		)

		if not account:
			frappe.throw(
				_("Please set account in Salary Component {0}").format(
					get_link_to_form("Salary Component", salary_component)
				)
			)

		return account

	def get_salary_components(self, component_type):
		salary_slips = self.get_sal_slip_list(ss_status=1, as_dict=True)

		if salary_slips:
			ss = frappe.qb.DocType("Salary Slip")
			ssd = frappe.qb.DocType("Salary Detail")
			salary_components = (
				frappe.qb.from_(ss)
				.join(ssd)
				.on(ss.name == ssd.parent)
				.select(
					ssd.salary_component,
					ssd.amount,
					ssd.parentfield,
					ssd.additional_salary,
					ss.salary_structure,
					ss.employee,
					ss.name.as_("salary_slip"),
				)
				.where(
					(ssd.parentfield == component_type)
					& (ss.name.isin([d.name for d in salary_slips]))
					& (
						(ssd.do_not_include_in_total == 0)
						| ((ssd.do_not_include_in_total == 1) & (ssd.do_not_include_in_accounts == 0))
					)
				)
			).run(as_dict=True)

			return salary_components

	def get_salary_component_total(self, component_type=None):
		salary_components = self.get_salary_components(component_type)
		if salary_components:
			component_dict = {}
			advance_deductions = self.get_advance_deductions(component_type, salary_components)

			for item in salary_components:
				employee_cost_centers = self.get_payroll_cost_centers_for_employee(
					item.employee, item.salary_structure
				)
				employee_advance = advance_deductions.get(item.additional_salary)

				for cost_center, percentage in employee_cost_centers.items():
					amount_against_cost_center = flt(item.amount) * percentage / 100

					if employee_advance:
						self.add_advance_deduction_entry(
							item, amount_against_cost_center, cost_center, employee_advance
						)
					else:
						key = (item.salary_component, cost_center)
						component_dict[key] = component_dict.get(key, 0) + amount_against_cost_center

					self.set_salary_slip_payable_entries(
						component_type, item.salary_slip, item.employee, amount_against_cost_center
					)

			account_details = self.get_account(component_dict=component_dict)

			return account_details

	def get_advance_deductions(self, component_type: str, salary_components: list[dict]) -> dict[str, dict]:
		"""Employee Advances being recovered, keyed by the Additional Salary that recovers them"""
		additional_salaries = list({d.additional_salary for d in salary_components if d.additional_salary})
		if component_type != "deductions" or not additional_salaries:
			return {}

		AdditionalSalary = frappe.qb.DocType("Additional Salary")
		EmployeeAdvance = frappe.qb.DocType("Employee Advance")
		advances = (
			frappe.qb.from_(AdditionalSalary)
			.join(EmployeeAdvance)
			.on(AdditionalSalary.ref_docname == EmployeeAdvance.name)
			.select(
				AdditionalSalary.name.as_("additional_salary"),
				EmployeeAdvance.name.as_("employee_advance"),
				EmployeeAdvance.advance_account,
			)
			.where(AdditionalSalary.name.isin(additional_salaries))
			.where(AdditionalSalary.ref_doctype == "Employee Advance")
		).run(as_dict=True)

		return {d.additional_salary: d for d in advances}

	def add_advance_deduction_entry(
		self,
		item: dict,
		amount: float,
		cost_center: str,
		employee_advance: dict,
	) -> None:
		self._advance_deduction_entries.append(
			{
				"employee": item.employee,
				"account": employee_advance.advance_account,
				"amount": amount,
				"cost_center": cost_center,
				"reference_type": "Employee Advance",
				"reference_name": employee_advance.employee_advance,
			}
		)

	def set_ledger_entries_for_advance_deductions(self, entries: list, payable_amount: float) -> float:
		for entry in self._advance_deduction_entries:
			payable_amount = self.add_ledger_entry(
				entries,
				entry.get("account"),
				entry.get("amount"),
				entry_type="credit",
				cost_center=entry.get("cost_center"),
				payable_amount=payable_amount,
				party=entry.get("employee"),
				against_voucher_type="Employee Advance",
				against_voucher=entry.get("reference_name"),
			)

		return payable_amount

	def set_salary_slip_payable_entries(self, component_type, salary_slip, employee, amount):
		"""Accumulate earnings / deductions per salary slip to post the net payable slip-wise"""
		slip_details = self.salary_slip_payable_entries.setdefault(salary_slip, {"employee": employee})
		slip_details[component_type] = slip_details.get(component_type, 0) + amount

	def get_payroll_cost_centers_for_employee(self, employee, salary_structure):
		if not hasattr(self, "employee_cost_centers"):
			self.employee_cost_centers = {}

		if not self.employee_cost_centers.get(employee):
			SalaryStructureAssignment = frappe.qb.DocType("Salary Structure Assignment")
			EmployeeCostCenter = frappe.qb.DocType("Employee Cost Center")
			assignment_subquery = (
				frappe.qb.from_(SalaryStructureAssignment)
				.select(SalaryStructureAssignment.name)
				.where(
					(SalaryStructureAssignment.employee == employee)
					& (SalaryStructureAssignment.salary_structure == salary_structure)
					& (SalaryStructureAssignment.docstatus == 1)
					& (SalaryStructureAssignment.from_date <= self.end_date)
				)
				.orderby(SalaryStructureAssignment.from_date, order=frappe.qb.desc)
				.limit(1)
			)
			cost_centers = dict(
				(
					frappe.qb.from_(EmployeeCostCenter)
					.select(EmployeeCostCenter.cost_center, EmployeeCostCenter.percentage)
					.where(EmployeeCostCenter.parent == assignment_subquery)
				).run(as_list=True)
			)

			if not cost_centers:
				default_cost_center, department = frappe.get_cached_value(
					"Employee", employee, ["payroll_cost_center", "department"]
				)

				if not default_cost_center and department:
					default_cost_center = frappe.get_cached_value(
						"Department", department, "payroll_cost_center"
					)

				if not default_cost_center:
					default_cost_center = self.cost_center

				cost_centers = {default_cost_center: 100}

			self.employee_cost_centers.setdefault(employee, cost_centers)

		return self.employee_cost_centers.get(employee, {})

	def get_account(self, component_dict=None):
		account_dict = {}
		for key, amount in component_dict.items():
			component, cost_center = key
			account = self.get_salary_component_account(component)
			accounting_key = (account, cost_center)

			account_dict[accounting_key] = account_dict.get(accounting_key, 0) + amount

		return account_dict

	def make_accrual_ledger_entries(self, submitted_salary_slips):
		"""Post the payroll accrual as HR Ledger Entries against this Payroll Entry.

		Debits the salary component (expense) accounts of earnings, credits the accounts of deductions
		and employee advances being recovered, and credits the net pay of every salary slip to the
		payroll payable account with the employee as party (against the Salary Slip, which is what an
		HR Payment settles). Employer contributions are posted in the same voucher: expense debited,
		liability credited."""
		self.check_permission("write")
		self.salary_slip_payable_entries = {}
		self._advance_deduction_entries = []

		earnings = self.get_salary_component_total(component_type="earnings") or {}
		deductions = self.get_salary_component_total(component_type="deductions") or {}
		employer_contributions = self.get_salary_components("employer_contributions") or []

		entries = []
		if earnings or deductions:
			payable_amount = self.get_payable_amount_for_earnings_and_deductions(entries, earnings, deductions)
			self.set_ledger_entries_for_advance_deductions(entries, payable_amount)
			self.set_payable_amount_against_payroll_payable_account(entries)

		entries.extend(self.get_employer_contribution_ledger_entries(employer_contributions))

		if entries:
			self.add_round_off_entry(entries)
			try:
				make_ledger_entries(entries, merge_entries=True)
			except Exception:
				self.log_error("Accrual Ledger Entry creation against Salary Slip failed")
				raise

			update_advances_for_voucher(self.doctype, self.name)

		self.set_accrual_posted_in_salary_slips(submitted_salary_slips)

	# kept for backward compatibility with upstream callers
	make_accrual_jv_entry = make_accrual_ledger_entries

	def get_employer_contribution_ledger_entries(self, employer_contributions) -> list[dict]:
		if not employer_contributions:
			return []

		component_accounts = self.get_employer_contribution_accounts(
			{item.salary_component for item in employer_contributions}
		)

		precision = self.get_ledger_precision()
		expense_entries = {}
		liability_entries = {}
		for item in employer_contributions:
			expense_account, liability_account = component_accounts[item.salary_component]

			# the last cost center takes the rounding remainder so that the expense
			# splits always sum up to the amount credited against the liability
			item_amount = flt(item.amount, precision)
			employee_cost_centers = list(
				self.get_payroll_cost_centers_for_employee(item.employee, item.salary_structure).items()
			)
			allocated = 0
			for cost_center, percentage in employee_cost_centers[:-1]:
				split = flt(item_amount * percentage / 100, precision)
				allocated += split
				expense_key = (expense_account, cost_center)
				expense_entries[expense_key] = expense_entries.get(expense_key, 0) + split

			last_cost_center = employee_cost_centers[-1][0]
			expense_key = (expense_account, last_cost_center)
			expense_entries[expense_key] = expense_entries.get(expense_key, 0) + flt(
				item_amount - allocated, precision
			)

			liability_entries[liability_account] = liability_entries.get(liability_account, 0) + item_amount

		if not any(expense_entries.values()):
			return []

		entries = []
		remarks = _("Employer contribution accrual for salaries from {0} to {1}").format(
			self.start_date, self.end_date
		)
		for (account, cost_center), amount in expense_entries.items():
			self.add_ledger_entry(
				entries,
				account,
				amount,
				entry_type="debit",
				cost_center=cost_center or self.cost_center,
				remarks=remarks,
			)

		for account, amount in liability_entries.items():
			self.add_ledger_entry(
				entries,
				account,
				amount,
				entry_type="credit",
				cost_center=self.cost_center,
				remarks=remarks,
			)

		return entries

	def get_employer_contribution_accounts(self, salary_components):
		"""Returns {salary_component: (expense_account, liability_account)} in a single query"""
		salary_components = list(salary_components)
		account_details = frappe.get_all(
			"Salary Component Account",
			filters={
				"parenttype": "Salary Component",
				"parent": ["in", salary_components],
				"company": self.company,
			},
			fields=["parent", "account", "liability_account"],
		)
		component_accounts = {d.parent: (d.account, d.liability_account) for d in account_details}

		for salary_component in salary_components:
			accounts = component_accounts.get(salary_component)
			if not accounts or not all(accounts):
				frappe.throw(
					_("Please set expense and liability accounts in Salary Component {0}").format(
						get_link_to_form("Salary Component", salary_component)
					)
				)

		return component_accounts

	def get_ledger_precision(self) -> int:
		try:
			return frappe.get_precision("HR Ledger Entry", "debit_in_account_currency") or 2
		except Exception:
			return cint(frappe.db.get_default("currency_precision")) or 2

	def get_payable_amount_for_earnings_and_deductions(self, entries, earnings, deductions):
		payable_amount = 0

		# Earnings
		for (account, cost_center), amount in earnings.items():
			payable_amount = self.add_ledger_entry(
				entries,
				account,
				amount,
				entry_type="debit",
				cost_center=cost_center or self.cost_center,
				payable_amount=payable_amount,
			)

		# Deductions
		for (account, cost_center), amount in deductions.items():
			payable_amount = self.add_ledger_entry(
				entries,
				account,
				amount,
				entry_type="credit",
				cost_center=cost_center or self.cost_center,
				payable_amount=payable_amount,
			)

		return payable_amount

	def set_payable_amount_against_payroll_payable_account(self, entries):
		"""Credit the net pay of each salary slip to the payroll payable account (party: employee)"""
		for salary_slip, slip_details in self.salary_slip_payable_entries.items():
			slip_payable_amount = flt(slip_details.get("earnings")) - flt(slip_details.get("deductions"))
			self.add_ledger_entry(
				entries,
				self.payroll_payable_account,
				slip_payable_amount,
				entry_type="credit",
				cost_center=self.cost_center,
				party=slip_details["employee"],
				against_voucher_type="Salary Slip",
				against_voucher=salary_slip,
			)

	def add_ledger_entry(
		self,
		entries: list,
		account: str,
		amount: float,
		entry_type: str = "credit",
		cost_center: str | None = None,
		payable_amount: float = 0,
		party: str | None = None,
		against_voucher_type: str | None = None,
		against_voucher: str | None = None,
		remarks: str | None = None,
	) -> float:
		"""Append an HR Ledger Entry dict for `amount` (in payroll currency) and return the running
		payable amount (earnings increase it, deductions reduce it)"""
		precision = self.get_ledger_precision()
		account_currency, amount_in_account_currency, amount_in_company_currency = (
			self.get_amounts_in_account_and_company_currency(account, amount)
		)

		if entry_type == "debit":
			payable_amount += flt(amount, precision)
		else:
			payable_amount -= flt(amount, precision)

		if not amount_in_account_currency:
			return payable_amount

		is_debit = entry_type == "debit"
		if amount_in_account_currency < 0:
			# a negative amount is posted on the opposite side
			is_debit = not is_debit
			amount_in_account_currency = abs(amount_in_account_currency)
			amount_in_company_currency = abs(amount_in_company_currency)

		row = frappe._dict(
			{
				"posting_date": self.posting_date,
				"company": self.company,
				"account": account,
				"account_currency": account_currency,
				"cost_center": cost_center,
				"voucher_type": self.doctype,
				"voucher_no": self.name,
				"remarks": remarks
				or _("Accrual for salaries from {0} to {1}").format(self.start_date, self.end_date),
				"debit": amount_in_company_currency if is_debit else 0,
				"credit": 0 if is_debit else amount_in_company_currency,
				"debit_in_account_currency": amount_in_account_currency if is_debit else 0,
				"credit_in_account_currency": 0 if is_debit else amount_in_account_currency,
			}
		)

		if party:
			row.update({"party_type": "Employee", "party": party})

		if against_voucher_type:
			row.update({"against_voucher_type": against_voucher_type, "against_voucher": against_voucher})

		entries.append(row)
		return payable_amount

	def get_amounts_in_account_and_company_currency(self, account: str, amount: float) -> tuple:
		"""Returns (account_currency, amount in account currency, amount in company currency) for an
		amount in payroll currency. Accounts not in company currency are assumed to be in payroll currency."""
		precision = self.get_ledger_precision()
		company_currency = qcs_hrms.get_company_currency(self.company)
		account_currency = (
			frappe.get_cached_value("HR Ledger Account", account, "account_currency") or company_currency
		)
		exchange_rate = flt(self.exchange_rate) or 1
		amount_in_company_currency = flt(flt(amount) * exchange_rate, precision)

		if account_currency == company_currency:
			amount_in_account_currency = amount_in_company_currency
		else:
			amount_in_account_currency = flt(amount, precision)

		return account_currency, amount_in_account_currency, amount_in_company_currency

	def add_round_off_entry(self, entries: list) -> None:
		"""Book the rounding difference from converting each row to company currency to the company's
		round off account (the ledger itself only tolerates a few units of the last decimal place)"""
		precision = self.get_ledger_precision()
		difference = flt(
			sum(flt(d.get("debit")) for d in entries) - sum(flt(d.get("credit")) for d in entries), precision
		)
		# each converted row can be off by half a unit of the last decimal place
		max_rounding_difference = len(entries) * 0.5 / (10**precision)
		if not difference or abs(difference) > max_rounding_difference:
			# no difference, or not a rounding difference (left to ledger validation)
			return

		round_off_account, round_off_cost_center = frappe.get_cached_value(
			"Company", self.company, ["round_off_account", "round_off_cost_center"]
		)
		if not round_off_account:
			frappe.throw(
				_("Please set Round Off Account in Company {0}").format(
					get_link_to_form("Company", self.company)
				)
			)

		amount = abs(difference)
		entries.append(
			frappe._dict(
				{
					"posting_date": self.posting_date,
					"company": self.company,
					"account": round_off_account,
					"account_currency": qcs_hrms.get_company_currency(self.company),
					"cost_center": round_off_cost_center or self.cost_center,
					"voucher_type": self.doctype,
					"voucher_no": self.name,
					"remarks": _("Round off"),
					"debit": amount if difference < 0 else 0,
					"credit": amount if difference > 0 else 0,
					"debit_in_account_currency": amount if difference < 0 else 0,
					"credit_in_account_currency": amount if difference > 0 else 0,
				}
			)
		)

	def cancel_ledger_entries(self):
		"""Reverse the accrual HR Ledger Entries posted against this Payroll Entry"""
		entries = frappe.get_all(
			"HR Ledger Entry",
			filters={"voucher_type": self.doctype, "voucher_no": self.name, "is_cancelled": 0},
			fields=[
				"posting_date",
				"company",
				"account",
				"account_currency",
				"debit",
				"credit",
				"debit_in_account_currency",
				"credit_in_account_currency",
				"party_type",
				"party",
				"cost_center",
				"against_voucher_type",
				"against_voucher",
				"voucher_type",
				"voucher_no",
				"remarks",
			],
		)
		if entries:
			make_ledger_entries(entries, cancel=True)
			update_advances_for_voucher(self.doctype, self.name)

	def set_accrual_posted_in_salary_slips(self, submitted_salary_slips):
		if not submitted_salary_slips:
			return

		SalarySlip = frappe.qb.DocType("Salary Slip")
		(
			frappe.qb.update(SalarySlip)
			.set(SalarySlip.accrual_posted, 1)
			.where(SalarySlip.name.isin([salary_slip.name for salary_slip in submitted_salary_slips]))
		).run()

	def get_linked_payments(self, docstatus: int | None = None) -> list[str]:
		"""Non-cancelled (or the given docstatus) HR Payments referencing this Payroll Entry or its
		(non-withheld) salary slips"""
		Payment = frappe.qb.DocType("HR Payment")
		Reference = frappe.qb.DocType("HR Payment Reference")
		SalarySlip = frappe.qb.DocType("Salary Slip")

		salary_slips = (
			frappe.qb.from_(SalarySlip)
			.select(SalarySlip.name)
			.where((SalarySlip.payroll_entry == self.name) & (SalarySlip.status != "Withheld"))
		)

		return (
			frappe.qb.from_(Payment)
			.join(Reference)
			.on((Reference.parent == Payment.name) & (Reference.parenttype == "HR Payment"))
			.select(Payment.name)
			.distinct()
			.where(Payment.docstatus < 2 if docstatus is None else Payment.docstatus == docstatus)
			.where(
				((Reference.reference_doctype == self.doctype) & (Reference.reference_name == self.name))
				| ((Reference.reference_doctype == "Salary Slip") & (Reference.reference_name.isin(salary_slips)))
			)
		).run(pluck=True)

	def get_salary_slips_with_payments(self, salary_slips: list[str]) -> list[str]:
		"""Salary slips already referenced in a draft or submitted HR Payment"""
		if not salary_slips:
			return []

		Payment = frappe.qb.DocType("HR Payment")
		Reference = frappe.qb.DocType("HR Payment Reference")
		return (
			frappe.qb.from_(Reference)
			.join(Payment)
			.on((Reference.parent == Payment.name) & (Reference.parenttype == "HR Payment"))
			.select(Reference.reference_name)
			.distinct()
			.where(
				(Payment.docstatus < 2)
				& (Reference.reference_doctype == "Salary Slip")
				& (Reference.reference_name.isin(salary_slips))
			)
		).run(pluck=True)

	def get_hr_payment_outstanding_amount(self, payment_type=None) -> float:
		"""Unpaid net pay (payroll currency) of the submitted, not withheld salary slips (HR Payment protocol)"""
		if payment_type == "Receive" or self.docstatus != 1:
			return 0

		salary_slips = frappe.get_all(
			"Salary Slip",
			filters={"payroll_entry": self.name, "docstatus": 1, "status": ["!=", "Withheld"]},
			fields=["name", "hr_payment"],
		)
		return flt(
			sum(get_salary_slip_payable_amounts([d.name for d in salary_slips if not d.hr_payment]).values())
		)

	@frappe.whitelist()
	def has_bank_entries(self) -> dict[str, bool]:
		return {
			"has_bank_entries": bool(self.get_linked_payments()),
			"has_bank_entries_for_withheld_salaries": not any(
				employee.is_salary_withheld for employee in self.employees
			),
		}

	@frappe.whitelist(methods=["POST"])
	def make_bank_entry(self, for_withheld_salaries: bool = False) -> list[str]:
		"""Create draft HR Payments paying the net salaries from the payment (bank/cash) account against
		the payroll payable account: one HR Payment per employee (party) with one reference per salary
		slip. Returns the names of the HR Payments created."""
		self.check_permission("write")
		if not self.payment_account:
			frappe.throw(_("Please set the Payment Account"), title=_("Payment Account Missing"))

		precision = self.get_ledger_precision()
		salary_slips = frappe.get_all(
			"Salary Slip",
			filters={
				"payroll_entry": self.name,
				"docstatus": 1,
				"status": "Withheld" if for_withheld_salaries else ["!=", "Withheld"],
			},
			fields=["name", "employee", "salary_withholding_cycle"],
			order_by="employee asc, name asc",
		)
		already_paid = set(self.get_salary_slips_with_payments([d.name for d in salary_slips]))
		salary_slips = [d for d in salary_slips if d.name not in already_paid]
		payable_amounts = get_salary_slip_payable_amounts([d.name for d in salary_slips])

		slips_by_employee = {}
		for slip in salary_slips:
			slip.amount = flt(payable_amounts.get(slip.name), precision)
			if slip.amount > 0:
				slips_by_employee.setdefault(slip.employee, []).append(slip)

		remark = _("withheld salaries") if for_withheld_salaries else _("salaries")
		remarks = _("Payment of {0} from {1} to {2}").format(remark, self.start_date, self.end_date)

		payments = []
		for employee, employee_slips in slips_by_employee.items():
			payment = self.make_hr_payment(employee, employee_slips, remarks=remarks)
			payments.append(payment.name)

			if for_withheld_salaries:
				link_payment_in_salary_withholdings(employee_slips, payment.name)

		return payments

	def make_hr_payment(self, employee: str, salary_slips: list[dict], remarks: str | None = None):
		"""Insert a draft HR Payment (in payroll currency) paying the given salary slips of an employee"""
		precision = self.get_ledger_precision()
		payment = frappe.new_doc("HR Payment")
		payment.update(
			{
				"company": self.company,
				"posting_date": self.posting_date,
				"payment_type": "Pay",
				"party_type": "Employee",
				"party": employee,
				"paid_from": self.payment_account,
				"paid_to": self.payroll_payable_account,
				"currency": self.currency,
				"exchange_rate": flt(self.exchange_rate) or 1,
				"cost_center": self.cost_center,
				"reference_date": self.posting_date,
				"remarks": remarks,
			}
		)
		for slip in salary_slips:
			payment.append(
				"references",
				{
					"reference_doctype": "Salary Slip",
					"reference_name": slip.name,
					"allocated_amount": flt(slip.amount, precision),
				},
			)
		payment.paid_amount = flt(sum(flt(row.allocated_amount) for row in payment.references), precision)
		payment.flags.ignore_permissions = True
		payment.insert()

		return payment

	def set_start_end_dates(self):
		self.update(
			get_start_end_dates(self.payroll_frequency, self.start_date or self.posting_date, self.company)
		)

	@frappe.whitelist()
	def get_employees_with_unmarked_attendance(self) -> list[dict] | None:
		if not self.validate_attendance:
			return

		unmarked_attendance = []
		employee_details = {record.name: record for record in self.get_employee_and_attendance_details()}
		holiday_list_ranges = get_holiday_list_ranges_for_employees(
			{name: details.company for name, details in employee_details.items()},
			self.start_date,
			self.end_date,
		)

		payroll_dates = {}
		for employee, details in employee_details.items():
			payroll_dates[employee] = self.get_payroll_dates_for_employee(details)
			holiday_list_ranges[employee] = clip_holiday_list_ranges(
				holiday_list_ranges.get(employee, []), *payroll_dates[employee]
			)

		holidays = get_holidays_in_ranges_map(holiday_list_ranges)

		for emp in self.employees:
			details = employee_details.get(emp.employee)
			if not details:
				continue

			start_date, end_date = payroll_dates[emp.employee]
			payroll_days = date_diff(end_date, start_date) + 1
			unmarked_days = payroll_days - (len(holidays.get(emp.employee, [])) + details.attendance_count)

			if unmarked_days > 0:
				unmarked_attendance.append(
					{
						"employee": emp.employee,
						"employee_name": emp.employee_name,
						"unmarked_days": unmarked_days,
					}
				)

		return unmarked_attendance

	def get_employee_and_attendance_details(self) -> list[dict]:
		"""Returns a list of employee and attendance details like
		[
		        {
		                "name": "HREMP00001",
		                "date_of_joining": "2019-01-01",
		                "relieving_date": "2022-01-01",
		                "company": "_Test Company",
		                "attendance_count": 22
		        }
		]
		"""
		employees = [emp.employee for emp in self.employees]

		Employee = frappe.qb.DocType("Employee")
		Attendance = frappe.qb.DocType("Attendance")

		return (
			frappe.qb.from_(Employee)
			.left_join(Attendance)
			.on(
				(Employee.name == Attendance.employee)
				& (Attendance.attendance_date.between(self.start_date, self.end_date))
				& (Attendance.docstatus == 1)
			)
			.select(
				Employee.name,
				Employee.date_of_joining,
				Employee.relieving_date,
				Employee.company,
				Count(Attendance.name).as_("attendance_count"),
			)
			.where(Employee.name.isin(employees))
			.groupby(Employee.name)
		).run(as_dict=True)

	def get_payroll_dates_for_employee(self, employee_details: dict) -> tuple[str, str]:
		start_date = self.start_date
		if employee_details.date_of_joining > getdate(self.start_date):
			start_date = employee_details.date_of_joining

		end_date = self.end_date
		if employee_details.relieving_date and employee_details.relieving_date < getdate(self.end_date):
			end_date = employee_details.relieving_date

		return start_date, end_date

	@frappe.whitelist(methods=["POST"])
	def create_overtime_slips(self) -> None:
		self.check_permission("write")

		from qcs_hrms.hr.doctype.overtime_slip.overtime_slip import (
			create_overtime_slips_for_employees,
			filter_employees_for_overtime_slip_creation,
		)

		employee_list = [emp.employee for emp in self.employees]
		employees = filter_employees_for_overtime_slip_creation(self.start_date, self.end_date, employee_list)

		if employees:
			args = frappe._dict(
				{
					"posting_date": self.posting_date,
					"start_date": self.start_date,
					"end_date": self.end_date,
					"company": self.company,
					"currency": self.currency,
					"payroll_entry": self.name,
				}
			)
			if len(employees) > 30 or frappe.flags.enqueue_payroll_entry:
				self.db_set("status", "Queued")
				frappe.enqueue(
					create_overtime_slips_for_employees,
					timeout=3000,
					employees=employees,
					args=args,
				)
				frappe.msgprint(
					_("Overtime Slip creation is queued. It may take a few minutes"),
					alert=True,
					indicator="blue",
				)
			else:
				create_overtime_slips_for_employees(employees, args)

	@frappe.whitelist(methods=["POST"])
	def submit_overtime_slips(self) -> None:
		self.check_permission("write")

		from qcs_hrms.hr.doctype.overtime_slip.overtime_slip import (
			submit_overtime_slips_for_employees,
		)

		overtime_slips = self.get_unsubmitted_overtime_slips()
		if overtime_slips:
			if len(overtime_slips) > 30 or frappe.flags.enqueue_payroll_entry:
				self.db_set("status", "Queued")
				frappe.enqueue(
					submit_overtime_slips_for_employees,
					timeout=3000,
					overtime_slips=overtime_slips,
					payroll_entry=self.name,
				)
				frappe.msgprint(
					_("Overtime Slip submission is queued. It may take a few minutes"),
					alert=True,
					indicator="blue",
				)
			else:
				submit_overtime_slips_for_employees(overtime_slips, self.name)

	@frappe.whitelist()
	def get_unsubmitted_overtime_slips(self, limit: int | None = None) -> list[str]:
		OvertimeSlip = frappe.qb.DocType("Overtime Slip")
		query = (
			frappe.qb.from_(OvertimeSlip)
			.select(OvertimeSlip.name)
			.where((OvertimeSlip.docstatus == 0) & (OvertimeSlip.payroll_entry == self.name))
		)
		if limit:
			query = query.limit(limit)

		return query.run(pluck="name")

	@frappe.whitelist()
	def get_overtime_slip_details(self) -> list[bool]:
		from qcs_hrms.hr.doctype.overtime_slip.overtime_slip import filter_employees_for_overtime_slip_creation

		employee_eligible_for_overtime = unsubmitted_overtime_slips = []

		if frappe.db.get_single_value("Payroll Settings", "create_overtime_slip"):
			employees = [emp.employee for emp in self.employees]
			employee_eligible_for_overtime = filter_employees_for_overtime_slip_creation(
				self.start_date, self.end_date, employees
			)
			unsubmitted_overtime_slips = self.get_unsubmitted_overtime_slips(limit=1)

		return [len(employee_eligible_for_overtime) > 0, len(unsubmitted_overtime_slips) > 0]


def get_salary_structure(company: str, currency: str, payroll_frequency: str) -> list[str]:
	SalaryStructure = frappe.qb.DocType("Salary Structure")

	query = (
		frappe.qb.from_(SalaryStructure)
		.select(SalaryStructure.name)
		.where(
			(SalaryStructure.docstatus == 1)
			& (SalaryStructure.is_active == "Yes")
			& (SalaryStructure.company == company)
			& (SalaryStructure.currency == currency)
		)
	)

	if payroll_frequency:
		query = query.where(SalaryStructure.payroll_frequency == payroll_frequency)

	return query.run(pluck=True)


def get_filtered_employees(
	sal_struct,
	filters,
	searchfield=None,
	search_string=None,
	fields=None,
	as_dict=False,
	limit=None,
	offset=None,
	ignore_match_conditions=False,
) -> list:
	SalaryStructureAssignment = frappe.qb.DocType("Salary Structure Assignment")
	Employee = frappe.qb.DocType("Employee")

	query = (
		frappe.qb.from_(Employee)
		.join(SalaryStructureAssignment)
		.on(Employee.name == SalaryStructureAssignment.employee)
		.where(
			(SalaryStructureAssignment.docstatus == 1)
			& (Employee.status != "Inactive")
			& (Employee.company == filters.company)
			& ((Employee.date_of_joining <= filters.end_date) | (Employee.date_of_joining.isnull()))
			& ((Employee.relieving_date >= filters.start_date) | (Employee.relieving_date.isnull()))
			& (SalaryStructureAssignment.salary_structure.isin(sal_struct))
			& (SalaryStructureAssignment.payroll_payable_account == filters.payroll_payable_account)
			& (filters.end_date >= SalaryStructureAssignment.from_date)
		)
	)

	query = set_fields_to_select(query, fields)
	query = set_searchfield(query, searchfield, search_string, qb_object=Employee)
	query = set_filter_conditions(query, filters, qb_object=Employee)

	if not ignore_match_conditions:
		query = set_match_conditions(query=query, qb_object=Employee)

	if limit:
		query = query.limit(limit)

	if offset:
		query = query.offset(offset)

	return query.run(as_dict=as_dict)


def set_fields_to_select(query, fields: list[str] | None = None):
	default_fields = ["employee", "employee_name", "department", "designation"]

	if fields:
		query = query.select(*fields).distinct()
	else:
		query = query.select(*default_fields).distinct()

	return query


def set_searchfield(query, searchfield, search_string, qb_object):
	if searchfield:
		query = query.where(
			(qb_object[searchfield].like("%" + search_string + "%"))
			| (qb_object.employee_name.like("%" + search_string + "%"))
		)

	return query


def set_filter_conditions(query, filters, qb_object):
	"""Append optional filters to employee query"""
	if filters.get("employees"):
		query = query.where(qb_object.name.notin(filters.get("employees")))

	for fltr_key in ["branch", "department", "designation", "grade"]:
		if filters.get(fltr_key):
			query = query.where(qb_object[fltr_key] == filters[fltr_key])

	return query


def set_match_conditions(query, qb_object):
	match_conditions = get_match_cond("Employee", as_condition=False)
	employee_permission_fields = get_employee_permission_fields()

	for cond in match_conditions:
		if isinstance(cond, dict):
			for key, value in cond.items():
				fieldname = employee_permission_fields.get(key)
				if not fieldname:
					continue

				if isinstance(value, list):
					query = query.where(qb_object[fieldname].isin(value))
				else:
					query = query.where(qb_object[fieldname] == value)

	return query


def get_employee_permission_fields():
	permission_fields = {"Employee": "name"}
	for df in frappe.get_meta("Employee").get_link_fields():
		if not df.get("ignore_user_permissions"):
			permission_fields[df.options] = df.fieldname

	return permission_fields


def remove_payrolled_employees(emp_list, start_date, end_date):
	SalarySlip = frappe.qb.DocType("Salary Slip")

	employees_with_payroll = (
		frappe.qb.from_(SalarySlip)
		.select(SalarySlip.employee)
		.where(
			(SalarySlip.docstatus == 1)
			& (SalarySlip.start_date == start_date)
			& (SalarySlip.end_date == end_date)
		)
	).run(pluck=True)

	return [emp_list[emp] for emp in emp_list if emp not in employees_with_payroll]


def delete_salary_slip(salary_slip: str) -> None:
	"""Delete a cancelled salary slip of a cancelled Payroll Entry.

	HR Ledger Entries are immutable and keep pointing to the slip after it is cancelled. Cancelled
	(reversed) ledger entries must not block the deletion, every other link still does."""
	from frappe.model.delete_doc import (
		check_if_doc_is_linked,
		get_dynamic_linked_docs,
		raise_link_exists_exception,
	)

	doc = frappe.get_doc("Salary Slip", salary_slip)
	check_if_doc_is_linked(doc)
	for link in get_dynamic_linked_docs(doc):
		if link["reference_doctype"] == "HR Ledger Entry" and frappe.db.get_value(
			"HR Ledger Entry", link["reference_docname"], "is_cancelled"
		):
			continue
		raise_link_exists_exception(
			doc, link["reference_doctype"], link["reference_docname"], link["at_position"]
		)

	frappe.delete_doc("Salary Slip", salary_slip, force=True)


@frappe.whitelist()
def get_start_end_dates(
	payroll_frequency: str, start_date: str | datetime.date | None = None, company: str | None = None
) -> frappe._dict:
	"""Returns dict of start and end dates for given payroll frequency based on start_date"""

	if payroll_frequency == "Monthly" or payroll_frequency == "Bimonthly" or payroll_frequency == "":
		year_start_date = get_fiscal_year(start_date, company=company, as_dict=True).year_start_date
		month = "%02d" % getdate(start_date).month
		m = get_month_details(year_start_date, month)
		if payroll_frequency == "Bimonthly":
			if getdate(start_date).day <= 15:
				start_date = m["month_start_date"]
				end_date = m["month_mid_end_date"]
			else:
				start_date = m["month_mid_start_date"]
				end_date = m["month_end_date"]
		else:
			start_date = m["month_start_date"]
			end_date = m["month_end_date"]

	if payroll_frequency == "Weekly":
		end_date = add_days(start_date, 6)

	if payroll_frequency == "Fortnightly":
		end_date = add_days(start_date, 13)

	if payroll_frequency == "Daily":
		end_date = start_date

	return frappe._dict({"start_date": start_date, "end_date": end_date})


def get_frequency_kwargs(frequency_name):
	frequency_dict = {
		"monthly": {"months": 1},
		"fortnightly": {"days": 14},
		"weekly": {"days": 7},
		"daily": {"days": 1},
	}
	return frequency_dict.get(frequency_name)


@frappe.whitelist()
def get_end_date(start_date: str | datetime.date, frequency: str) -> dict:
	start_date = getdate(start_date)
	frequency = frequency.lower() if frequency else "monthly"
	kwargs = get_frequency_kwargs(frequency) if frequency != "bimonthly" else get_frequency_kwargs("monthly")

	# weekly, fortnightly and daily intervals have fixed days so no problems
	end_date = add_to_date(start_date, **kwargs) - relativedelta(days=1)
	if frequency != "bimonthly":
		return dict(end_date=end_date.strftime(DATE_FORMAT))

	else:
		return dict(end_date="")


def get_month_details(year_start_date, month):
	"""`year_start_date` is the start date of the fiscal year (see `qcs_hrms.utils.compat.get_fiscal_year`)"""
	ysd = getdate(year_start_date) if year_start_date else None
	if ysd:
		import calendar
		import datetime

		diff_mnt = cint(month) - cint(ysd.month)
		if diff_mnt < 0:
			diff_mnt = 12 - int(ysd.month) + cint(month)
		msd = ysd + relativedelta(months=diff_mnt)  # month start date
		month_days = cint(calendar.monthrange(cint(msd.year), cint(month))[1])  # days in month
		mid_start = datetime.date(msd.year, cint(month), 16)  # month mid start date
		mid_end = datetime.date(msd.year, cint(month), 15)  # month mid end date
		med = datetime.date(msd.year, cint(month), month_days)  # month end date
		return frappe._dict(
			{
				"year": msd.year,
				"month_start_date": msd,
				"month_end_date": med,
				"month_mid_start_date": mid_start,
				"month_mid_end_date": mid_end,
				"month_days": month_days,
			}
		)
	else:
		frappe.throw(_("Fiscal Year not found"))


def log_payroll_failure(process, payroll_entry, error):
	error_log = frappe.log_error(
		title=_("Salary Slip {0} failed for Payroll Entry {1}").format(process, payroll_entry.name)
	)
	message_log = frappe.message_log.pop() if frappe.message_log else str(error)

	try:
		if isinstance(message_log, str):
			error_message = json.loads(message_log).get("message")
		else:
			error_message = message_log.get("message")
	except Exception:
		error_message = message_log

	error_message += "\n" + _("Check Error Log {0} for more details.").format(
		get_link_to_form("Error Log", error_log.name)
	)

	payroll_entry.db_set({"error_message": error_message, "status": "Failed"})


def create_salary_slips_for_employees(employees, args, publish_progress=True):
	payroll_entry = frappe.get_cached_doc("Payroll Entry", args.payroll_entry)

	try:
		salary_slips_exist_for = get_existing_salary_slips(employees, args)
		count = 0

		employees = list(set(employees) - set(salary_slips_exist_for))
		for emp in employees:
			slip_args = {
				"doctype": "Salary Slip",
				"employee": emp,
				"payroll_frequency": args.get("payroll_frequency"),
				"start_date": args.get("start_date"),
				"end_date": args.get("end_date"),
				"company": args.get("company"),
				"posting_date": args.get("posting_date"),
				"deduct_tax_for_unsubmitted_tax_exemption_proof": args.get(
					"deduct_tax_for_unsubmitted_tax_exemption_proof"
				),
				"payroll_entry": args.get("payroll_entry"),
				"exchange_rate": args.get("exchange_rate"),
				"currency": args.get("currency"),
			}
			frappe.get_doc(slip_args).insert()

			count += 1
			if publish_progress:
				frappe.publish_progress(
					count * 100 / len(employees),
					title=_("Creating Salary Slips..."),
				)

		payroll_entry.db_set({"status": "Submitted", "salary_slips_created": 1, "error_message": ""})

		if salary_slips_exist_for:
			frappe.msgprint(
				_(
					"Salary Slips already exist for employees {}, and will not be processed by this payroll."
				).format(frappe.bold(", ".join(emp for emp in salary_slips_exist_for))),
				title=_("Message"),
				indicator="orange",
			)

	except Exception as e:
		if not frappe.in_test:
			frappe.db.rollback()
		log_payroll_failure("creation", payroll_entry, e)

	finally:
		if not frappe.in_test:
			frappe.db.commit()  # nosemgrep
		frappe.publish_realtime("completed_salary_slip_creation", user=frappe.session.user)


def show_payroll_submission_status(submitted, unsubmitted, payroll_entry):
	if not submitted and not unsubmitted:
		frappe.msgprint(
			_(
				"No salary slip found to submit for the above selected criteria OR salary slip already submitted"
			)
		)
	elif submitted and not unsubmitted:
		frappe.msgprint(
			_("Salary Slips submitted for period from {0} to {1}").format(
				payroll_entry.start_date, payroll_entry.end_date
			),
			title=_("Success"),
			indicator="green",
		)
	elif unsubmitted:
		frappe.msgprint(
			_("Could not submit some Salary Slips: {}").format(
				", ".join(get_link_to_form("Salary Slip", entry) for entry in unsubmitted)
			),
			title=_("Failure"),
			indicator="red",
		)


def get_existing_salary_slips(employees, args):
	SalarySlip = frappe.qb.DocType("Salary Slip")

	return (
		frappe.qb.from_(SalarySlip)
		.select(SalarySlip.employee)
		.distinct()
		.where(
			(SalarySlip.docstatus != 2)
			& (SalarySlip.company == args.company)
			& (SalarySlip.payroll_entry == args.payroll_entry)
			& (SalarySlip.start_date >= args.start_date)
			& (SalarySlip.end_date <= args.end_date)
			& (SalarySlip.employee.isin(employees))
		)
	).run(pluck=True)


def submit_salary_slips_for_employees(payroll_entry, salary_slips, publish_progress=True):
	try:
		submitted = []
		unsubmitted = []
		frappe.flags.via_payroll_entry = True
		count = 0

		for entry in salary_slips:
			salary_slip = frappe.get_doc("Salary Slip", entry[0])
			if salary_slip.net_pay < 0:
				unsubmitted.append(entry[0])
			else:
				try:
					salary_slip.submit()
					submitted.append(salary_slip)
				except frappe.ValidationError:
					unsubmitted.append(entry[0])

			count += 1
			if publish_progress:
				frappe.publish_progress(
					count * 100 / len(salary_slips), title=_("Submitting Salary Slips...")
				)

		if submitted:
			payroll_entry.make_accrual_ledger_entries(submitted)
			payroll_entry.email_salary_slip(submitted)
			payroll_entry.db_set({"salary_slips_submitted": 1, "status": "Submitted", "error_message": ""})

		show_payroll_submission_status(submitted, unsubmitted, payroll_entry)

	except Exception as e:
		if not frappe.in_test:
			frappe.db.rollback()
		log_payroll_failure("submission", payroll_entry, e)

	finally:
		if not frappe.in_test:
			frappe.db.commit()  # nosemgrep
		frappe.publish_realtime("completed_salary_slip_submission", user=frappe.session.user)

	frappe.flags.via_payroll_entry = False


def get_employee_list(
	filters: frappe._dict,
	searchfield=None,
	search_string=None,
	fields: list[str] | None = None,
	as_dict=True,
	limit=None,
	offset=None,
	ignore_match_conditions=False,
) -> list:
	sal_struct = get_salary_structure(
		filters.company,
		filters.currency,
		filters.payroll_frequency,
	)

	if not sal_struct:
		return []

	emp_list = get_filtered_employees(
		sal_struct,
		filters,
		searchfield,
		search_string,
		fields,
		as_dict=as_dict,
		limit=limit,
		offset=offset,
		ignore_match_conditions=ignore_match_conditions,
	)

	if as_dict:
		employees_to_check = {emp.employee: emp for emp in emp_list}
	else:
		employees_to_check = {emp[0]: emp for emp in emp_list}

	return remove_payrolled_employees(employees_to_check, filters.start_date, filters.end_date)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def employee_query(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict
) -> list:
	filters = frappe._dict(filters)

	if not filters.payroll_frequency:
		frappe.throw(_("Select Payroll Frequency."))

	employee_list = get_employee_list(
		filters,
		searchfield=searchfield,
		search_string=txt,
		fields=["name", "employee_name"],
		as_dict=False,
		limit=page_len,
		offset=start,
	)

	return employee_list


def get_salary_withholdings(
	start_date: str,
	end_date: str,
	employee: str | None = None,
	pluck: str | None = None,
) -> list[str] | list[dict]:
	Withholding = frappe.qb.DocType("Salary Withholding")
	WithholdingCycle = frappe.qb.DocType("Salary Withholding Cycle")
	withheld_salaries = (
		frappe.qb.from_(Withholding)
		.join(WithholdingCycle)
		.on(WithholdingCycle.parent == Withholding.name)
		.select(
			Withholding.employee,
			Withholding.name.as_("salary_withholding"),
			WithholdingCycle.name.as_("salary_withholding_cycle"),
		)
		.where(
			(WithholdingCycle.from_date == start_date)
			& (WithholdingCycle.to_date == end_date)
			& (WithholdingCycle.docstatus == 1)
			& (WithholdingCycle.is_salary_released != 1)
		)
	)

	if employee:
		withheld_salaries = withheld_salaries.where(Withholding.employee == employee)

	if pluck:
		return withheld_salaries.run(pluck=pluck)
	return withheld_salaries.run(as_dict=True)


def get_salary_slip_payable_amounts(salary_slips: list[str]) -> dict[str, float]:
	"""Net payable (payroll currency) per salary slip: the amount credited to the payroll payable account
	by the accrual, i.e. earnings - deductions of the components included in accounting entries"""
	if not salary_slips:
		return {}

	SalaryDetail = frappe.qb.DocType("Salary Detail")
	rows = (
		frappe.qb.from_(SalaryDetail)
		.select(SalaryDetail.parent, SalaryDetail.parentfield, SalaryDetail.amount)
		.where(
			(SalaryDetail.parenttype == "Salary Slip")
			& (SalaryDetail.parent.isin(salary_slips))
			& (SalaryDetail.parentfield.isin(["earnings", "deductions"]))
			& (
				(SalaryDetail.do_not_include_in_total == 0)
				| ((SalaryDetail.do_not_include_in_total == 1) & (SalaryDetail.do_not_include_in_accounts == 0))
			)
		)
	).run(as_dict=True)

	amounts = dict.fromkeys(salary_slips, 0.0)
	for row in rows:
		sign = 1 if row.parentfield == "earnings" else -1
		amounts[row.parent] += sign * flt(row.amount)

	return amounts


def update_payroll_entry_payment_status(payroll_entry: str) -> None:
	"""Set Payroll Entry status to Paid when all its submitted salary slips are paid via HR Payment"""
	details = frappe.db.get_value("Payroll Entry", payroll_entry, ["docstatus", "status"], as_dict=True)
	if not details or details.docstatus != 1 or details.status not in ("Submitted", "Paid"):
		return

	salary_slips = frappe.get_all(
		"Salary Slip", filters={"payroll_entry": payroll_entry, "docstatus": 1}, pluck="hr_payment"
	)
	status = "Paid" if salary_slips and all(salary_slips) else "Submitted"
	if details.status != status:
		frappe.db.set_value("Payroll Entry", payroll_entry, "status", status)


def update_payment_reference(payment_doc, reference_row, cancel=False):
	"""HR Payment reference handler for Payroll Entry (`qcs_hrms_payment_reference_handlers` hook).

	A payment referencing the Payroll Entry itself pays the submitted, not withheld and unpaid salary
	slips of the payment's party (employee); on cancel those slips are unlinked again."""
	payroll_entry = reference_row.reference_name
	if frappe.db.get_value("Payroll Entry", payroll_entry, "docstatus") != 1:
		frappe.throw(_("Payroll Entry {0} must be submitted").format(frappe.bold(payroll_entry)))

	SalarySlip = frappe.qb.DocType("Salary Slip")
	if cancel:
		(
			frappe.qb.update(SalarySlip)
			.set(SalarySlip.hr_payment, None)
			.where((SalarySlip.payroll_entry == payroll_entry) & (SalarySlip.hr_payment == payment_doc.name))
		).run()
	else:
		(
			frappe.qb.update(SalarySlip)
			.set(SalarySlip.hr_payment, payment_doc.name)
			.where(
				(SalarySlip.payroll_entry == payroll_entry)
				& (SalarySlip.docstatus == 1)
				& (SalarySlip.status != "Withheld")
				& (SalarySlip.employee == payment_doc.party)
				& ((SalarySlip.hr_payment.isnull()) | (SalarySlip.hr_payment == ""))
			)
		).run()

	update_payroll_entry_payment_status(payroll_entry)
