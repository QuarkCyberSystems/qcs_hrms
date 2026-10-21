import click

from qcs_hrms.setup import after_install as setup

BUG_REPORT_URL = "https://github.com/QuarkCyberSystems/qcs_hrms/issues/new"


def after_install():
	try:
		print("Setting up QCS HR...")
		setup()

		click.secho("Thank you for installing QCS HR!", fg="green")

	except Exception as e:
		click.secho(
			"Installation for QCS HR app failed due to an error."
			" Please try re-installing the app or"
			f" report the issue on {BUG_REPORT_URL} if not resolved.",
			fg="bright_red",
		)
		raise e
