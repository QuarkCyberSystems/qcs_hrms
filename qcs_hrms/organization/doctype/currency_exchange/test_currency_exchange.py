# Copyright (c) 2026, Quark Cyber System FZC
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from qcs_hrms.utils.compat import get_exchange_rate


class TestCurrencyExchange(IntegrationTestCase):
	def test_exchange_rate_lookup(self):
		for date, rate in (("2016-01-01", 60.0), ("2016-01-10", 65.1)):
			if not frappe.db.exists("Currency Exchange", {"date": date, "from_currency": "USD", "to_currency": "INR"}):
				frappe.get_doc(
					{
						"doctype": "Currency Exchange",
						"date": date,
						"from_currency": "USD",
						"to_currency": "INR",
						"exchange_rate": rate,
					}
				).insert()

		self.assertEqual(get_exchange_rate("USD", "USD"), 1)
		self.assertEqual(get_exchange_rate("USD", "INR", "2016-01-05"), 60.0)
		self.assertEqual(get_exchange_rate("USD", "INR", "2016-01-15"), 65.1)
		self.assertAlmostEqual(get_exchange_rate("INR", "USD", "2016-01-15"), 1 / 65.1, places=6)
