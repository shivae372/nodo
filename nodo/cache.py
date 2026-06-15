"""
Incremental parse cache — zero-dependency.

Parsing (regex, and especially tree-sitter) is the per-file cost that dominates a
rescan. This cache stores each file's extracted imports keyed by (mtime, size,
parser mode), so on a rescan only the files that actually changed are re-parsed.
It is deliberately conservative: any mismatch (version, mode, mtime, size) misses
and re-parses, so a cached run produces byte-identical results to a clean run.
Corrupt/unreadable cache → treated as empty. Disable with --no-cache.
"""
import json
from pathlib import Path

CACHE_VERSION = 3
CACHE_NAME = 'cache.json'


def load(out_dir):
    """Return the {rel: entry} file map from a prior scan, or {} if unusable."""
    p = Path(out_dir) / CACHE_NAME
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
        if data.get('version') != CACHE_VERSION:
            return {}
        files = data.get('files')
        return files if isinstance(files, dict) else {}
    except Exception:
        return {}


def save(out_dir, files):
    """Persist the file map. Never raises."""
    p = Path(out_dir) / CACHE_NAME
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'version': CACHE_VERSION, 'files': files}),
                     encoding='utf-8')
    except Exception:
        pass
