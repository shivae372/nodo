"""
Self-healing: nodo's diagnosis of its own blind spots.

Deterministic and offline — it inspects the scan result and reports where nodo
came up empty, so the agent on top (Claude) knows exactly what to teach it. The
output is *evidence*, paired with a ready-to-fill lesson template; Claude reads a
couple of example files and writes the lesson, then `--teach` makes it stick.

Three gap classes:
  1. unknown_language   — a code-looking extension nodo has no parser/lesson for
  2. silent_extraction  — a file nodo parsed but pulled nothing out of (grammar gap)
  3. unresolved_local   — local (relative) imports that didn't resolve to an edge
"""
import os
from pathlib import Path

from . import scanner
from . import lessons as _lessons

# Extensions that are legitimately structure-free (no defs/imports expected) —
# never flagged as a "silent extraction" gap.
_STRUCTURELESS = {
    '.css', '.scss', '.sass', '.less', '.styl', '.sql', '.sh', '.bash',
    '.json', '.md', '.txt', '.svg', '.html', '.htm', '.xml', '.yaml', '.yml',
}

# Non-code file extensions we should NOT nag about as "unknown languages".
_NOISE_EXTS = {
    '.json', '.lock', '.toml', '.yaml', '.yml', '.cfg', '.ini', '.env',
    '.txt', '.md', '.mdx', '.rst', '.lock', '.map', '.gitignore', '.gitattributes',
    '.editorconfig', '.csv', '.tsv', '.log', '.png', '.jpg', '.jpeg', '.gif',
    '.svg', '.ico', '.webp', '.pdf', '.mp4', '.mov', '.webm', '.mp3', '.wav',
    '.woff', '.woff2', '.ttf', '.eot', '.lockb', '.snap', '.min', '.d.ts',
    '.bmp', '.zip', '.gz', '.tar', '.pdf', '.xlsx', '.docx', '.pptx',
}


def _present_extensions(root, ignore_dirs):
    """Count every file extension under root (honoring the same dir pruning as the
    scanner), so we can spot code-looking extensions nodo doesn't handle."""
    counts = {}
    root = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ignore_dirs and (not d.startswith('.') or d == '.github')]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext:
                counts[ext] = counts.get(ext, 0) + 1
    return counts


def self_check(root, nodes, edges, file_texts, lessons, ignore_dirs):
    """Return {'gaps': [...], 'report': str, 'teach_template': {...}}.

    Pure inspection — no rescan, no network. `gaps` is machine-readable; `report`
    is the human/Claude-facing text; `teach_template` is a lesson skeleton for the
    top unknown language, ready for Claude to fill in and `--teach`."""
    outdeg = {n['rel']: 0 for n in nodes}
    id_to_rel = {n['id']: n['rel'] for n in nodes}
    for e in edges:
        s = id_to_rel.get(e.get('source'))
        if s is not None and e.get('kind', 'import') == 'import':
            outdeg[s] = outdeg.get(s, 0) + 1

    taught = _lessons.taught_extensions(lessons)
    known = scanner.SOURCE_EXTS | scanner.DOC_EXTS | scanner.ASSET_EXTS | taught

    gaps = []

    # 1) unknown languages — code-looking extensions with no parser and no lesson
    present = _present_extensions(root, ignore_dirs)
    unknown = []
    for ext, cnt in present.items():
        if ext in known or ext in _NOISE_EXTS:
            continue
        unknown.append((ext, cnt))
    unknown.sort(key=lambda x: (-x[1], x[0]))
    for ext, cnt in unknown[:8]:
        gaps.append({'kind': 'unknown_language', 'ext': ext, 'files': cnt,
                     'detail': f"{cnt} file(s) with extension '{ext}' — nodo has no "
                               f"parser or lesson for it; symbols/imports go unseen."})

    # build the resolution index once for the unresolved-local check
    rel_paths = [n['rel'] for n in nodes]
    idx = scanner._build_resolution_index(rel_paths)

    silent, unresolved = [], []
    for n in nodes:
        rel = n['rel']
        ext = os.path.splitext(rel)[1].lower()
        text = file_texts.get(rel, '')
        if not text:
            continue
        loc = n.get('loc', 0)
        defs = scanner_defs_count(rel, text)
        imps = scanner.extract_imports(rel, text)

        # 2) silent extraction — a parsed source file we pulled nothing from
        if (ext not in _STRUCTURELESS and loc >= 10
                and defs == 0 and len(imps) == 0 and outdeg.get(rel, 0) == 0):
            silent.append((rel, loc))

        # 3) unresolved LOCAL imports — relative paths that didn't resolve to a file
        local = [t for t in imps if t.startswith('.') or t.startswith('/')]
        miss = [t for t in local if scanner.resolve_import(rel, t, idx) is None]
        if len(miss) >= 2:
            unresolved.append((rel, len(miss), sorted(set(miss))[:4]))

    silent.sort(key=lambda x: (-x[1], x[0]))
    for rel, loc in silent[:10]:
        gaps.append({'kind': 'silent_extraction', 'file': rel, 'loc': loc,
                     'detail': f"{rel} ({loc} lines) parsed but yielded no symbols or "
                               f"imports — likely a grammar/pattern gap for its language."})

    unresolved.sort(key=lambda x: (-x[1], x[0]))
    for rel, cnt, ex in unresolved[:10]:
        gaps.append({'kind': 'unresolved_local', 'file': rel, 'count': cnt, 'examples': ex,
                     'detail': f"{rel}: {cnt} local import(s) didn't resolve "
                               f"(e.g. {', '.join(ex)}) — a resolver_hint can fix these."})

    report = _format(root, gaps, unknown)
    template = _teach_template(unknown[0][0] if unknown else None)
    return {'gaps': gaps, 'report': report, 'teach_template': template}


def scanner_defs_count(rel, text):
    """Count definitions nodo extracts for a file (AST / regex / lesson path)."""
    try:
        from .symbols import _defs_in
        return len(_defs_in(rel, text))
    except Exception:
        return 0


def _teach_template(ext):
    if not ext:
        return None
    name = ext.lstrip('.')
    return {
        'languages': {
            name: {
                'extensions': [ext],
                'category': 'lib',
                'def_patterns': ['<regex with ONE capture group around the symbol name>'],
                'import_patterns': ['<regex with ONE capture group around the import target>'],
                'note': f'Taught by Claude: how {name} declares definitions and imports.',
                'taught_by': 'claude',
            }
        }
    }


def _format(root, gaps, unknown):
    out = ['[nodo · self-check]']
    if not gaps:
        out.append('')
        out.append("No blind spots detected. nodo understands every language present "
                   "and resolved its local imports cleanly. ✔")
        return '\n'.join(out)
    by = {}
    for g in gaps:
        by.setdefault(g['kind'], []).append(g)

    if 'unknown_language' in by:
        out.append('')
        out.append('Languages nodo does NOT understand yet (no parser / no lesson):')
        for g in by['unknown_language']:
            out.append(f"  • {g['ext']}  — {g['files']} file(s)")
        out.append("  → Teach it: write a lesson (extensions + def/import regex) and run")
        out.append("    `nodo . --teach lesson.json`. A starter template is below.")

    if 'silent_extraction' in by:
        out.append('')
        out.append('Files nodo parsed but pulled NOTHING from (possible grammar gap):')
        for g in by['silent_extraction']:
            out.append(f"  • {g['file']}  ({g['loc']} lines)")
        out.append("  → If these are a language above, the lesson fixes them. If they're a "
                   "supported language, they may be data/generated — safe to ignore.")

    if 'unresolved_local' in by:
        out.append('')
        out.append('Local imports nodo could not resolve to a file:')
        for g in by['unresolved_local']:
            out.append(f"  • {g['file']}  ({g['count']}: {', '.join(g['examples'])})")
        out.append("  → Add `resolver_hints` to a lesson mapping the import string → real path,")
        out.append("    or confirm they're dynamic/computed (nodo stays silent rather than guess).")

    out.append('')
    out.append("nodo found the spots; you (Claude) supply the fix — read a couple of the "
               "files above, write the lesson, `--teach` it, and the next scan heals.")
    return '\n'.join(out)
