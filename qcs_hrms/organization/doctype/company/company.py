# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# Copyright (c) 2026, Quark Cyber System FZC
# License: GNU General Public License v3. See license.txt
"""Slim Company for a standalone HR app: legal entity tree, HR defaults and HR ledger accounts."""

import frappe
import frappe.defaults
from frappe import _, bold
from frappe.cache_manager import clear_defaults_cache
from frappe.contacts.address_and_contact import load_address_and_contact
from frappe.desk.page.setup_wizard.setup_wizard import make_records
from frappe.utils.nestedset import NestedSet, get_root_of, rebuild_tree

DEFAULT_ACCOUNT_FIELDS = (
	"default_payroll_payable_account",
	"default_expense_claim_payable_account",
	"default_employee_advance_account",
	"default_bank_account",
	"default_cash_account",
	"round_off_account",
	"default_salary_expense_account",
)


class Company(NestedSet):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		abbr: DF.Data
		company_description: DF.TextEditor | None
		company_logo: DF.AttachImage | None
		company_name: DF.Data
		cost_center: DF.Data | None
		country: DF.Link
		date_of_establishment: DF.Date | None
		date_of_incorporation: DF.Date | None
		default_bank_account: DF.Link | None
		default_cash_account: DF.Link | None
		default_currency: DF.Link
		default_employee_advance_account: DF.Link | None
		default_expense_claim_payable_account: DF.Link | None
		default_holiday_list: DF.Link | None
		default_letter_head: DF.Link | None
		default_payroll_payable_account: DF.Link | None
		default_salary_expense_account: DF.Link | None
		email: DF.Data | None
		exchange_gain_loss_account: DF.Link | None
		fax: DF.Data | None
		fiscal_year_start_month: DF.Literal[
			"January",
			"February",
			"March",
			"April",
			"May",
			"June",
			"July",
			"August",
			"September",
			"October",
			"November",
			"December",
		]
		is_group: DF.Check
		lft: DF.Int
		old_parent: DF.Data | None
		parent_company: DF.Link | None
		phone_no: DF.Data | None
		registration_details: DF.Code | None
		rgt: DF.Int
		round_off_account: DF.Link | None
		round_off_cost_center: DF.Data | None
		tax_id: DF.Data | None
		website: DF.Data | None
	# end: auto-generated types

	nsm_parent_field = "parent_company"

	def onload(self):
		load_address_and_contact(self, "company")

	def validate(self):
		self.flags.country_change = self.is_new() or self.has_value_changed("country")
		self.validate_abbr()
		self.validate_parent_company()
		self.check_parent_changed()
		self.validate_default_accounts()
		self.validate_currency()

	def validate_abbr(self):
		if not self.abbr:
			self.abbr = "".join(c[0] for c in self.company_name.split()).upper()

		self.abbr = self.abbr.strip()

		if not self.abbr:
			frappe.throw(_("Abbreviation is mandatory"))

		if frappe.db.exists("Company", {"name": ("!=", self.name), "abbr": self.abbr}):
			frappe.throw(_("Abbreviation already used for another company"))

	def validate_parent_company(self):
		if self.parent_company:
			if self.parent_company == self.name:
				frappe.throw(_("Company cannot be its own parent"))
			if not frappe.db.get_value("Company", self.parent_company, "is_group"):
				frappe.throw(_("Parent Company must be a group company"))

	def check_parent_changed(self):
		self.flags.parent_company_changed = not self.is_new() and self.has_value_changed("parent_company")

	def validate_default_accounts(self):
		if not frappe.db.table_exists("HR Ledger Account"):
			return

		for fieldname in (*DEFAULT_ACCOUNT_FIELDS, "exchange_gain_loss_account"):
			account = self.get(fieldname)
			if not account:
				continue

			account_company = frappe.db.get_value("HR Ledger Account", account, "company")
			if account_company and account_company != self.name:
				frappe.throw(
					_("Account {0} does not belong to company: {1}").format(bold(account), bold(self.name))
				)

		if self.default_payroll_payable_account:
			account_currency = frappe.db.get_value(
				"HR Ledger Account", self.default_payroll_payable_account, "account_currency"
			)
			if account_currency and account_currency != self.default_currency:
				frappe.throw(
					_(
						"The currency of {0} should be same as the company's default currency. Please select another account."
					).format(bold(_("Default Payroll Payable Account")))
				)

	def validate_currency(self):
		if self.is_new() or not self.has_value_changed("default_currency"):
			return

		if frappe.db.table_exists("HR Ledger Entry") and frappe.db.exists(
			"HR Ledger Entry", {"company": self.name, "is_cancelled": 0}
		):
			frappe.throw(
				_(
					"Cannot change company's default currency, because there are existing ledger entries. Transactions must be cancelled to change the default currency."
				)
			)

	def on_update(self):
		NestedSet.on_update(self)

		if not frappe.db.exists("Department", {"company": self.name}):
			self.create_default_departments()

		if not frappe.local.flags.ignore_chart_of_accounts:
			self.create_default_accounts()

		if self.flags.country_change:
			from qcs_hrms.overrides.company import make_company_fixtures

			make_company_fixtures(self)

		if not frappe.local.flags.ignore_chart_of_accounts:
			from qcs_hrms.overrides.company import set_expense_claim_type_accounts

			set_expense_claim_type_accounts(self)

		if self.default_currency:
			frappe.db.set_value("Currency", self.default_currency, "enabled", 1)

		if self.flags.parent_company_changed:
			rebuild_tree("Company")

		if not frappe.db.get_default("company"):
			frappe.db.set_default("company", self.name)

		frappe.clear_cache()

	def create_default_accounts(self):
		"""Create the minimal HR ledger account set when any account default is missing."""
		if all(self.get(fieldname) for fieldname in DEFAULT_ACCOUNT_FIELDS):
			return

		from qcs_hrms.ledger.setup import create_default_accounts

		create_default_accounts(self.name)

		# pick up the defaults filled in by the ledger setup without dropping this doc's flags
		values = frappe.db.get_value("Company", self.name, list(DEFAULT_ACCOUNT_FIELDS), as_dict=True) or {}
		for fieldname, value in values.items():
			self.set(fieldname, value)

	def create_default_departments(self):
		root = get_root_of("Department") or "All Departments"
		departments = (
			_("Accounts"),
			_("Marketing"),
			_("Sales"),
			_("Purchase"),
			_("Operations"),
			_("Production"),
			_("Dispatch"),
			_("Customer Service"),
			_("Human Resources"),
			_("Management"),
			_("Quality Management"),
			_("Research & Development"),
			_("Legal"),
		)
		records = [
			{
				"doctype": "Department",
				"department_name": root,
				"is_group": 1,
				"parent_department": "",
				"__condition": lambda: not frappe.db.exists("Department", root),
			}
		]
		records += [
			{
				"doctype": "Department",
				"department_name": department,
				"parent_department": root,
				"company": self.name,
			}
			for department in departments
		]

		# Make root department with NSM updation
		make_records(records[:1])

		frappe.local.flags.ignore_update_nsm = True
		make_records(records)
		frappe.local.flags.ignore_update_nsm = False
		rebuild_tree("Department")

	def after_rename(self, olddn, newdn, merge=False):
		self.db_set("company_name", newdn)

		DefaultValue = frappe.qb.DocType("DefaultValue")
		(
			frappe.qb.update(DefaultValue)
			.set(DefaultValue.defvalue, newdn)
			.where((DefaultValue.defkey == "company") & (DefaultValue.defvalue == olddn))
		).run()

		clear_defaults_cache()

	def abbreviate(self):
		self.abbr = "".join(c[0].upper() for c in self.company_name.split())

	def on_trash(self):
		NestedSet.validate_if_child_exists(self)
		frappe.utils.nestedset.update_nsm(self)

		from qcs_hrms.overrides.company import handle_linked_docs

		handle_linked_docs(self)

		if frappe.db.table_exists("HR Ledger Account") and not (
			frappe.db.table_exists("HR Ledger Entry")
			and frappe.db.exists("HR Ledger Entry", {"company": self.name})
		):
			frappe.db.delete("HR Ledger Account", {"company": self.name})

		frappe.defaults.clear_default("company", value=self.name)

		frappe.db.delete("Employee", {"company": self.name})
		frappe.db.delete("Department", {"company": self.name})


def get_name_with_abbr(name, company):
	company_abbr = frappe.get_cached_value("Company", company, "abbr")
	parts = name.split(" - ")

	if parts[-1].lower() != company_abbr.lower():
		parts.append(company_abbr)

	return " - ".join(parts)


@frappe.whitelist()
def get_children(
	doctype: str, parent: str | None = None, company: str | None = None, is_root: bool = False
) -> list:
	if parent is None or parent == "All Companies":
		parent = ""

	filters = {"parent_company": parent} if parent else {"parent_company": ["is", "not set"]}

	# get_list, not get_all: it applies the caller's Company User Permissions, so a restricted user
	# sees only their own companies.
	return frappe.get_list(
		"Company",
		filters=filters,
		fields=["name as value", "is_group as expandable"],
	)


@frappe.whitelist()
def add_node():
	from frappe.desk.treeview import make_tree_args

	args = frappe.form_dict
	args = make_tree_args(**args)

	# `args` comes straight from form_dict, so without this the caller chooses the doctype created.
	args.doctype = "Company"

	if args.parent_company == "All Companies":
		args.parent_company = None

	frappe.get_doc(args).insert()


@frappe.whitelist()
def get_default_company_address(
	name: str, sort_key: str = "is_primary_address", existing_address: str | None = None
) -> str | None:
	if sort_key not in ["is_shipping_address", "is_primary_address"]:
		return None

	frappe.has_permission("Company", ptype="select", doc=name, throw=True)

	Address = frappe.qb.DocType("Address")
	DynamicLink = frappe.qb.DocType("Dynamic Link")
	out = (
		frappe.qb.from_(Address)
		.join(DynamicLink)
		.on(DynamicLink.parent == Address.name)
		.select(Address.name, Address[sort_key])
		.where(
			(DynamicLink.link_doctype == "Company")
			& (DynamicLink.link_name == name)
			& (Address.disabled == 0)
		)
	).run()

	if existing_address and existing_address in [d[0] for d in out]:
		return existing_address

	if out:
		return max(out, key=lambda x: x[1])[0]  # find max by sort_key

	return None
