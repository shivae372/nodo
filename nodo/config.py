"""
Optional per-project configuration.

Looks for a `.nodo.json` at the project root. Everything is optional; a
project with no config still gets the full built-in analysis. Lets any codebase
add custom rules and tune scanning without touching the tool's source.

Example .nodo.json:

{
  "ignore_dirs": ["generated", "legacy"],
  "community_names": { "0": "API layer", "1": "UI" },
  "custom_rules": [
    {
      "name": "Inlined plan limit",
      "pattern": "\\b(50|500|2000)\\b",
      "include": "api/",
      "severity": "warn",
      "category": "Billing",
      "detail": "Numeric plan limit not imported from the single source of truth."
    }
  ]
}
"""
import json
from pathlib import Path

CONFIG_NAME = '.nodo.json'

DEFAULTS = {
    'ignore_dirs': [],
    'community_names': {},   # {"0": "Human name", ...}
    'custom_rules': [],
    'project_name': None,    # falls back to the directory name
    'max_file_kb': 512,
    # Drop findings you've decided are noise. Each entry is a string (matches the
    # issue type) or {"type": "...", "path": "..."} (both substring; an entry with
    # both must match both). Suppressed issues don't count toward the per-type cap.
    'suppress': [],
    # Re-weight built-in findings for your project, e.g. {"console.log left in
    # code": "warn"}. Values: "error" | "warn" | "info". Exact type match first,
    # else substring.
    'severity_overrides': {},
}


def load_config(root):
    cfg = dict(DEFAULTS)
    path = Path(root) / CONFIG_NAME
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8', errors='ignore'))
            for k in DEFAULTS:
                if k in data:
                    cfg[k] = data[k]
        except Exception as e:
            print(f'[nodo] warning: failed to parse {CONFIG_NAME}: {e}')
    return cfg


SAMPLE_CONFIG = {
    "project_name": None,
    "ignore_dirs": ["generated"],
    "community_names": {},
    "custom_rules": [
        {
            "name": "Hardcoded localhost URL",
            "pattern": "http://localhost",
            "severity": "info",
            "category": "Config",
            "detail": "Hardcoded localhost — read from an env var so other environments work."
        }
    ]
}


def write_sample_config(root):
    path = Path(root) / CONFIG_NAME
    if path.exists():
        return False
    path.write_text(json.dumps(SAMPLE_CONFIG, indent=2), encoding='utf-8')
    return True
