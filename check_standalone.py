"""Static standalone check for qcs_hrms: no erpnext/hrms coupling, all Link targets resolvable.

Usage (from bench root): env/bin/python apps/qcs_hrms/check_standalone.py [path-prefix ...]
"""

import glob
import json
import os
import re
import sys

BENCH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APP = os.path.join(BENCH, "apps", "qcs_hrms", "qcs_hrms")
FRAPPE = os.path.join(BENCH, "apps", "frappe", "frappe")
LINK_TYPES = ("Link", "Table", "Table MultiSelect")


def doctype_jsons(root):
	for f in glob.glob(f"{root}/**/doctype/*/*.json", recursive=True):
		if os.path.basename(f) == os.path.basename(os.path.dirname(f)) + ".json":
			try:
				j = json.load(open(f))
			except Exception as e:
				yield f, {"__error__": str(e)}
				continue
			if j.get("doctype") == "DocType":
				yield f, j


def main(prefixes):
	known = {j["name"] for _, j in doctype_jsons(FRAPPE) if "name" in j}
	ours = {}
	problems = []
	for f, j in doctype_jsons(APP):
		if "__error__" in j:
			problems.append(f"INVALID JSON {f}: {j['__error__']}")
			continue
		ours[j["name"]] = f
	known |= set(ours)
	modules = set(open(os.path.join(APP, "modules.txt")).read().split("\n")) - {""}
	sel = lambda p: not prefixes or any(os.path.relpath(p, APP).startswith(x) for x in prefixes)
	for name, f in sorted(ours.items()):
		if not sel(f):
			continue
		j = json.load(open(f))
		if j.get("module") not in modules:
			problems.append(f"MODULE {name}: module '{j.get('module')}' not in modules.txt")
		for fld in j.get("fields", []):
			if fld.get("fieldtype") in LINK_TYPES and fld.get("options") and fld["options"] not in known:
				problems.append(f"LINK {name}.{fld['fieldname']} -> '{fld['options']}' does not exist")
	for p in glob.glob(f"{APP}/**/*", recursive=True):
		if not os.path.isfile(p) or not sel(p) or "/test" in p or "__pycache__" in p:
			continue
		if not p.endswith((".py", ".js", ".json", ".html", ".vue")):
			continue
		for i, line in enumerate(open(p, encoding="utf-8", errors="ignore"), 1):
			if re.search(r"\berpnext\b", line, re.I) and not line.strip().startswith(("#", "//")):
				problems.append(f"ERPNEXT {os.path.relpath(p, APP)}:{i}: {line.strip()[:120]}")
			if re.search(r"(?<![\w/.:-])hrms\.(?!localhost)", line):
				problems.append(f"HRMS {os.path.relpath(p, APP)}:{i}: {line.strip()[:120]}")
	print("\n".join(problems) or "OK")
	print(f"\n{len(problems)} problem(s)")


if __name__ == "__main__":
	main(sys.argv[1:])
