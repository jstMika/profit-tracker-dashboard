#!/usr/bin/env python3
"""Regenerate dashboard.html by injecting dashboard_data.json into the template."""
import json
import sys
from pathlib import Path

ROOT = Path("~/Documents/Profit-Tracker").expanduser()
TEMPLATE = ROOT / "scripts" / "dashboard_template.html"
DATA = ROOT / "dashboard_data.json"
OUT = ROOT / "dashboard.html"

if not TEMPLATE.exists():
    sys.exit(f"Missing template: {TEMPLATE}")
if not DATA.exists():
    sys.exit(f"Missing data: {DATA}")

html = TEMPLATE.read_text()
data = json.loads(DATA.read_text())
data_str = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
OUT.write_text(html.replace('__DATA_PLACEHOLDER__', data_str))
print(f"Wrote {OUT} ({len(OUT.read_text())} bytes, {len(data.get('days', []))} days)")
