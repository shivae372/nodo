"""
Surprising connections — the unexpected edges that reveal hidden architecture.

Graphify's headline trick, done deterministically and offline. We rank "bridge"
edges: links that cross a module/community boundary or a modality boundary
(code ↔ docs ↔ assets). An edge scores high when it is cross-modal, crosses
communities, touches a hub, and connects otherwise-distant nodes (low shared-
neighbour overlap). nodo surfaces the edge + structural evidence; the AI assistant
supplies the plain-English "why it matters." Pure stdlib — no embeddings, no LLM.
"""
from collections import defaultdict


def build_surprises(nodes, edges, communities=None, top=14):
    """Rank the most surprising cross-boundary edges in the unified graph.

    nodes: [{id,label,rel,kind}], edges: [{source,target,kind,prov?}],
    communities: optional {id: community}. Returns a ranked list of dicts."""
    by_id = {n['id']: n for n in nodes}
    comm = communities or {}
    adj = defaultdict(set)
    for e in edges:
        s, t = e.get('source'), e.get('target')
        if s is None or t is None or s == t:
            continue
        adj[s].add(t)
        adj[t].add(s)

    ranked, seen = [], set()
    for e in edges:
        s, t = e.get('source'), e.get('target')
        if s is None or t is None or s == t:
            continue
        key = (min(s, t), max(s, t))
        if key in seen:
            continue
        seen.add(key)
        a, b = by_id.get(s), by_id.get(t)
        if not a or not b:
            continue
        ka, kb = a.get('kind', 'code'), b.get('kind', 'code')
        if 'concept' in (ka, kb):           # concept↔doc edges are by construction, not surprising
            continue
        cross_modal = ka != kb
        ca, cb = comm.get(s), comm.get(t)
        cross_comm = ca is not None and cb is not None and ca != cb
        if not cross_modal and not cross_comm:   # same modality + same module = mundane
            continue
        na, nb = adj[s], adj[t]
        union = len(na | nb) or 1
        dist = 1.0 - (len(na & nb) / union)      # low shared-neighbour overlap = distant
        deg = len(na) + len(nb)
        score = ((3.0 if cross_modal else 0.0)
                 + (1.6 if cross_comm else 0.0)
                 + 1.2 * min(deg, 24) / 24.0
                 + 1.0 * dist)
        reason = []
        if cross_modal:
            reason.append(f"{ka}↔{kb} cross-modal link")
        if cross_comm:
            reason.append(f"bridges modules {ca}↔{cb}")
        if deg >= 16:
            reason.append(f"touches a hub (degree {deg})")
        if dist > 0.95 and not cross_modal:
            reason.append("far apart in the graph")
        ranked.append({
            'from': a.get('label'), 'to': b.get('label'),
            'from_file': a.get('rel'), 'to_file': b.get('rel'),
            'kind': e.get('kind', 'import'), 'prov': e.get('prov', ''),
            'score': round(score, 3),
            'reason': '; '.join(reason) or 'cross-boundary edge',
        })
    ranked.sort(key=lambda r: (-r['score'], r['from_file'] or '', r['to_file'] or ''))
    return ranked[:top]


def suggested_questions(hubs, surprises, knowledge, has_callgraph=False):
    """A few questions this graph is well-suited to answer (for the report header)."""
    qs = []
    if hubs:
        qs.append(f"What breaks if I change `{hubs[0].get('file')}`? (highest-blast-radius hub)")
    if surprises:
        s = surprises[0]
        qs.append(f"Why does `{s['from']}` connect to `{s['to']}`? (top surprising link)")
    topics = (knowledge or {}).get('topics') or []
    if topics:
        qs.append(f"How does the \"{topics[0]['name']}\" area work? (largest knowledge topic)")
    if has_callgraph:
        qs.append("What are the load-bearing functions? (most-called in the call graph)")
    qs.append("What should I fix first? (highest-confidence issues)")
    qs.append("How does <A> reach <B>? (import path between two files)")
    return qs[:6]


def architecture_insights(ctx, callgraph=None, symbol_graph=None):
    """Deterministic, pattern-based architecture notes for the advanced report."""
    lines = ["\n## Architecture insights\n"]
    hubs = ctx.get('hubs', [])
    iss = ctx.get('issues', [])
    gods = [h for h in hubs if h.get('edges', 0) >= 12][:5]
    if gods:
        lines.append("- **God objects (high coupling — refactor candidates):** "
                     + ', '.join(f"`{h['file']}` ({h['edges']} edges)" for h in gods))
    ncyc = sum(1 for i in iss if i.get('type') == 'Import cycle')
    if ncyc:
        lines.append(f"- **Import cycles:** {ncyc} — break these for cleaner layering")
    if callgraph and callgraph.get('available'):
        from . import callgraph as _cg
        top = _cg.top_hubs(callgraph, 5)
        if top:
            lines.append("- **Load-bearing functions (most-called):** "
                         + ', '.join(f"`{n}()` ×{d}" for n, d in top))
    if symbol_graph and symbol_graph.get('available'):
        c = symbol_graph['counts']
        lines.append(f"- **Symbol graph:** {c['symbols']} symbols / {c['calls']} call edges / "
                     f"{c['inherits']} inheritance edge(s) across {c['files']} files")
    ndead = sum(1 for i in iss if 'disconnected' in i.get('type', '').lower()
                or 'never imported' in i.get('type', '').lower())
    if ndead:
        lines.append(f"- **Possibly dead surface:** {ndead} disconnected / orphaned finding(s)")
    if len(lines) == 1:
        lines.append("- _No notable architectural smells._")
    return '\n'.join(lines) + '\n'


def render_markdown(surprises, questions):
    """A report section for nodo-report.md."""
    out = ["\n## Surprising connections\n",
           "_Cross-module / cross-modal links that grep and similarity search miss. "
           "nodo finds the edge + evidence; ask your assistant *why it matters*._\n"]
    if not surprises:
        out.append("_None surfaced (small or highly-modular graph)._\n")
    for i, s in enumerate(surprises, 1):
        prov = f", {s['prov']}" if s.get('prov') else ""
        out.append(f"{i}. **{s['from']}** → **{s['to']}**  "
                   f"(`{s['from_file']}` ↔ `{s['to_file']}`; {s['kind']}{prov})  \n"
                   f"   {s['reason']}")
    if questions:
        out.append("\n## Questions this map answers well\n")
        for q in questions:
            out.append(f"- {q}")
    return '\n'.join(out) + '\n'
