# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# Copyright (c) 2026, Quark Cyber System FZC
# License: GNU General Public License v3. See license.txt
"""Link-field search queries (ported from upstream)."""

import frappe
from frappe.desk.reportview import get_filters_cond, get_match_cond
from frappe.permissions import has_permission
from frappe.utils import unique


# searches for active employees
@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def employee_query(
	doctype,
	txt,
	searchfield,
	start,
	page_len,
	filters,
	reference_doctype: str | None = None,
	ignore_user_permissions: bool = False,
):
	doctype = "Employee"
	conditions = []
	fields = get_fields(doctype, ["name", "employee_name"])
	ignore_permissions = False

	if reference_doctype and ignore_user_permissions:
		ignore_permissions = has_ignored_field(reference_doctype, doctype) and has_permission(
			doctype,
			ptype="select" if frappe.only_has_select_perm(doctype) else "read",
		)

	search_conditions = " or ".join([f"{field} like %(txt)s" for field in fields])
	mcond = "" if ignore_permissions else get_match_cond(doctype)

	return frappe.db.sql(
		"""select {fields} from `tabEmployee`
		where status in ('Active', 'Suspended')
			and docstatus < 2
			and ({key} like %(txt)s or {search_conditions})
			{fcond} {mcond}
		order by
			(case when locate(%(_txt)s, name) > 0 then locate(%(_txt)s, name) else 99999 end),
			(case when locate(%(_txt)s, employee_name) > 0 then locate(%(_txt)s, employee_name) else 99999 end),
			idx desc,
			name, employee_name
		limit %(page_len)s offset %(start)s""".format(
			**{
				"fields": ", ".join(fields),
				"key": searchfield,
				"fcond": get_filters_cond(doctype, filters, conditions),
				"mcond": mcond,
				"search_conditions": search_conditions,
			}
		),
		{"txt": "%%%s%%" % txt, "_txt": txt.replace("%", ""), "start": start, "page_len": page_len},
	)


def has_ignored_field(reference_doctype, doctype):
	meta = frappe.get_meta(reference_doctype)
	for field in meta.fields:
		if not field.ignore_user_permissions:
			continue
		if field.fieldtype == "Link" and field.options == doctype:
			return True
		elif field.fieldtype == "Dynamic Link":
			options = meta.get_link_doctype(field.fieldname)
			if not options:
				continue
			if isinstance(options, str):
				options = options.split("\n")
			if doctype in options or "DocType" in options:
				return True

	return False


def get_fields(doctype, fields=None):
	if fields is None:
		fields = []
	meta = frappe.get_meta(doctype)
	fields.extend(meta.get_search_fields())

	if meta.title_field and meta.title_field.strip() not in fields:
		fields.insert(1, meta.title_field.strip())

	return unique(fields)
