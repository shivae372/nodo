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
    'other':     {'bg': '#94a3b8', 'bd': '#64748b', 'name': 'Other'},
}


def _sev_size(d):
    if d >= 40: return 30
    if d >= 20: return 24
    if d >= 10: return 18
    if d >= 5:  return 13
    return 9


def render(out_dir, project_name, abs_root, nodes, edges, communities,
           comm_summaries, issues, community_names=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    community_names = community_names or {}

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

    # hubs (top authored nodes)
    hub_list = []
    for nid, d in ranked:
        if d == 0:
            break
        n = id_to_node[nid]
        hub_list.append({'label': n['label'], 'file': n['rel'], 'edges': d})
        if len(hub_list) >= 15:
            break

    comm_display = []
    for c in comm_summaries:
        name = community_names.get(str(c['id']), c['name'])
        comm_display.append({**c, 'name': name})

    # ── artifacts: JSON + MD + TXT ──
    _write_artifacts(out_dir, project_name, build_ts, nodes, edges, communities,
                     comm_display, issues, hub_list)

    # ── vis nodes/edges ──
    vis_nodes = _build_vis_nodes(nodes, deg, rank_of, communities, issue_by_file)
    vis_edges = [{'from': e['source'], 'to': e['target'],
                  'color': {'color': '#cbd5e1', 'opacity': 0.35}, 'width': 1}
                 for e in edges]

    stats = {
        'nodes': len(nodes), 'edges': len(edges),
        'communities': len(set(communities.values())) if communities else 0,
        'issues': {'total': len(issues), 'error': n_err, 'warn': n_warn, 'info': n_info},
    }

    html = _build_html(project_name, abs_root, build_date, build_ts,
                       vis_nodes, vis_edges, issues, stats, hub_list,
                       comm_display, nodes, deg)
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
                     comm_display, issues, hub_list):
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
        },
        'hubs': hub_list,
        'communities': comm_display,
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
          f'- Issues: **{len(issues)}** ({n_err} errors, {n_warn} warnings, {n_info} info)\n',
          '\n## Architectural hubs (highest blast radius)\n',
          '| File | Edges |', '|---|---|']
    for h in hub_list:
        md.append(f'| `{h["file"]}` | {h["edges"]} |')
    md.append('\n## Modules\n')
    md.append('| # | Name | Size |')
    md.append('|---|---|---|')
    for c in comm_display:
        md.append(f'| {c["id"]} | {c["name"]} | {c["size"]} |')
    md.append('\n## Issues by category\n')
    by_cat = defaultdict(list)
    for iss in issues:
        by_cat[iss['category']].append(iss)
    for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
        grp = by_cat[cat]
        md.append(f'\n### {cat} ({len(grp)})\n')
        for iss in grp[:40]:
            ln = f':L{iss["line"]}' if iss.get('line') else ''
            md.append(f'- **[{iss["severity"].upper()}] {iss["type"]}** — `{iss.get("file","")}{ln}` — {iss["detail"][:140]}')
        if len(grp) > 40:
            md.append(f'- _…and {len(grp) - 40} more (see nodo-context.json)_')
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
        txt.append(f'[{iss["severity"].upper()}] {iss["type"]}')
        txt.append(f'  File  : {iss.get("file","")}{ln}')
        txt.append(f'  Detail: {iss["detail"]}\n')
    (out_dir / 'nodo-issues.txt').write_text('\n'.join(txt) + '\n', encoding='utf-8')


# The big HTML template lives in template.py to keep this file readable.
from .template import build_html as _build_html  # noqa: E402
