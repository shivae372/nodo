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


# ── Incremental DETECTION cache ───────────────────────────────────────────────
# Detection (cross-file passes, cycle DFS, duplication windows) re-runs every scan
# even when the parse cache hits. This caches the *issue list* keyed by a signature
# over (every file's content hash + parser mode + detection-affecting config), so a
# rescan with no content/config change reuses it. Byte-identical inputs →
# byte-identical issues (detectors are deterministic), so reuse is always correct.
import hashlib  # noqa: E402

DETECT_NAME = 'detect-cache.json'
DETECT_VERSION = 1


def detect_signature(file_hashes, parser, config_blob):
    """Stable signature for a detection run. `file_hashes` is {rel: content-hash},
    `config_blob` any JSON-able dict of detection-affecting settings."""
    h = hashlib.sha1()
    h.update(f'v{DETECT_VERSION}|{parser}|'.encode())
    for rel in sorted(file_hashes):
        h.update(rel.encode('utf-8', 'ignore'))
        h.update(b'\0')
        h.update(str(file_hashes[rel]).encode())
        h.update(b'\n')
    h.update(json.dumps(config_blob, sort_keys=True, default=str).encode('utf-8', 'ignore'))
    return h.hexdigest()


def load_detect(out_dir):
    """Return (signature, issues) from the last detection run, or (None, None)."""
    p = Path(out_dir) / DETECT_NAME
    if not p.exists():
        return None, None
    try:
        data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
        if data.get('version') != DETECT_VERSION:
            return None, None
        return data.get('signature'), data.get('issues')
    except Exception:
        return None, None


def save_detect(out_dir, signature, issues):
    """Persist the detection result. Never raises."""
    p = Path(out_dir) / DETECT_NAME
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'version': DETECT_VERSION, 'signature': signature,
                                 'issues': issues}), encoding='utf-8')
    except Exception:
        pass


# ── Incremental INSIGHTS cache ────────────────────────────────────────────────
# Derived insights (entry flows, sensitive-surface map, API routes) are pure
# functions of the file set + contents + parser — the sensitive map alone is a
# 7-regex battery over every file's full text, the dominant cost of a no-change
# rescan on large repos. Cached under the same signature discipline as detection:
# identical inputs → identical outputs, any mismatch recomputes.

INSIGHTS_NAME = 'insights-cache.json'
INSIGHTS_VERSION = 1


def load_insights(out_dir):
    """Return (signature, payload) from the last insights run, or (None, None)."""
    p = Path(out_dir) / INSIGHTS_NAME
    if not p.exists():
        return None, None
    try:
        data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
        if data.get('version') != INSIGHTS_VERSION:
            return None, None
        return data.get('signature'), data.get('payload')
    except Exception:
        return None, None


def save_insights(out_dir, signature, payload):
    """Persist the insights payload. Never raises."""
    p = Path(out_dir) / INSIGHTS_NAME
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'version': INSIGHTS_VERSION, 'signature': signature,
                                 'payload': payload}), encoding='utf-8')
    except Exception:
        pass
