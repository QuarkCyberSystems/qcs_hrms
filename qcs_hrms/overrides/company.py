# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# Copyright (c) 2026, Quark Cyber System FZC
# License: GNU General Public License v3. See license.txt
"""HR fixtures and clean-up that run with the Company lifecycle.

These used to be Company doc_events of the ERP app; the Organization Company controller now calls them
directly (see `qcs_hrms.organization.doctype.company.company`).
"""

import json

import frappe
from frappe import _


def make_company_fixtures(doc, method=None):
	"""Regional setup and salary components for a new company or a company whose country changed."""
	if not doc.country:
		return

	run_regional_setup(doc.country)
	make_salary_components(doc.country)


def delete_company_fixtures():
	countries = frappe.get_all(
		"Company",
		distinct="True",
		pluck="country",
	)

	for country in countries:
		try:
			module_name = f"qcs_hrms.regional.{frappe.scrub(country)}.setup.uninstall"
			frappe.get_attr(module_name)()
		except (ImportError, AttributeError):
			# regional file or method does not exist
			pass
		except Exception as e:
			frappe.log_error("Unable to delete country fixtures for QCS HR")
			msg = _("Failed to delete defaults for country {0}.").format(frappe.bold(country))
			msg += "<br><br>" + _("{0}: {1}").format(frappe.bold(_("Error")), get_error_message(e))
			frappe.throw(msg, title=_("Country Fixture Deletion Failed"))


def run_regional_setup(country):
	try:
		module_name = f"qcs_hrms.regional.{frappe.scrub(country)}.setup.setup"
		frappe.get_attr(module_name)()
	except (ImportError, AttributeError):
		pass
	except Exception as e:
		frappe.log_error("Unable to setup country fixtures for QCS HR")
		msg = _("Failed to setup defaults for country {0}.").format(frappe.bold(country))
		msg += "<br><br>" + _("{0}: {1}").format(frappe.bold(_("Error")), get_error_message(e))
		frappe.throw(msg, title=_("Country Setup failed"))


def get_error_message(error) -> str:
	message_log = str(error)
	try:
		message_log = frappe.message_log.pop() if frappe.message_log else str(error)
		if isinstance(message_log, str):
			error_message = json.loads(message_log).get("message")
		else:
			error_message = message_log.get("message")
	except Exception:
		error_message = message_log

	return error_message


def make_salary_components(country):
	docs = []

	file_name = "salary_components.json"

	# default components already added
	if not frappe.db.exists("Salary Component", "Basic"):
		file_path = frappe.get_app_path("qcs_hrms", "payroll", "data", file_name)
		docs.extend(json.loads(read_data_file(file_path)))

	file_path = frappe.get_app_path("qcs_hrms", "regional", frappe.scrub(country), "data", file_name)
	docs.extend(json.loads(read_data_file(file_path)))

	for d in docs:
		try:
			doc = frappe.get_doc(d)
			doc.flags.ignore_permissions = True
			doc.flags.ignore_mandatory = True
			doc.insert(ignore_if_duplicate=True)
		except Exception:
			frappe.log_error("Error occurred while creating Salary Component")
		finally:
			frappe.clear_messages()


def read_data_file(file_path):
	try:
		with open(file_path) as f:
			return f.read()
	except OSError:
		return "[]"


def set_expense_claim_type_accounts(doc, method=None):
	"""Give every Expense Claim Type a default HR ledger expense account for this company."""
	if frappe.local.flags.ignore_chart_of_accounts or not frappe.db.table_exists("HR Ledger Account"):
		return

	expense_account = None
	for claim_type in frappe.get_all("Expense Claim Type", pluck="name"):
		if frappe.db.exists("Expense Claim Account", {"parent": claim_type, "company": doc.name}):
			continue

		if not expense_account:
			expense_account = get_or_create_expense_claim_account(doc)
			if not expense_account:
				return

		claim_type_doc = frappe.get_doc("Expense Claim Type", claim_type)
		claim_type_doc.append("accounts", {"company": doc.name, "default_account": expense_account})
		claim_type_doc.save(ignore_permissions=True)


def get_or_create_expense_claim_account(doc) -> str | None:
	# "Employee Expenses" is part of the default set from qcs_hrms.ledger.setup
	for account_name in ("Employee Expenses", _("Expense Claims")):
		account = frappe.db.get_value(
			"HR Ledger Account",
			{"account_name": account_name, "company": doc.name, "account_type": "Expense", "disabled": 0},
		)
		if account:
			return account

	return (
		frappe.get_doc(
			{
				"doctype": "HR Ledger Account",
				"account_name": _("Expense Claims"),
				"company": doc.name,
				"account_type": "Expense",
				"account_currency": doc.default_currency,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def handle_linked_docs(doc, method=None):
	delete_docs_with_company_field(doc)
	clear_company_field_for_single_doctypes(doc)


# HR records that only make sense for one company and are deleted along with it
COMPANY_DATA_TO_BE_DELETED = (
	"Salary Component Account",
	"Salary Structure",
	"Salary Structure Assignment",
	"Payroll Period",
	"Income Tax Slab",
	"Leave Period",
	"Leave Policy Assignment",
	"Employee Onboarding Template",
	"Employee Separation Template",
)


def delete_docs_with_company_field(doc, method=None):
	"""
	Deletes records from linked doctypes where the 'company' field matches the company's name
	"""
	for doctype in COMPANY_DATA_TO_BE_DELETED:
		if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, "company"):
			continue
		records_to_delete = frappe.get_all(doctype, filters={"company": doc.name}, pluck="name")
		if records_to_delete:
			frappe.db.delete(doctype, {"name": ["in", records_to_delete]})


def clear_company_field_for_single_doctypes(doc):
	"""
	Clears the 'company' value in Single doctypes where applicable
	"""
	single_docs = get_single_doctypes_with_company_field()
	if not single_docs:
		return

	singles = frappe.qb.DocType("Singles")
	(
		frappe.qb.update(singles)
		.set(singles.value, "")
		.where(singles.doctype.isin(single_docs))
		.where(singles.field == "company")
		.where(singles.value == doc.name)
	).run()


def get_single_doctypes_with_company_field():
	DocType = frappe.qb.DocType("DocType")
	DocField = frappe.qb.DocType("DocField")

	return (
		frappe.qb.from_(DocField)
		.select(DocField.parent)
		.where(
			(DocField.fieldtype == "Link")
			& (DocField.options == "Company")
			& (
				DocField.parent.isin(
					frappe.qb.from_(DocType)
					.select(DocType.name)
					.where((DocType.issingle == 1) & (DocType.module.isin(["HR", "Payroll"])))
				)
			)
		)
	).run(pluck=True)
