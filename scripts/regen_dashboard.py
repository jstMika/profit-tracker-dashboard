#!/usr/bin/env python3
"""Regenerate dashboard.html by injecting dashboard_data.json into the template.

Works both locally (~/Documents/Profit-Tracker) and on the GitHub Actions runner
(checkout root). The first existing root wins.
"""
import json
import os
import sys
from pathlib import Path


def find_root():
    # 1) Explicit override
    env = os.environ.get("PROFIT_TRACKER_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # 2) Current working directory (GitHub Actions checkout)
    cwd = Path.cwd()
    if (cwd / "scripts" / "dashboard_template.html").exists():
        return cwd
    # 3) Local Mac default
    mac = Path("~/Documents/Profit-Tracker").expanduser()
    if (mac / "scripts" / "dashboard_template.html").exists():
        return mac
    # 4) Sandbox mount fallback
    for cand in Path("/sessions").glob("*/mnt/Profit-Tracker"):
        if (cand / "scripts" / "dashboard_template.html").exists():
            return cand
    return cwd  # last resort — error will surface below


ROOT = find_root()
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
