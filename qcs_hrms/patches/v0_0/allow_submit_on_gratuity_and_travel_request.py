import frappe

from qcs_hrms.patches.v0_0.grant_hr_roles_on_advance_and_vehicle_log import copy_standard_perms_to_custom

# doctype: roles whose standard DocPerm now carries submit / cancel / amend
DOCTYPES = {
	"Gratuity": ("HR Manager", "HR User"),
	"Travel Request": ("System Manager", "HR Manager", "HR User"),
}
PTYPES = ("submit", "cancel", "amend")


def execute():
	"""Give the roles that create Gratuity and Travel Request the right to submit them.

	Both doctypes are submittable, but no role had submit permission, so only Administrator could
	submit them. The ESS user type setup creates Custom DocPerms, which replace the standard
	permissions, so the rights are also added to existing Custom DocPerm rows of these roles.
	HR Manager and HR User were also added to Travel Request; their standard rows are copied into
	the Custom DocPerms when those exist.
	"""
	copy_standard_perms_to_custom(("Travel Request",), ("HR Manager", "HR User"))
	for doctype, roles in DOCTYPES.items():
		CustomDocPerm = frappe.qb.DocType("Custom DocPerm")
		(
			frappe.qb.update(CustomDocPerm)
			.set(CustomDocPerm.submit, 1)
			.set(CustomDocPerm.cancel, 1)
			.set(CustomDocPerm.amend, 1)
			.where(
				(CustomDocPerm.parent == doctype)
				& (CustomDocPerm.role.isin(roles))
				& (CustomDocPerm.permlevel == 0)
				& (CustomDocPerm.create == 1)
			)
		).run()
		frappe.clear_cache(doctype=doctype)
