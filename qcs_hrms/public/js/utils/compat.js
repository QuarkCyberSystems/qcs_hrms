// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// Copyright (c) 2026, Quark Cyber System FZC
// License: GNU General Public License v3. See license.txt

// Client helpers QCS HR used to borrow from the ERP app's bundle.
frappe.provide("qcs_hrms");
frappe.provide("qcs_hrms.common");
frappe.provide("qcs_hrms.queries");

$.extend(qcs_hrms, {
	get_currency: function (company) {
		if (!company && window.cur_frm) company = cur_frm.doc.company;
		if (company)
			return (
				frappe.get_doc(":Company", company)?.default_currency ||
				frappe.boot.sysdefaults.currency
			);
		return frappe.boot.sysdefaults.currency;
	},
});

$.extend(qcs_hrms.queries, {
	employee: function () {
		return { query: "qcs_hrms.controllers.queries.employee_query" };
	},
});

$.extend(qcs_hrms.common, {
	get_tree_options: function (option) {
		// valid options for a tree filter, based on user permissions and boot docs
		let unscrub_option = frappe.model.unscrub(option);
		let user_permission = frappe.defaults.get_user_permissions();
		let options;

		if (user_permission && user_permission[unscrub_option]) {
			options = user_permission[unscrub_option].map((perm) => perm.doc);
		} else {
			options = $.map(locals[`:${unscrub_option}`] || {}, function (c) {
				return c.name;
			}).sort();
		}

		// filter unique values, as there may be multiple user permissions for any value
		return options.filter((value, index, self) => self.indexOf(value) === index);
	},

	get_tree_default: function (option) {
		let options = this.get_tree_options(option);
		if (options.includes(frappe.defaults.get_default(option))) {
			return frappe.defaults.get_default(option);
		}
		return options[0];
	},

	get_fiscal_year: function (date, with_dates = false, company = null) {
		// synchronous, like the original: callers use it to compute filter defaults
		let fiscal_year = "";
		frappe.call({
			method: "qcs_hrms.utils.compat.get_fiscal_year_for_date",
			type: "GET",
			args: { date: date || frappe.datetime.get_today(), company: company },
			async: false,
			callback: function (r) {
				if (r.message) fiscal_year = with_dates ? r.message : r.message[0];
			},
		});
		return fiscal_year;
	},

	copy_value_in_all_rows: function (doc, dt, dn, table_fieldname, fieldname) {
		var d = locals[dt][dn];
		if (d[fieldname]) {
			var cl = doc[table_fieldname] || [];
			for (var i = 0; i < cl.length; i++) {
				if (!cl[i][fieldname]) cl[i][fieldname] = d[fieldname];
			}
		}
		refresh_field(table_fieldname);
	},

	get_terms: function (tc_name, doc, callback) {
		if (tc_name) {
			return frappe.call({
				method: "qcs_hrms.organization.doctype.terms_and_conditions.terms_and_conditions.get_terms_and_conditions",
				args: {
					template_name: tc_name,
					doc: doc,
				},
				callback: function (r) {
					callback(r);
				},
			});
		}
	},

	map_current_doc: function (opts) {
		// pick source documents in a dialog and map them into the current form
		const frm = window.cur_frm;

		function _map() {
			return frappe.call({
				// POST: the full target doc can exceed GET url limits
				type: "POST",
				method: "frappe.model.mapper.map_docs",
				args: {
					method: opts.method,
					source_names: opts.source_name,
					target_doc: frm.doc,
					args: opts.args,
				},
				freeze: true,
				freeze_message: __("Mapping {0} ...", [opts.source_doctype]),
				callback: function (r) {
					if (!r.exc) {
						frappe.model.sync(r.message);
						frm.dirty();
						frm.refresh();
					}
				},
			});
		}

		let query_args = {};
		if (opts.get_query_filters) query_args.filters = opts.get_query_filters;
		if (opts.get_query_method) query_args.query = opts.get_query_method;
		if (query_args.filters || query_args.query) {
			opts.get_query = () => JSON.parse(JSON.stringify(query_args));
		}

		if (opts.source_doctype) {
			const d = new frappe.ui.form.MultiSelectDialog({
				doctype: opts.source_doctype,
				target: opts.target,
				date_field: opts.date_field || undefined,
				setters: opts.setters,
				read_only_setters: opts.read_only_setters,
				get_query: opts.get_query,
				add_filters_group: 1,
				allow_child_item_selection: opts.allow_child_item_selection,
				child_fieldname: opts.child_fieldname,
				child_columns: opts.child_columns,
				size: opts.size,
				action: function (selections, args) {
					if (!selections.length) {
						frappe.msgprint(__("Please select {0}", [opts.source_doctype]));
						return;
					}

					opts.source_name = [...new Set(selections)];
					if (opts.allow_child_item_selection) opts.args = args;

					d.dialog.hide();
					_map();
				},
			});

			return d;
		}

		if (opts.source_name) {
			opts.source_name = [opts.source_name];
			return _map();
		}
	},
});
