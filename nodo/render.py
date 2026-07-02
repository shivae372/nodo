"""
Rendering — emits the self-contained HTML viewer plus AI-agent artifacts.

Outputs (into the chosen output dir):
  - nodo.html           interactive viewer (topology + Issues + AI Context tabs)
  - nodo-issues.txt     plain-text issue list
  - nodo-context.json   machine-readable: stats, hubs, communities, issues
  - nodo-context.md     token-cheap summary for dropping into an LLM context

The HTML inlines vis-network from a local copy (vendored next to this file) so
the viewer works fully offline with no CDN dependency.
"""
import json
import datetime
from collections import defaultdict
from pathlib import Path

CAT_STYLE = {
    'api':       {'bg': '#3b82f6', 'bd': '#1d4ed8', 'name': 'API / Route'},
    'component': {'bg': '#ec4899', 'bd': '#be185d', 'name': 'Component'},
    'page':      {'bg': '#64748b', 'bd': '#334155', 'name': 'Page / Screen'},
    'lib':       {'bg': '#6366f1', 'bd': '#4338ca', 'name': 'Lib / Util'},
    'store':     {'bg': '#84cc16', 'bd': '#4d7c0f', 'name': 'State / Hook'},
    'model':     {'bg': '#14b8a6', 'bd': '#0d9488', 'name': 'Model / Schema'},
    'style':     {'bg': '#f59e0b', 'bd': '#d97706', 'name': 'Style'},
    'config':    {'bg': '#a855f7', 'bd': '#7c3aed', 'name': 'Config'},
    'test':      {'bg': '#22c55e', 'bd': '#15803d', 'name': 'Test'},
    'doc':       {'bg': '#0ea5e9', 'bd': '#0369a1', 'name': 'Doc / Spec'},
    'asset':     {'bg': '#f97316', 'bd': '#c2410c', 'name': 'Asset (image/PDF/video)'},
    'concept':   {'bg': '#eab308', 'bd': '#a16207', 'name': 'Concept (knowledge)'},
    'other':     {'bg': '#94a3b8', 'bd': '#64748b', 'name': 'Other'},
}


def _sev_size(d):
    if d >= 40: return 30
    if d >= 20: return 24
    if d >= 10: return 18
    if d >= 5:  return 13
    return 9


def render(out_dir, project_name, abs_root, nodes, edges, communities,
           comm_summaries, issues, community_names=None,
           flows=None, sensitive=None, apis=None, docs=None, assets=None,
           diagnostics=None, parser=None, knowledge=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    community_names = community_names or {}
    flows = flows or []
    sensitive = sensitive or []
    apis = apis or []
    docs = docs or {}
    assets = assets or []
    diagnostics = diagnostics or {}
    knowledge = knowledge or {}

    build_date = datetime.date.today().isoformat()
    build_ts = datetime.datetime.now().isoformat(timespec='seconds')

    # degree + rank
    deg = defaultdict(int)
    for e in edges:
        deg[e['source']] += 1
        deg[e['target']] += 1
    for n in nodes:
        deg.setdefault(n['id'], 0)
    ranked = sorted(deg.items(), key=lambda kv: kv[1], reverse=True)
    rank_of = {nid: i + 1 for i, (nid, _) in enumerate(ranked)}

    id_to_node = {n['id']: n for n in nodes}
    issue_by_file = defaultdict(list)
    for iss in issues:
        if iss.get('file'):
            issue_by_file[iss['file']].append(iss)

    n_err = sum(1 for x in issues if x['severity'] == 'error')
    n_warn = sum(1 for x in issues if x['severity'] == 'warn')
    n_info = sum(1 for x in issues if x['severity'] == 'info')

    # hubs (top CODE nodes only — docs/assets are not "load-bearing files").
    # God-node hygiene: barrel/wiring files (__init__.py, index.ts, mod.rs) top raw
    # degree rankings while saying little about architecture — an agent's orientation
    # budget is better spent on real modules. Skip them while filling the list; if a
    # project is nearly all barrels (rare), fall back to them, flagged 'mechanical'.
    code_ids = {n['id'] for n in nodes if n.get('kind', 'code') == 'code'}

    def _mechanical(rel):
        base = rel.rsplit('/', 1)[-1].split('.', 1)[0]
        return base in ('__init__', 'index', 'mod')

    hub_list, _barrels = [], []
    for nid, d in ranked:
        if d == 0:
            break
        if nid not in code_ids:
            continue
        n = id_to_node[nid]
        row = {'label': n['label'], 'file': n['rel'], 'edges': d}
        (_barrels if _mechanical(n['rel']) else hub_list).append(row)
        if len(hub_list) >= 15:
            break
    if len(hub_list) < 5 and _barrels:
        for row in _barrels:
            row['mechanical'] = True
            hub_list.append(row)
            if len(hub_list) >= 15:
                break
        hub_list.sort(key=lambda h: -h['edges'])

    comm_display = []
    for c in comm_summaries:
        name = community_names.get(str(c['id']), c['name'])
        comm_display.append({**c, 'name': name})

    # ── artifacts: JSON + MD + TXT ──
    _write_artifacts(out_dir, project_name, build_ts, nodes, edges, communities,
                     comm_display, issues, hub_list, deg, rank_of, flows, sensitive, apis,
                     docs, assets, diagnostics, parser, knowledge)

    # ── vis nodes/edges ──
    vis_nodes = _build_vis_nodes(nodes, deg, rank_of, communities, issue_by_file)
    vis_edges = []
    for e in edges:
        if e.get('kind') == 'reference':
            # doc/asset reference edges: dashed violet so they read as "describes /
            # references", distinct from solid code import edges.
            vis_edges.append({'from': e['source'], 'to': e['target'], 'dashes': True,
                              'color': {'color': '#a78bfa', 'opacity': 0.55}, 'width': 1})
        else:
            vis_edges.append({'from': e['source'], 'to': e['target'],
                              'color': {'color': '#cbd5e1', 'opacity': 0.35}, 'width': 1})

    stats = {
        'nodes': len(nodes), 'edges': len(edges),
        'communities': len(set(communities.values())) if communities else 0,
        'issues': {'total': len(issues), 'error': n_err, 'warn': n_warn, 'info': n_info},
    }

    html = _build_html(project_name, abs_root, build_date, build_ts,
                       vis_nodes, vis_edges, issues, stats, hub_list,
                       comm_display, nodes, deg, flows, sensitive, apis)
    (out_dir / 'nodo.html').write_text(html, encoding='utf-8')
    return {
        'html': str(out_dir / 'nodo.html'),
        'json': str(out_dir / 'nodo-context.json'),
        'md': str(out_dir / 'nodo-context.md'),
        'txt': str(out_dir / 'nodo-issues.txt'),
        'stats': stats,
    }


def _build_vis_nodes(nodes, deg, rank_of, communities, issue_by_file):
    total = len(nodes)
    vis_nodes = []
    for n in nodes:
        cat = n['category']
        style = CAT_STYLE.get(cat, CAT_STYLE['other'])
        d = deg.get(n['id'], 0)
        rel = n['rel']
        comm = communities.get(n['id'], 0)
        node_issues = issue_by_file.get(rel, [])
        has_issue = bool(node_issues)
        if has_issue:
            sevs = [x['severity'] for x in node_issues]
            top = 'error' if 'error' in sevs else ('warn' if 'warn' in sevs else 'info')
        else:
            top = 'ok'
        border = {'error': '#dc2626', 'warn': '#d97706', 'info': '#3b82f6', 'ok': style['bd']}[top]
        bw = 3 if has_issue else (2 if d >= 10 else 1)
        rank = rank_of.get(n['id'], total)
        is_hub = d >= 20
        label = n['label']
        display = (label[:22] + '..') if len(label) > 24 else label
        tip = (f"<b>{label}</b><br><span style='color:#6b7280;font-size:10px'>{rel}</span><br>"
               f"{style['name']} | {d} edges | community {comm}<br>rank #{rank} of {total}")
        if is_hub:
            tip += "<br><b style='color:#7c3aed'>&#9733; hub (high blast radius)</b>"
        if has_issue:
            tip += f"<br><b style='color:{border}'>&#9888; {len(node_issues)} issue(s)</b>"
        vis_nodes.append({
            'id': n['id'], 'label': display, 'title': tip,
            'color': {'background': style['bg'], 'border': border,
                      'highlight': {'background': style['bg'], 'border': '#1e293b'},
                      'hover': {'background': style['bg'], 'border': '#1e293b'}},
            'size': _sev_size(d),
            'font': {'size': 11, 'color': '#111827', 'face': 'Inter,sans-serif',
                     'strokeWidth': 3, 'strokeColor': '#ffffff'},
            'borderWidth': bw,
            '_d': d, '_cat': cat, '_sf': rel, '_comm': comm, '_label': label,
            '_sev': top, '_issue': has_issue, '_n': len(node_issues),
            '_base': _sev_size(d), '_bw': bw, '_rank': rank, '_hub': is_hub,
        })
    return vis_nodes


def _write_artifacts(out_dir, project_name, build_ts, nodes, edges, communities,
                     comm_display, issues, hub_list, deg, rank_of,
                     flows=None, sensitive=None, apis=None, docs=None, assets=None,
                     diagnostics=None, parser=None, knowledge=None):
    flows = flows or []
    sensitive = sensitive or []
    apis = apis or []
    docs = docs or {}
    assets = assets or []
    diagnostics = diagnostics or {}
    knowledge = knowledge or {}
    n_err = sum(1 for x in issues if x['severity'] == 'error')
    n_warn = sum(1 for x in issues if x['severity'] == 'warn')
    n_info = sum(1 for x in issues if x['severity'] == 'info')

    # JSON
    context = {
        'project': project_name,
        'generated': build_ts,
        'tool': 'nodo',
        'stats': {
            'nodes': len(nodes), 'edges': len(edges),
            'communities': len(set(communities.values())) if communities else 0,
            'issues': {'total': len(issues), 'error': n_err, 'warn': n_warn, 'info': n_info},
            'parser': parser or 'regex',
        },
        # what the scan dropped or reused — nothing fails silently
        'diagnostics': {
            'parser': parser or 'regex',
            'skipped_large': diagnostics.get('skipped_large', []),
            'read_errors': diagnostics.get('read_errors', []),
            'stat_errors': diagnostics.get('stat_errors', []),
            'cache_hits': diagnostics.get('cache_hits', 0),
            'parsed': diagnostics.get('parsed', 0),
            'changed': diagnostics.get('changed', []),
            'added': diagnostics.get('added', []),
        },
        'hubs': hub_list,
        'communities': comm_display,
        'flows': flows,
        'sensitive': sensitive,
        'api_routes': apis,
        # design docs (paths only; full text stays on disk) + multimodal asset
        # manifest linking images/PDFs/video to the nodes that reference them.
        'docs': sorted(docs.keys()),
        'assets': assets,
        # knowledge graph over docs/PDFs: concepts + topic communities
        'knowledge': {
            'concepts': knowledge.get('concepts', []),
            'topics': knowledge.get('topics', []),
            'god_nodes': knowledge.get('god_nodes', []),
        },
        # compact file + edge tables so `--query` can answer blast-radius
        # questions without re-scanning the project.
        'files': [
            {'id': n['id'], 'rel': n['rel'], 'category': n['category'],
             'loc': n.get('loc', 0), 'tier': n.get('tier', 'app'),
             'kind': n.get('kind', 'code'),
             'community': communities.get(n['id'], 0),
             'hub_rank': rank_of.get(n['id'])}
            for n in nodes
        ],
        'edges': edges,
        'issues': [
            {**{k: v for k, v in iss.items() if k != 'snippet'},
             'snippet': [f'{s["n"]}: {s["text"]}' for s in iss.get('snippet', [])]}
            for iss in issues
        ],
    }
    (out_dir / 'nodo-context.json').write_text(json.dumps(context, indent=2), encoding='utf-8')

    # Markdown
    md = [f'# {project_name} — Architecture Context\n',
          f'> Generated {build_ts} by Nodo · companion to nodo.html\n',
          '\n## Stats\n',
          f'- {len(nodes)} files · {len(edges)} dependencies · '
          f'{len(set(communities.values())) if communities else 0} modules',
          f'- Issues: **{len(issues)}** ({n_err} errors, {n_warn} warnings, {n_info} info)',
          f'- Confidence: {sum(1 for i in issues if i.get("confidence") == "high")} high · '
          f'{sum(1 for i in issues if i.get("confidence") == "medium")} medium · '
          f'{sum(1 for i in issues if i.get("confidence") == "low")} low '
          f'(act on high first; low are hints)\n',
          '\n## Ask nodo instead of reading files\n']
    # Progressive disclosure (the token-saving contract): this summary orients;
    # SPECIFIC questions should go to queries (a few hundred tokens) not file reads.
    # Ground each suggestion in THIS scan so it's copy-pasteable.
    _top_hub = hub_list[0]['file'] if hub_list else '<file>'
    _concept = ''
    _gods = (knowledge or {}).get('god_nodes') or []
    if _gods:
        _concept = _gods[0].get('concept', '')
    md.append('Answers cost a few hundred tokens each — cheaper than opening files:\n')
    md.append(f'- What breaks if I change a file: `nodo . --query {_top_hub}`')
    md.append('- Any question, routed for you: `nodo . --ask "<question>"` '
              '(impact, issues, hubs, concepts, overview)')
    if _concept:
        md.append(f'- Where a concept lives (code+docs): `nodo . --explain "{_concept}"`')
    md.append('- What my recent edits put at risk: ask `what changed` '
              '(or MCP `nodo_changed`)')
    md.extend(['\n## Architectural hubs (highest blast radius)\n',
               '| File | Edges |', '|---|---|'])
    for h in hub_list:
        md.append(f'| `{h["file"]}` | {h["edges"]} |')

    # personalization: what changed since last scan + the files you query most
    changed, added = diagnostics.get('changed', []), diagnostics.get('added', [])
    if changed or added:
        md.append('\n## Since your last scan\n')
        if changed:
            md.append(f'- **Changed ({len(changed)})**: '
                      + ', '.join(f'`{r}`' for r in changed[:12]) + (' …' if len(changed) > 12 else ''))
        if added:
            md.append(f'- **New ({len(added)})**: '
                      + ', '.join(f'`{r}`' for r in added[:12]) + (' …' if len(added) > 12 else ''))
    try:
        from . import querylog
        freq = querylog.frequent_files(out_dir, [n['rel'] for n in nodes if n.get('kind', 'code') == 'code'])
        if freq:
            md.append('\n## Files you work with most (from your queries)\n')
            for rel, cnt in freq:
                md.append(f'- `{rel}` — queried {cnt}×')
    except Exception:
        pass

    if sensitive:
        md.append('\n## Security-sensitive surfaces (review first)\n')
        for layer in sensitive:
            top = ', '.join(f'`{f["rel"]}`' for f in layer['files'][:5])
            md.append(f'- **{layer["label"]}** ({layer["count"]}): {top}')

    if flows:
        md.append('\n## Entry-point flows (what each entry reaches)\n')
        for f in flows[:12]:
            reaches = ', '.join(f['reaches'][:8])
            md.append(f'- `{f["entry"]}` → {f["reach_count"]} files: {reaches}')

    if docs:
        md.append('\n## Design docs (intent — judge code against these)\n')
        md.append('Specs/READMEs in this repo. Use `--explain "<concept>"` to find the '
                  'doc that defines a feature, then compare against the code.\n')
        for rel in sorted(docs)[:30]:
            md.append(f'- `{rel}`')

    if assets:
        md.append('\n## Multimodal assets (images / PDFs / video)\n')
        md.append('Linked to the nodes that reference them. To interpret their *contents*, '
                  'open the file directly (Claude can read images & PDFs).\n')
        for a in assets[:30]:
            ref = ', '.join(f'`{r}`' for r in a.get('referenced_by', [])[:3]) or '—'
            md.append(f'- `{a["rel"]}` ({a["type"]}) — referenced by: {ref}')

    if knowledge.get('topics'):
        md.append('\n## Knowledge graph — topics from docs & PDFs\n')
        md.append('Document/PDF content mined into concepts and clustered into topics '
                  '(communities). Use `--explain "<concept>"` to locate, then read the '
                  'source for the AI answer. Full detail in `nodo-knowledge.md`.\n')
        for t in knowledge['topics'][:12]:
            cs = ', '.join(t['concepts'][:6])
            ds = ', '.join(d.split('/')[-1] for d in t['docs'][:4])
            md.append(f'- **{t["name"]}** — concepts: {cs}' + (f' · docs: {ds}' if ds else ''))

    md.append('\n## Modules\n')
    md.append('| # | Name | Size |')
    md.append('|---|---|---|')
    for c in comm_display:
        md.append(f'| {c["id"]} | {c["name"]} | {c["size"]} |')
    md.append('\n## Issues by category\n')
    by_cat = defaultdict(list)
    for iss in issues:
        by_cat[iss['category']].append(iss)
    # Global row budget: the summary must stay token-cheap even on repos with
    # thousands of findings — full detail lives in nodo-context.json / -issues.txt
    # (progressive disclosure: act on high-confidence here, drill in on demand).
    _budget = 150
    for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
        grp = by_cat[cat]
        md.append(f'\n### {cat} ({len(grp)})\n')
        take = grp[:min(40, max(0, _budget))]
        for iss in take:
            ln = f':L{iss["line"]}' if iss.get('line') else ''
            md.append(f'- **[{iss["severity"].upper()}] {iss["type"]}** _(conf: {iss.get("confidence","medium")})_ — `{iss.get("file","")}{ln}` — {iss["detail"][:140]}')
        _budget -= len(take)
        if len(grp) > len(take):
            md.append(f'- _…and {len(grp) - len(take)} more (see nodo-context.json)_')
    (out_dir / 'nodo-context.md').write_text('\n'.join(md) + '\n', encoding='utf-8')

    # TXT
    txt = [f'{project_name} — CODE ISSUES (via Nodo)', f'Generated: {build_ts}',
           f'Total: {len(issues)}  Errors: {n_err}  Warnings: {n_warn}  Info: {n_info}',
           '=' * 78]
    last = None
    for iss in issues:
        if iss['category'] != last:
            txt.append(f'\n\n── {iss["category"].upper()} ──\n')
            last = iss['category']
        ln = f':L{iss["line"]}' if iss.get('line') else ''
        txt.append(f'[{iss["severity"].upper()}] {iss["type"]}  (confidence: {iss.get("confidence", "medium")})')
        txt.append(f'  File  : {iss.get("file","")}{ln}')
        txt.append(f'  Detail: {iss["detail"]}\n')
    (out_dir / 'nodo-issues.txt').write_text('\n'.join(txt) + '\n', encoding='utf-8')

    # ── Prose architecture report (readable narrative + issue posture) ──
    _write_report(out_dir, project_name, build_ts, nodes, edges, communities,
                  comm_display, issues, hub_list, flows, sensitive,
                  n_err, n_warn, n_info)

    # ── Knowledge graph artifact (topics/concepts from docs + PDFs) ──
    if knowledge.get('topics') or knowledge.get('concepts'):
        _write_knowledge(out_dir, project_name, build_ts, knowledge)


def _write_report(out_dir, project_name, build_ts, nodes, edges, communities,
                  comm_display, issues, hub_list, flows, sensitive,
                  n_err, n_warn, n_info):
    from collections import Counter
    cats = Counter(n['category'] for n in nodes)
    n_mods = len(set(communities.values())) if communities else 0
    r = []
    r.append(f'# {project_name} — Architecture Report\n')
    r.append(f'> Generated {build_ts} by Nodo. A readable narrative companion to '
             f'`nodo.html`. Regenerate after code changes.\n')

    r.append('\n## Corpus\n')
    r.append('| Metric | Value |')
    r.append('|---|---|')
    r.append(f'| Source files | {len(nodes)} |')
    r.append(f'| Dependencies (edges) | {len(edges)} |')
    r.append(f'| Modules (clusters) | {n_mods} |')
    for label, key in (('API / routes', 'api'), ('Components', 'component'),
                       ('Libraries', 'lib'), ('Pages', 'page'), ('Models', 'model')):
        if cats.get(key):
            r.append(f'| {label} | {cats[key]} |')

    r.append('\n## Load-bearing files (highest blast radius)\n')
    r.append('These are the most-depended-on files. A change here ripples widest — '
             'review and test them with extra care.\n')
    r.append('| File | Edges |')
    r.append('|---|---|')
    for h in hub_list[:10]:
        r.append(f'| `{h["file"]}` | {h["edges"]} |')

    if sensitive:
        r.append('\n## Security posture\n')
        r.append('Files auto-classified by the sensitive operations they perform. '
                 'In an audit, start here.\n')
        for layer in sensitive:
            top = ', '.join(f'`{f["rel"]}`' for f in layer['files'][:3])
            r.append(f'- **{layer["label"]}** — {layer["count"]} file(s). e.g. {top}')

    if flows:
        r.append('\n## Primary flows\n')
        r.append('The entry points that reach the most of the codebase — the '
                 'critical paths through the system.\n')
        for f in flows[:8]:
            r.append(f'- `{f["entry"]}` → touches {f["reach_count"]} files')

    r.append('\n## Code health\n')
    total = len(issues)
    r.append(f'Nodo flagged **{total}** issues: {n_err} errors, {n_warn} warnings, '
             f'{n_info} info. Breakdown by category:\n')
    by_cat = Counter(i['category'] for i in issues)
    r.append('| Category | Count |')
    r.append('|---|---|')
    for cat, cnt in by_cat.most_common():
        r.append(f'| {cat} | {cnt} |')
    if n_err:
        r.append('\n**Errors need attention first.** See the Issues tab in '
                 '`nodo.html` or `nodo-issues.txt` for exact lines and snippets.')

    r.append('\n## Modules\n')
    r.append('| # | Cluster | Size |')
    r.append('|---|---|---|')
    for c in comm_display:
        r.append(f'| {c["id"]} | {c["name"]} | {c["size"]} |')

    r.append('\n---\n')
    r.append('*For machine-readable detail (every edge, issue, line number, and '
             'snippet) see `nodo-context.json`. For blast-radius and path queries '
             'run `nodo.py . --query <file>` or `--path <a> <b>`.*\n')
    (out_dir / 'nodo-report.md').write_text('\n'.join(r) + '\n', encoding='utf-8')


def _write_knowledge(out_dir, project_name, build_ts, knowledge):
    """Write nodo-knowledge.md — the multimodal knowledge graph for AI agents:
    topics (communities), their concepts, and the docs/PDFs in each."""
    topics = knowledge.get('topics', [])
    concept_docs = knowledge.get('concept_docs', {})
    k = [f'# {project_name} — Knowledge Graph\n',
         f'> Generated {build_ts} by Nodo. Concepts + topics mined from docs & PDFs '
         f'(deterministic). Ask the Claude skill to answer questions *semantically* '
         f'over this graph (and to read images/PDFs with vision).\n',
         f'\n{len(topics)} topic(s), {len(knowledge.get("concepts", []))} concept(s).\n']
    god = knowledge.get('god_nodes', [])
    if god:
        k.append('\n## God-nodes (most-connected concepts — everything flows through these)\n')
        for g in god:
            k.append(f'- **{g["concept"]}** — referenced across {g["docs"]} doc(s)')
    for t in topics:
        k.append(f'\n## Topic: {t["name"]}  ({t["size"]} nodes)\n')
        if t.get('concepts'):
            k.append('- **Concepts**: ' + ', '.join(t['concepts']))
        if t.get('docs'):
            k.append('- **Sources**: ' + ', '.join(f'`{d}`' for d in t['docs']))
    if concept_docs:
        k.append('\n## Concept → sources\n')
        for c in sorted(concept_docs)[:60]:
            k.append(f'- **{c}**: ' + ', '.join(f'`{d}`' for d in concept_docs[c][:6]))
    (out_dir / 'nodo-knowledge.md').write_text('\n'.join(k) + '\n', encoding='utf-8')


# The big HTML template lives in template.py to keep this file readable.
from .template import build_html as _build_html  # noqa: E402
