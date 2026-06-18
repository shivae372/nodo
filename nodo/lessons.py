"""
Self-learning: lessons Claude teaches nodo, persisted locally and applied
deterministically on every scan.

nodo never calls an LLM. It detects its own blind spots (see health.py) and
APPLIES the structured lessons a human or Claude writes — so a language nodo
didn't understand becomes first-class on the next scan, and a confirmed false
positive stays suppressed. All offline, zero-dependency, deterministic.

The division of labour, made durable:
  • nodo finds where it is blind  (health.self_check)
  • Claude tutors it              (writes a lesson: extensions + def/import regex,
                                   keep-alive corrections, or resolver hints)
  • nodo persists + applies it    (lessons.json, loaded and applied every scan)

Lesson file:  <out_dir>/lessons.json   (local; keep it out of git like the rest of .nodo)

Schema (every field optional):
{
  "version": 1,
  "languages": {
    "<name>": {
      "extensions": [".zig"],            # what files this lesson covers
      "category": "lib",                 # optional viewer category
      "def_patterns":   ["\\\\bfn\\\\s+(\\\\w+)"],     # regex, ONE capture group = symbol name
      "import_patterns":["@import\\\\(\\"([^\\"]+)\\""],  # regex, ONE group = import target
      "note": "...", "taught_by": "claude", "taught_at": <epoch>
    }
  },
  "keep_alive": ["path/or/Symbol", ...],          # suppress dead/disconnected/orphan findings
  "resolver_hints": {                              # help a failed import resolve to a real file
    "<import string>": "real/rel/path",            #   global: any importer of that string
    "<importer substr>::<import string>": "path"   #   scoped: only importers whose rel path
  }                                                #   contains <importer substr> — lets the SAME
}                                                  #   relative import (e.g. "./App") map to
                                                   #   different files in different base dirs.
# The hint VALUE may be an exact project path OR any import string nodo can
# resolve for that importer (so you don't have to know the stored rel path).
"""
import json
import os
import re
import time
from pathlib import Path

LESSONS_NAME = 'lessons.json'
SCHEMA_VERSION = 1

# Compiled-pattern cache so repeated extraction is cheap. Keyed by the raw
# pattern string; value is a compiled regex or None (invalid → ignored at runtime).
_COMPILED = {}


def _compile(pat):
    if pat in _COMPILED:
        return _COMPILED[pat]
    try:
        rx = re.compile(pat)
        if rx.groups < 1:          # need a capture group (the name / target)
            rx = None
    except re.error:
        rx = None
    _COMPILED[pat] = rx
    return rx


def empty():
    return {'version': SCHEMA_VERSION, 'languages': {}, 'keep_alive': [], 'resolver_hints': {}}


def _normalize(obj):
    """Coerce an arbitrary dict into the lesson schema, dropping junk fields."""
    out = empty()
    if not isinstance(obj, dict):
        return out
    langs = obj.get('languages')
    if isinstance(langs, dict):
        for name, spec in langs.items():
            if not isinstance(spec, dict):
                continue
            exts = [e.lower() if e.startswith('.') else '.' + e.lower()
                    for e in spec.get('extensions', []) if isinstance(e, str) and e.strip()]
            ls = {
                'extensions': sorted(set(exts)),
                'def_patterns': [p for p in spec.get('def_patterns', []) if isinstance(p, str)],
                'import_patterns': [p for p in spec.get('import_patterns', []) if isinstance(p, str)],
            }
            if isinstance(spec.get('category'), str):
                ls['category'] = spec['category']
            if isinstance(spec.get('grammar'), str) and spec['grammar'].strip():
                ls['grammar'] = spec['grammar'].strip()
            if isinstance(spec.get('note'), str):
                ls['note'] = spec['note']
            ls['taught_by'] = spec.get('taught_by', 'claude') if isinstance(spec.get('taught_by'), str) else 'claude'
            if isinstance(spec.get('taught_at'), (int, float)):
                ls['taught_at'] = int(spec['taught_at'])
            out['languages'][str(name)] = ls
    ka = obj.get('keep_alive')
    if isinstance(ka, list):
        out['keep_alive'] = sorted(set(s for s in ka if isinstance(s, str) and s.strip()))
    rh = obj.get('resolver_hints')
    if isinstance(rh, dict):
        out['resolver_hints'] = {str(k): str(v) for k, v in rh.items()
                                 if isinstance(k, str) and isinstance(v, str)}
    return out


def validate_lesson(obj):
    """Return (ok, errors, normalized). The 'heal safely' gate: every regex is
    compile-tested and must carry a capture group; bad input is reported, never
    silently applied."""
    errors = []
    if not isinstance(obj, dict):
        return False, ['lesson must be a JSON object'], empty()
    norm = _normalize(obj)
    for name, spec in norm['languages'].items():
        if not spec['extensions']:
            errors.append(f"language '{name}': no extensions (need e.g. \".zig\")")
        if not spec['def_patterns'] and not spec['import_patterns'] and not spec.get('grammar'):
            errors.append(f"language '{name}': needs at least one def_pattern, "
                          f"import_pattern, or a tree-sitter `grammar` name")
        for kind in ('def_patterns', 'import_patterns'):
            for p in spec[kind]:
                if _compile(p) is None:
                    errors.append(f"language '{name}': {kind} pattern is not a valid regex "
                                  f"with a capture group: {p!r}")
    ok = not errors
    return ok, errors, norm


def load_lessons(out_dir):
    """Read <out_dir>/lessons.json, tolerant of missing/malformed files."""
    path = Path(out_dir) / LESSONS_NAME
    if not path.exists():
        return empty()
    try:
        data = json.loads(path.read_text(encoding='utf-8', errors='ignore'))
    except Exception as e:
        print(f'[nodo] warning: failed to parse {LESSONS_NAME}: {e}')
        return empty()
    return _normalize(data)


def _write(out_dir, lessons):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = Path(out_dir) / LESSONS_NAME
    # deterministic on disk: sorted keys so re-teaching the same thing is a no-op diff
    path.write_text(json.dumps(lessons, indent=2, sort_keys=True), encoding='utf-8')
    return path


def merge_lessons(out_dir, new_obj):
    """Validate `new_obj` and merge it into the persisted lessons. Returns
    (ok, errors, summary). Languages are upserted by name; keep_alive and
    resolver_hints are unioned. This is the durable 'nodo learned it' step."""
    ok, errors, norm = validate_lesson(new_obj)
    if not ok:
        return False, errors, {}
    cur = load_lessons(out_dir)
    added_langs, updated_langs = [], []
    for name, spec in norm['languages'].items():
        if 'taught_at' not in spec:
            spec['taught_at'] = int(time.time())
        (updated_langs if name in cur['languages'] else added_langs).append(name)
        cur['languages'][name] = spec
    before_ka = set(cur['keep_alive'])
    cur['keep_alive'] = sorted(before_ka | set(norm['keep_alive']))
    cur['resolver_hints'].update(norm['resolver_hints'])
    cur['version'] = SCHEMA_VERSION
    _write(out_dir, cur)
    summary = {
        'languages_added': sorted(added_langs),
        'languages_updated': sorted(updated_langs),
        'keep_alive_added': sorted(set(norm['keep_alive']) - before_ka),
        'resolver_hints_added': sorted(norm['resolver_hints'].keys()),
        'extensions_now_understood': sorted(taught_extensions(cur)),
    }
    return True, [], summary


# ── applying lessons (consumed by scanner / symbols / detectors) ──────────────
def has_content(lessons):
    return bool(lessons and (lessons.get('languages') or lessons.get('keep_alive')
                             or lessons.get('resolver_hints')))


def taught_extensions(lessons):
    out = set()
    for spec in (lessons or {}).get('languages', {}).values():
        out |= set(spec.get('extensions', []))
    return out


def grammar_map(lessons):
    """{'.ext': 'grammar'} for languages whose lesson names a tree-sitter grammar."""
    out = {}
    for spec in (lessons or {}).get('languages', {}).values():
        g = spec.get('grammar')
        if g:
            for e in spec.get('extensions', []):
                out[e] = g
    return out


def ext_index(lessons):
    """{'.ext': (lang_name, spec)} — first lesson wins on a tie (sorted by name)."""
    idx = {}
    for name in sorted((lessons or {}).get('languages', {})):
        spec = lessons['languages'][name]
        for e in spec.get('extensions', []):
            idx.setdefault(e, (name, spec))
    return idx


def _ext(rel):
    return os.path.splitext(rel)[1].lower()


def extract_defs(rel, text, lessons):
    """[(name, line)] from the taught def_patterns for this file's language, or
    None if the extension isn't taught."""
    hit = ext_index(lessons).get(_ext(rel))
    if not hit:
        return None
    _name, spec = hit
    out = []
    for pat in spec.get('def_patterns', []):
        rx = _compile(pat)
        if rx is None:
            continue
        for m in rx.finditer(text):
            sym = m.group(1)
            if sym:
                out.append((sym, text[:m.start()].count('\n') + 1))
    return out


def extract_imports(rel, text, lessons):
    """[target strings] from the taught import_patterns, or None if not taught."""
    hit = ext_index(lessons).get(_ext(rel))
    if not hit:
        return None
    _name, spec = hit
    out = []
    for pat in spec.get('import_patterns', []):
        rx = _compile(pat)
        if rx is None:
            continue
        out.extend(g for g in rx.findall(text) if g)
    return out


def category_for(rel, lessons):
    hit = ext_index(lessons).get(_ext(rel))
    if hit and isinstance(hit[1].get('category'), str):
        return hit[1]['category']
    return None


def keep_alive_set(lessons):
    return set((lessons or {}).get('keep_alive', []))


def resolve_hint(target, lessons, importer_rel=None):
    """Return the hint VALUE for an unresolved import `target`, or None.

    Keys may be:
      - "<import string>"                 global — applies to any importer
      - "<importer substr>::<import>"     scoped — applies only when importer_rel
                                          contains <importer substr>. This is the
                                          fix for "the same './App' in different
                                          base dirs": a literal-string key alone
                                          can't disambiguate them; a scoped key can.
    The most specific match wins (scoped beats global; a longer scope beats a
    shorter one), so a precise hint overrides a catch-all. The returned value is
    still just a string — the caller (scanner.resolve_with_hint) resolves it to a
    real file for the importer, so the value need not be the exact stored path."""
    hints = (lessons or {}).get('resolver_hints', {})
    if not hints:
        return None
    importer = (importer_rel or '').replace('\\', '/')
    best = None  # (specificity, value)
    for key, val in hints.items():
        if '::' in key:
            scope, imp = (s.strip() for s in key.split('::', 1))
            if imp != target or not scope or scope not in importer:
                continue
            spec = 100 + len(scope)            # scoped beats global, longer = more specific
        else:
            if key != target:
                continue
            spec = 1
        if best is None or spec > best[0]:
            best = (spec, val)
    return best[1] if best else None
