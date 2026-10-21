// Copyright (c) 2026, Quark Cyber System FZC
// License: GNU General Public License v3. See license.txt

// Frappe-only sites have no company step in the setup wizard, so QCS HR adds one.
frappe.provide("qcs_hrms.setup");

frappe.setup.on("before_load", function () {
	const completed = frappe.boot.setup_wizard_completed_apps || [];
	if (completed.includes("qcs_hrms")) return;

	qcs_hrms.setup.slides_settings.map(frappe.setup.add_slide);
});

qcs_hrms.setup.months = [
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
];

// Fiscal year start month by country, everything else starts in January
qcs_hrms.setup.fiscal_year_start = {
	Afghanistan: "December",
	Australia: "July",
	Bangladesh: "July",
	Canada: "April",
	Egypt: "July",
	"Hong Kong": "April",
	India: "April",
	Iran: "March",
	Japan: "April",
	Kenya: "July",
	Myanmar: "April",
	"New Zealand": "April",
	Pakistan: "July",
	"South Africa": "March",
	Uganda: "July",
	"United Kingdom": "April",
};

qcs_hrms.setup.slides_settings = [
	{
		name: "qcs_company",
		title: __("Set up your company"),
		help: __("The legal entity your employees work for. You can add more companies later."),
		fields: [
			{
				fieldname: "company_name",
				label: __("Company Name"),
				fieldtype: "Data",
				reqd: 1,
			},
			{
				fieldname: "company_abbr",
				label: __("Company Abbreviation"),
				fieldtype: "Data",
				reqd: 1,
				description: __("A short code added to department and account names, like HR - AC."),
			},
			{ fieldtype: "Section Break" },
			{
				fieldname: "fiscal_year_start_month",
				label: __("Fiscal Year Starts In"),
				fieldtype: "Select",
				options: qcs_hrms.setup.months.join("\n"),
				default: "January",
				reqd: 1,
				description: __("Used for payroll periods, tax years and reports."),
			},
		],

		onload: function (slide) {
			slide.get_input("company_name").on("input", function () {
				const parts = (slide.get_field("company_name").get_value() || "").split(" ");
				const abbr = parts
					.map((p) => (p ? p.substr(0, 1) : ""))
					.join("")
					.toUpperCase()
					.slice(0, 5);
				slide.get_field("company_abbr").set_value(abbr);
			});
		},

		before_show: function () {
			const country = frappe.wizard?.values?.country;
			const month = qcs_hrms.setup.fiscal_year_start[country];
			if (month) this.get_field("fiscal_year_start_month").set_value(month);
		},

		validate: function () {
			if ((this.values.company_name || "").toLowerCase() == "company") {
				frappe.msgprint(__("Please pick a company name other than Company."));
				return false;
			}
			if (!this.values.company_abbr || this.values.company_abbr.length > 10) {
				frappe.msgprint(__("Company Abbreviation cannot have more than 10 characters."));
				return false;
			}
			return true;
		},
	},
];
