# Copyright (c) 2026, Quark Cyber System FZC
# License: GNU General Public License v3. See license.txt
"""Replacements for the ERP helpers upstream Frappe HR used to import.

qcs_hrms runs on Frappe alone, so the handful of generic utilities that upstream HR code
borrowed from the ERP app live here with the same call shapes.
"""

from datetime import date, timedelta

import frappe
from frappe import _
from frappe.desk.reportview import build_match_conditions
from frappe.query_builder.functions import Coalesce
from frappe.utils import add_months, add_years, cint, comma_or, flt, getdate, nowdate

MONTHS = (
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
)


class FiscalYearError(frappe.ValidationError):
	pass


def validate_status(status, options):
	if status not in options:
		frappe.throw(_("Status must be one of {0}").format(comma_or(options)))


def get_link_fields_grouped_by_option(doctype: str) -> dict[str, list[str]]:
	meta = frappe.get_meta(doctype)
	link_fields_map = {}
	for df in meta.fields:
		if df.fieldtype == "Link" and df.options and not df.ignore_user_permissions:
			link_fields_map.setdefault(df.options, []).append(df.fieldname)
	return link_fields_map


def build_qb_match_conditions(doctype, user=None) -> list:
	"""User permission match conditions for `doctype` as a list of query builder criteria."""
	match_filters = build_match_conditions(doctype, user, False)
	link_fields_map = get_link_fields_grouped_by_option(doctype)
	criterion = []
	apply_strict_user_permissions = frappe.get_system_settings("apply_strict_user_permissions")

	if match_filters:
		_dt = frappe.qb.DocType(doctype)

		for match_filter in match_filters:
			for link_option, allowed_values in match_filter.items():
				fieldnames = link_fields_map.get(link_option, [])
				cond = None

				if link_option == doctype:
					cond = _dt["name"].isin(allowed_values)

				for fieldname in fieldnames:
					field = _dt[fieldname]
					cond = field.isin(allowed_values)

					if not apply_strict_user_permissions:
						cond = (Coalesce(field, "") == "") | cond

				if cond:
					criterion.append(cond)

	return criterion


def get_fiscal_year_start_month(company: str | None = None) -> int:
	"""Month number (1-12) a company's fiscal year starts in. Defaults to January."""
	if not company:
		import qcs_hrms

		company = qcs_hrms.get_default_company()

	month = frappe.get_cached_value("Company", company, "fiscal_year_start_month") if company else None
	if not month:
		return 1
	if isinstance(month, int) or str(month).isdigit():
		return min(max(cint(month), 1), 12)
	return MONTHS.index(month) + 1 if month in MONTHS else 1


def get_fiscal_year(date=None, company=None, as_dict=False, **kwargs):
	"""Fiscal year containing `date`, derived from `Company.fiscal_year_start_month`.

	Returns `(name, year_start_date, year_end_date)`, or a dict with `name`, `year`,
	`year_start_date` and `year_end_date` when `as_dict` is set (same shape as upstream).
	The name is the start year ("2026") for calendar years, else "2026-2027".
	"""
	transaction_date = getdate(date or nowdate())
	start_month = get_fiscal_year_start_month(company)

	start_year = transaction_date.year if transaction_date.month >= start_month else transaction_date.year - 1
	year_start_date = getdate(f"{start_year}-{start_month:02d}-01")
	year_end_date = add_months(year_start_date, 12) - timedelta(days=1)

	name = str(start_year) if start_month == 1 else f"{start_year}-{start_year + 1}"

	if as_dict:
		return frappe._dict(
			name=name, year=name, year_start_date=year_start_date, year_end_date=getdate(year_end_date)
		)

	return name, year_start_date, getdate(year_end_date)


def get_fiscal_year_dates(fiscal_year: str, company: str | None = None) -> tuple[date, date]:
	"""Start and end date of a fiscal year name returned by `get_fiscal_year`."""
	start_year = cint(str(fiscal_year).split("-")[0])
	if not start_year:
		frappe.throw(_("Invalid Fiscal Year {0}").format(fiscal_year), FiscalYearError)
	start_month = get_fiscal_year_start_month(company)
	start = getdate(f"{start_year}-{start_month:02d}-01")
	return start, getdate(add_years(start, 1) - timedelta(days=1))


def daterange(start_date, end_date):
	start_date, end_date = getdate(start_date), getdate(end_date)
	for n in range(int((end_date - start_date).days) + 1):
		yield start_date + timedelta(n)


def set_by_naming_series(doctype, fieldname, naming_series, hide_name_field=True, make_mandatory=1):
	"""Change a doctype's naming to user naming series (port of the upstream naming utility)."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter
	from frappe.model.naming import get_default_naming_series

	def set_property(field, prop, value):
		make_property_setter(doctype, field, prop, value, "Check", validate_fields_for_doctype=False)

	if naming_series:
		set_property("naming_series", "hidden", 0)
		set_property("naming_series", "reqd", make_mandatory)

		default_series = get_default_naming_series(doctype)
		if default_series:
			table = frappe.qb.DocType(doctype)
			(
				frappe.qb.update(table)
				.set(table.naming_series, default_series)
				.where(Coalesce(table.naming_series, "") == "")
			).run()

		if hide_name_field:
			set_property(fieldname, "reqd", 0)
			set_property(fieldname, "hidden", 1)
	else:
		set_property("naming_series", "reqd", 0)
		set_property("naming_series", "hidden", 1)

		if hide_name_field:
			set_property(fieldname, "hidden", 0)
			set_property(fieldname, "reqd", 1)

			table = frappe.qb.DocType(doctype)
			(
				frappe.qb.update(table).set(table[fieldname], table.name).where(Coalesce(table[fieldname], "") == "")
			).run()


def get_exchange_rate(from_currency, to_currency, transaction_date=None, args=None):
	"""Latest `Currency Exchange` rate on or before `transaction_date`.

	Returns 1 for the same currency, None when either currency is missing, and 0 when no rate is
	recorded. Falls back to the inverse of a recorded reverse rate."""
	if not (from_currency and to_currency):
		return None
	if from_currency == to_currency:
		return 1

	transaction_date = getdate(transaction_date or nowdate())

	def latest_rate(from_cur, to_cur):
		filters = [
			["date", "<=", transaction_date],
			["from_currency", "=", from_cur],
			["to_currency", "=", to_cur],
		]
		if args == "for_buying":
			filters.append(["for_buying", "=", 1])
		elif args == "for_selling":
			filters.append(["for_selling", "=", 1])

		rate = frappe.get_all(
			"Currency Exchange",
			filters=filters,
			fields=["exchange_rate"],
			order_by="date desc",
			limit=1,
			pluck="exchange_rate",
		)
		return flt(rate[0]) if rate else 0

	rate = latest_rate(from_currency, to_currency)
	if rate:
		return rate

	reverse = latest_rate(to_currency, from_currency)
	return flt(1 / reverse, 9) if reverse else 0


def delete_events(ref_type, ref_name):
	"""Delete calendar Events whose participants reference the given document."""
	Event = frappe.qb.DocType("Event")
	Participants = frappe.qb.DocType("Event Participants")
	events = (
		frappe.qb.from_(Event)
		.join(Participants)
		.on(Event.name == Participants.parent)
		.select(Event.name)
		.distinct()
		.where((Participants.reference_doctype == ref_type) & (Participants.reference_docname == ref_name))
	).run(pluck=True)

	if events:
		frappe.delete_doc("Event", events, for_reload=True)


@frappe.whitelist()
def get_fiscal_year_for_date(date: str | None = None, company: str | None = None) -> tuple:
	"""Client-side `qcs_hrms.common.get_fiscal_year`: (name, start_date, end_date)."""
	return get_fiscal_year(date, company=company)
