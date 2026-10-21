import frappe

DOCTYPES = ("Employee Advance", "Vehicle Log")
ROLES = ("HR Manager", "HR User")
PTYPES = (
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"report",
	"export",
	"print",
	"email",
	"share",
)


def execute():
	"""Copy the HR role permissions of the standard DocPerms into existing Custom DocPerms.

	The ESS user type setup creates Custom DocPerms for these doctypes, which replace the standard
	permissions entirely, so HR roles added to the doctype JSON would otherwise not apply.
	"""
	for doctype in DOCTYPES:
		if not frappe.db.exists("Custom DocPerm", {"parent": doctype}):
			continue

		for role in ROLES:
			standard = frappe.db.get_value(
				"DocPerm",
				{"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
				PTYPES,
				as_dict=True,
			)
			if not standard or frappe.db.exists(
				"Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0}
			):
				continue

			frappe.get_doc(
				{
					"doctype": "Custom DocPerm",
					"parent": doctype,
					"parenttype": "DocType",
					"parentfield": "permissions",
					"role": role,
					"permlevel": 0,
					**standard,
				}
			).insert(ignore_permissions=True)

		frappe.clear_cache(doctype=doctype)
