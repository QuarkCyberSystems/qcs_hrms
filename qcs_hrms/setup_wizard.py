# Copyright (c) 2026, Quark Cyber System FZC
# License: GNU General Public License v3. See license.txt
"""Setup wizard stage for a Frappe-only site: create the first Company and HR defaults."""

import frappe
from frappe import _


def get_setup_stages(args=None):
	args = frappe._dict(args or {})

	return [
		{
			"status": _("Setting up your company"),
			"fail_msg": _("Failed to create the company"),
			"tasks": [
				{
					"fn": setup_company,
					"args": args,
					"fail_msg": _("Failed to create the company"),
				}
			],
		},
		{
			"status": _("Setting up HR defaults"),
			"fail_msg": _("Failed to set up HR defaults"),
			"tasks": [
				{
					"fn": setup_hr_defaults,
					"args": args,
					"fail_msg": _("Failed to set up HR defaults"),
				}
			],
		},
	]


def setup_company(args):
	args = frappe._dict(args or {})
	company_name = (args.company_name or "").strip()
	if not company_name:
		return

	if not frappe.db.exists("Company", company_name):
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": company_name,
				"abbr": (args.company_abbr or "").strip() or None,
				"default_currency": args.currency or frappe.db.get_default("currency") or "USD",
				"country": args.country or frappe.db.get_default("country"),
				"fiscal_year_start_month": args.fiscal_year_start_month or "January",
			}
		)
		company.insert(ignore_permissions=True)

	frappe.db.set_default("company", company_name)
	frappe.defaults.set_global_default("company", company_name)


def setup_hr_defaults(args=None):
	"""Idempotent HR fixtures; safe to run again on a site where install already created them."""
	from qcs_hrms.setup import make_fixtures, set_hrms_as_default_app

	make_fixtures()
	set_hrms_as_default_app()
