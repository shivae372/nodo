"""
Vibe check — a concise architectural narrative, deterministic and zero-dep.

Reads the facts nodo already computed (`nodo-context.json`) and turns them into a
plain-English "what does this codebase vibe like?" read: size, language, the shape
(god module vs modular vs layered), coupling, health, and themes. No LLM, no
network — it's a templated read of the graph, so it's fast enough for the default
(vibe-coder) mode. Claude can expand any line on request.
"""
import os
from collections import Counter

_LANG = {
    '.py': 'Python', '.js': 'JavaScript', '.jsx': 'JavaScript', '.mjs': 'JavaScript',
    '.cjs': 'JavaScript', '.ts': 'TypeScript', '.tsx': 'TypeScript', '.mts': 'TypeScript',
    '.cts': 'TypeScript', '.go': 'Go', '.rs': 'Rust', '.java': 'Java', '.rb': 'Ruby',
    '.php': 'PHP', '.cs': 'C#', '.swift': 'Swift', '.kt': 'Kotlin', '.c': 'C', '.h': 'C',
    '.cpp': 'C++', '.cc': 'C++', '.scala': 'Scala', '.dart': 'Dart', '.lua': 'Lua',
    '.vue': 'Vue', '.svelte': 'Svelte', '.sol': 'Solidity',
}


def _lang_name(exts):
    for ext, _ in exts.most_common():
        if ext in _LANG:
            return _LANG[ext]
    return 'mixed'


def vibe_check(ctx):
    """Return a deterministic architectural 'vibe' narrative from a context dict."""
    files = ctx.get('files', [])
    code = [f for f in files if f.get('kind', 'code') == 'code']
    nfiles = len(code)
    if not nfiles:
        return "[nodo · vibe]\n\nNot enough code here to read a vibe yet."
    edges = [e for e in ctx.get('edges', []) if e.get('kind', 'import') == 'import']
    ndeps = len(edges)
    exts = Counter(os.path.splitext(f['rel'])[1].lower() for f in code)
    lang = _lang_name(exts)
    loc = sum(f.get('loc', 0) for f in code)

    hubs = ctx.get('hubs', [])
    top_hub = hubs[0] if hubs else None
    degs = sorted((h.get('edges', 0) for h in hubs), reverse=True)
    god = None
    if top_hub and ndeps:
        share = top_hub.get('edges', 0) / max(ndeps, 1)
        nxt = degs[1] if len(degs) > 1 else 0
        if top_hub.get('edges', 0) >= 8 and (share >= 0.20 or (nxt and top_hub['edges'] >= 2.5 * nxt)):
            god = top_hub['file']

    cats = Counter(f.get('category', 'other') for f in code)
    layered = sum(1 for c in ('api', 'model', 'component', 'store', 'lib', 'page') if cats.get(c)) >= 3
    ncomm = len(ctx.get('modules', []) or [])
    coupling = ndeps / max(nfiles, 1)

    iss = (ctx.get('stats', {}) or {}).get('issues', {}) or {}
    nerr, nwarn = iss.get('error', 0), iss.get('warn', 0)
    ncyc = sum(1 for i in ctx.get('issues', []) if i.get('type') == 'Import cycle')
    topics = (ctx.get('knowledge', {}) or {}).get('topics', []) or []

    size = 'small' if nfiles < 25 else 'mid-sized' if nfiles < 150 else 'large'
    style = []
    if god:
        style.append(f"hub-and-spoke around `{os.path.basename(god)}`")
    if layered:
        style.append("layered (routes / services / models / ui)")
    if ncomm >= 4 and not god:
        style.append("modular")
    if not style:
        style.append("flat & simple" if nfiles < 25 else "organically grown")
    couple = 'loosely coupled' if coupling < 1.5 else 'moderately coupled' if coupling < 3 else 'tightly coupled'
    health = 'clean' if (nerr + nwarn) == 0 else 'a few rough edges' if (nerr + nwarn) <= 5 else 'carrying some debt'

    out = ['[nodo · vibe]', '',
           f"Vibe: **{' + '.join(style)}, {couple}, {health}.**", '',
           f"A {size} {lang} codebase — {nfiles} files, {ndeps} internal deps, ~{loc:,} LOC."]
    if god:
        out.append(f"`{god}` is the **god module** — most paths route through it, so changes there "
                   f"ripple widely (check `--what-if {god}` before refactoring).")
    elif top_hub:
        out.append(f"Load-bearing file: `{top_hub['file']}` ({top_hub.get('edges', 0)} connections).")
    if layered:
        out.append("It reads as a layered app — separate route / service / model / ui tiers.")
    if topics:
        out.append("Main themes (from docs): " + ', '.join(t['name'] for t in topics[:4]) + ".")
    bits = []
    if nerr:
        bits.append(f"{nerr} error(s)")
    if nwarn:
        bits.append(f"{nwarn} warning(s)")
    if ncyc:
        bits.append(f"{ncyc} import cycle(s)")
    out.append("Health: " + (', '.join(bits) if bits else "no blocking issues") + ".")
    if top_hub:
        out.append(f"Start in `{top_hub['file']}`.")
    out.append('')
    out.append("_Deterministic read of the graph — ask me to expand any line._")
    return '\n'.join(out)
