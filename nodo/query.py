"""
Token-cheap blast-radius queries against a previously-generated nodo-context.json.

The point: instead of an AI agent reading ten files to figure out "what does this
touch and what breaks if I change it", it runs one query and gets a ~200-token
answer. This is where Nodo saves the most tokens — it answers the impact question
without the agent ever opening a file.

    python nodo.py <project> --query lib/auth.ts

Reads .nodo/nodo-context.json if present; if missing, the caller should run a
normal scan first.
"""
import json
import re
from pathlib import Path


def _load_context(out_dir):
    p = Path(out_dir) / 'nodo-context.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8', errors='ignore'))
    except Exception:
        return None


def _find_node(ctx, needle):
    """Match a file by exact rel path, suffix, or basename."""
    needle = needle.replace('\\', '/').strip()
    nodes = ctx.get('files', [])
    # exact
    for n in nodes:
        if n['rel'] == needle:
            return n
    # suffix (lets you pass a short path)
    cands = [n for n in nodes if n['rel'].endswith('/' + needle) or n['rel'].endswith(needle)]
    if len(cands) == 1:
        return cands[0]
    # basename (with extension)
    base = needle.split('/')[-1]
    bcands = [n for n in nodes if n['rel'].split('/')[-1] == base]
    if len(bcands) == 1:
        return bcands[0]
    # basename stem (no extension) — lets `--query AudioEngine` match AudioEngine.js
    stem = re.sub(r'\.[^./]+$', '', base)
    scands = [n for n in nodes
              if re.sub(r'\.[^./]+$', '', n['rel'].split('/')[-1]) == stem]
    if len(scands) == 1:
        return scands[0]
    if cands:
        return cands  # ambiguous — return list for the caller to disambiguate
    if bcands:
        return bcands
    if scands:
        return scands
    return None


def _bfs_path(start_id, goal_id, adj):
    """Shortest path start->goal over directed adjacency, or None."""
    from collections import deque
    prev = {start_id: None}
    q = deque([start_id])
    while q:
        cur = q.popleft()
        if cur == goal_id:
            chain = []
            while cur is not None:
                chain.append(cur)
                cur = prev[cur]
            return list(reversed(chain))
        for nxt in adj.get(cur, []):
            if nxt not in prev:
                prev[nxt] = cur
                q.append(nxt)
    return None


def path_between(out_dir, needle_a, needle_b):
    """Show the dependency chain connecting two files (a `path` query).

    Tries A->B following imports; if none, tries B->A; reports either, or that
    they are not connected through imports.
    """
    ctx = _load_context(out_dir)
    if ctx is None:
        return "No nodo-context.json found. Run a scan first: python nodo.py <project>"

    a = _find_node(ctx, needle_a)
    b = _find_node(ctx, needle_b)
    for label, hit, needle in (('A', a, needle_a), ('B', b, needle_b)):
        if hit is None:
            return f"No file matching '{needle}'."
        if isinstance(hit, list):
            return (f"'{needle}' is ambiguous ({len(hit)} matches): "
                    + ', '.join(n['rel'] for n in hit[:10]))

    by_id = {n['id']: n['rel'] for n in ctx['files']}
    out_adj = {}
    for e in ctx.get('edges', []):
        if e.get('kind', 'import') != 'import':
            continue  # trace import chains only, not doc/asset references
        out_adj.setdefault(e['source'], []).append(e['target'])

    fwd = _bfs_path(a['id'], b['id'], out_adj)
    if fwd:
        arrow = '\n  imports -> '.join(by_id[i] for i in fwd)
        return (f"{a['rel']} reaches {b['rel']} in {len(fwd) - 1} hop(s):\n  {arrow}")
    rev = _bfs_path(b['id'], a['id'], out_adj)
    if rev:
        arrow = '\n  imports -> '.join(by_id[i] for i in rev)
        return (f"{b['rel']} reaches {a['rel']} in {len(rev) - 1} hop(s):\n  {arrow}")
    return (f"{a['rel']} and {b['rel']} are not connected through the import graph "
            "(no directed path either way).")


def explain_concept(out_dir, concept, file_texts=None, docs=None, limit=12):
    """BM25 'where does <concept> live' search — zero-dependency, no model.

    Builds a BM25 index over file paths + content (code-aware tokenizer), expands
    the query with concept synonyms, and returns the best-matching files with
    their module and issue counts. file_texts (rel->text) gives content ranking;
    without it, ranks on path tokens alone.

    `docs` (rel->text of markdown/spec files) folds design documents into the same
    index, so `--explain "audio features"` surfaces the spec that defines them —
    this is what lets nodo judge code against intent, not just structure.
    """
    from .search import BM25, tokenize, expand_query
    ctx = _load_context(out_dir)
    if ctx is None:
        return "No nodo-context.json found. Run a scan first: python nodo.py <project>"

    files = ctx['files']
    issues_by_file = {}
    for i in ctx.get('issues', []):
        if i.get('file'):
            issues_by_file.setdefault(i['file'], []).append(i)

    # Build documents: path tokens repeated (path is a strong signal) + content.
    bm_docs = []
    node_by_rel = {}
    for n in files:
        # skip non-code nodes (doc/asset) — docs are added once via the `docs`
        # param below; assets have no searchable text. Prevents double-listing.
        if n.get('kind', 'code') != 'code':
            continue
        rel = n['rel']
        node_by_rel[rel] = n
        path_toks = tokenize(rel.replace('/', ' ')) * 3  # weight path 3x
        body_toks = tokenize(file_texts.get(rel, '')) if file_texts else []
        bm_docs.append((rel, path_toks + body_toks))

    # Fold in design docs so intent (not just code) is searchable.
    doc_rels = set()
    for rel, text in (docs or {}).items():
        doc_rels.add(rel)
        bm_docs.append((rel, tokenize(rel.replace('/', ' ')) * 3 + tokenize(text or '')))

    bm = BM25(bm_docs)
    qweights = expand_query(concept)
    if not qweights:
        return "Give a concept to explain, e.g. --explain authentication"
    ranked = bm.score(qweights)

    if not ranked:
        return (f"No files clearly relate to '{concept}'. Try a different term, or "
                "check the Security / Flows tabs in nodo.html.")

    matched_terms = ', '.join(sorted(qweights, key=lambda t: -qweights[t]))
    out = [f"Files & docs most related to '{concept}' "
           f"(BM25 over {len(bm_docs)} items; searched: {matched_terms}):", '']
    top = ranked[:limit]
    for rel, score in top:
        if rel in doc_rels:
            out.append(f"  {rel}  [doc]")
            continue
        n = node_by_rel.get(rel, {})
        iss = issues_by_file.get(rel)
        iss_tag = f"  ({len(iss)} issue(s))" if iss else ''
        out.append(f"  {rel}  [{n.get('category','?')}]{iss_tag}")

    from collections import Counter
    mods = Counter(node_by_rel[rel]['community'] for rel, _ in top if rel in node_by_rel)
    comm_names = {c['id']: c['name'] for c in ctx.get('communities', [])}
    if mods:
        out.append('')
        out.append('Concentrated in modules: ' + ', '.join(
            f"{comm_names.get(m, 'module ' + str(m))} ({cnt})"
            for m, cnt in mods.most_common(3)))
    out.append('')
    out.append("Tip: `--query <symbol-or-file>` for any of these to see its blast radius / references.")
    return '\n'.join(out)


def query_file(out_dir, needle):
    """Return a compact text report for one file's blast radius, or an error string."""
    ctx = _load_context(out_dir)
    if ctx is None:
        return ("No nodo-context.json found. Run a scan first:\n"
                "  python nodo.py <project>")

    hit = _find_node(ctx, needle)
    if hit is None:
        return f"No file matching '{needle}' in the graph."
    if isinstance(hit, list):
        lines = [f"'{needle}' is ambiguous — {len(hit)} matches:"]
        for n in hit[:20]:
            lines.append(f"  {n['rel']}")
        return '\n'.join(lines)

    rel = hit['rel']
    node_id = hit['id']

    # build adjacency from edges (ids)
    by_id = {n['id']: n for n in ctx['files']}
    dependents = []   # who imports this (breaks if I change its API)
    dependencies = [] # what this imports
    for e in ctx.get('edges', []):
        if e.get('kind', 'import') != 'import':
            continue  # reference (doc/asset) edges aren't import dependencies
        if e['target'] == node_id:
            dependents.append(by_id[e['source']]['rel'])
        if e['source'] == node_id:
            dependencies.append(by_id[e['target']]['rel'])

    issues_here = [i for i in ctx.get('issues', []) if i.get('file') == rel]

    out = []
    out.append(f"FILE  {rel}")
    out.append(f"      category={hit.get('category','?')}  loc={hit.get('loc','?')}  "
               f"edges={len(dependents) + len(dependencies)}")
    if (len(dependents) + len(dependencies)) > 0 and hit.get('hub_rank') and hit['hub_rank'] <= 15:
        out.append(f"      hub rank #{hit['hub_rank']} (high blast radius)")
    out.append("")
    out.append(f"DEPENDENTS ({len(dependents)}) — these import it; changing its exports may break them:")
    for d in sorted(dependents)[:25]:
        out.append(f"  <- {d}")
    if len(dependents) > 25:
        out.append(f"  ... +{len(dependents) - 25} more")
    out.append("")
    out.append(f"DEPENDENCIES ({len(dependencies)}) — this file imports:")
    for d in sorted(dependencies)[:25]:
        out.append(f"  -> {d}")
    if len(dependencies) > 25:
        out.append(f"  ... +{len(dependencies) - 25} more")

    # transitive change impact — everything that (directly or indirectly) imports
    # this file. The "if I change this, what could break" blast radius, full depth.
    rev = {}
    for e in ctx.get('edges', []):
        if e.get('kind', 'import') != 'import':
            continue
        rev.setdefault(e['target'], []).append(e['source'])
    from collections import deque
    impacted, dq, depth, maxd = set(), deque([node_id]), {node_id: 0}, 0
    while dq:
        cur = dq.popleft()
        for src in rev.get(cur, []):
            if src not in impacted and src != node_id:
                impacted.add(src)
                depth[src] = depth[cur] + 1
                maxd = max(maxd, depth[src])
                dq.append(src)
    if impacted:
        indirect = len(impacted) - len(dependents)
        out.append("")
        out.append(f"CHANGE IMPACT: {len(impacted)} file(s) transitively depend on this "
                   f"(up to {maxd} hop(s)" + (f"; {indirect} indirect" if indirect > 0 else "") + ").")
    if issues_here:
        out.append("")
        out.append(f"ISSUES ({len(issues_here)}):")
        for i in issues_here[:15]:
            ln = f":L{i['line']}" if i.get('line') else ''
            out.append(f"  [{i['severity']}] {i['type']}{ln} — {i['detail'][:90]}")
    return '\n'.join(out)
