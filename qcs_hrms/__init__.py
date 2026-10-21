import functools
import inspect

import frappe

__version__ = "0.0.1"


def refetch_resource(cache_key: str | list, user=None):
	frappe.publish_realtime(
		"hrms:refetch_resource",
		{"cache_key": cache_key},
		user=user or frappe.session.user,
		after_commit=True,
	)


def get_region(company=None):
	"""Return the country used to resolve regional overrides, based on flag,
	company or global settings. 

	You can also set the global company flag in `frappe.flags.company`.
	"""
	if not company:
		company = frappe.local.flags.company

	if company:
		return frappe.get_cached_value("Company", company, "country")

	return frappe.flags.country or frappe.get_system_settings("country")


def allow_regional(fn):
	"""Decorator to make a function regionally overridable. Lets
	HRMS functions can be overridden per region via the
	`regional_overrides` hook.

	Example:
	@qcs_hrms.allow_regional
	def myfunction():
	  pass"""

	@functools.wraps(fn)
	def caller(*args, **kwargs):
		overrides = frappe.get_hooks("regional_overrides", {}).get(get_region())
		function_path = f"{inspect.getmodule(fn).__name__}.{fn.__name__}"

		if not overrides or function_path not in overrides:
			return fn(*args, **kwargs)

		# Priority given to last installed app
		return frappe.get_attr(overrides[function_path][-1])(*args, **kwargs)

	return caller


def get_default_company(user=None):
	"""Return the user's default company, falling back to the global default or the only company."""
	from frappe.defaults import get_user_default_as_list

	if not user:
		user = frappe.session.user

	companies = get_user_default_as_list("company", user)
	if companies:
		default_company = companies[0]
	else:
		default_company = frappe.db.get_single_value("Global Defaults", "default_company") if frappe.db.exists(
			"DocType", "Global Defaults"
		) else None
		default_company = default_company or frappe.db.get_default("company")

	if not default_company:
		companies = frappe.get_all("Company", pluck="name", limit=2)
		if len(companies) == 1:
			default_company = companies[0]

	return default_company


def get_default_currency():
	"""Return the currency of the default company, else the system currency."""
	company = get_default_company()
	if company:
		return frappe.get_cached_value("Company", company, "default_currency")
	return frappe.db.get_default("currency")


def get_company_currency(company):
	"""Return the default currency of the given company."""
	return frappe.get_cached_value("Company", company, "default_currency") if company else None
