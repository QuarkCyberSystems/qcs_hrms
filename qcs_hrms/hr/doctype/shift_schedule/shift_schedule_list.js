frappe.listview_settings["Shift Schedule"] = {
	onload: (list_view) => qcs_hrms.add_shift_tools_button_to_list(list_view, "Assign Shift Schedule"),
};
