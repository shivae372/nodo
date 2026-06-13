"""
HTML template builder for Nodo.

Produces a single self-contained interactive page. vis-network is inlined when a
vendored copy exists (offline-capable); otherwise it falls back to the CDN using
the CORRECT unpkg path (`standalone/umd`, never the doubled `dist/dist` which
404s and silently blanks the canvas).

The page uses a flex-column body so vis-network's container gets a real pixel
height at construction time — the standard fix for "blank canvas in a flex
layout". Tabs toggle the graph pane with display:flex (not block).
"""
import json
from pathlib import Path
from collections import defaultdict

VENDOR_VIS = Path(__file__).parent / 'vendor' / 'vis-network.min.js'

# Category legend colours (kept in sync with render.CAT_STYLE)
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

SEV_COL = {'error': '#dc2626', 'warn': '#d97706', 'info': '#3b82f6'}
SEV_BG = {'error': '#fef2f2', 'warn': '#fffbeb', 'info': '#eff6ff'}
SEV_ICO = {'error': 'X', 'warn': '!', 'info': 'i'}


def _esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def build_html(project_name, abs_root, build_date, build_ts,
               vis_nodes, vis_edges, issues, stats, hub_list,
               comm_display, nodes, deg, flows=None, sensitive=None, apis=None):
    flows = flows or []
    sensitive = sensitive or []
    apis = apis or []
    nodes_json = json.dumps(vis_nodes, separators=(',', ':'))
    edges_json = json.dumps(vis_edges, separators=(',', ':'))
    issues_json = json.dumps(issues, separators=(',', ':'))

    js_meta = {
        'project': project_name, 'buildDate': build_date, 'buildTs': build_ts,
        'absRoot': abs_root, 'hubs': hub_list, 'stats': stats,
        'communities': comm_display,
    }
    meta_json = json.dumps(js_meta, separators=(',', ':'))

    # ── legend ──
    cat_counts = defaultdict(int)
    for n in nodes:
        cat_counts[n['category']] += 1
    legend = ''
    for c, info in CAT_STYLE.items():
        cnt = cat_counts.get(c, 0)
        if not cnt:
            continue
        legend += (f'<div class="leg-row" onclick="filterCat(\'{c}\')">'
                   f'<span class="leg-dot" style="background:{info["bg"]};border-color:{info["bd"]}"></span>'
                   f'<span class="leg-name">{info["name"]}</span>'
                   f'<span class="leg-cnt">{cnt}</span></div>')

    # ── community rows ──
    comm_rows = ''
    for c in comm_display:
        comm_rows += (f'<tr onclick="filterCommunity({c["id"]})">'
                      f'<td class="c-num">{c["id"]}</td>'
                      f'<td>{_esc(c["name"])}</td>'
                      f'<td class="c-sz">{c["size"]}</td></tr>')

    # ── issue cards ──
    issue_html = _build_issue_html(issues, abs_root)

    # ── flows + sensitive + api reference (auto-derived tabs) ──
    flows_html = _build_flows_html(flows, abs_root)
    sensitive_html = _build_sensitive_html(sensitive, abs_root)
    api_html = _build_api_html(apis, abs_root)
    api_count = sum(len(g['routes']) for g in apis)

    n_err = stats['issues']['error']
    n_warn = stats['issues']['warn']
    n_info = stats['issues']['info']
    total_iss = stats['issues']['total']
    pill_cls = 'pill-err' if n_err else ('pill-warn' if n_warn else 'pill-live')
    pill_txt = (f'{n_err} ERRORS' if n_err else (f'{n_warn} WARNINGS' if n_warn else 'CLEAN'))
    iss_badge = 'tab-err' if n_err else ('tab-warn' if n_warn else 'tab-info')

    # ── vis injection ──
    if VENDOR_VIS.exists():
        vis_js = VENDOR_VIS.read_text(encoding='utf-8')
        vis_css_tag = '<!-- vis-network CSS bundled into the inlined standalone build -->'
        vis_script_tag = '<script>\n' + vis_js + '\n</script>'
    else:
        vis_css_tag = '<link rel="stylesheet" href="https://unpkg.com/vis-network@9.1.9/styles/vis-network.min.css">'
        vis_script_tag = '<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>'

    # Build the JS with data injected via placeholder replacement (NOT .format,
    # because the JS body is full of literal { } braces).
    js = (_JS
          .replace('__NODES__', nodes_json)
          .replace('__EDGES__', edges_json)
          .replace('__ISSUES__', issues_json)
          .replace('__META__', meta_json))

    # Assemble the page via plain token replacement (NOT .format(), because the
    # embedded CSS/JS are full of literal { } braces). Tokens are %%NAME%% — a
    # delimiter that never appears in CSS/JS so there's zero collision risk.
    # CSS and JS are substituted LAST so their contents are never re-scanned.
    page = _PAGE
    text_repl = {
        '%%PROJECT%%': _esc(project_name),
        '%%BUILD_DATE%%': build_date,
        '%%BUILD_TS%%': build_ts,
        '%%TOTAL_NODES%%': str(stats['nodes']),
        '%%TOTAL_EDGES%%': str(stats['edges']),
        '%%TOTAL_COMMS%%': str(stats['communities']),
        '%%TOTAL_ISS%%': str(total_iss),
        '%%N_ERR%%': str(n_err),
        '%%N_WARN%%': str(n_warn),
        '%%N_INFO%%': str(n_info),
        '%%PILL_CLS%%': pill_cls,
        '%%PILL_TXT%%': pill_txt,
        '%%ISS_BADGE%%': iss_badge,
        '%%LEGEND%%': legend,
        '%%COMM_ROWS%%': comm_rows,
        '%%ISSUE_HTML%%': issue_html,
        '%%FLOWS_HTML%%': flows_html,
        '%%SENSITIVE_HTML%%': sensitive_html,
        '%%API_HTML%%': api_html,
        '%%FLOW_COUNT%%': str(len(flows)),
        '%%SENS_COUNT%%': str(sum(s['count'] for s in sensitive)),
        '%%API_COUNT%%': str(api_count),
        '%%VIS_CSS_TAG%%': vis_css_tag,
    }
    for k, v in text_repl.items():
        page = page.replace(k, v)
    # big blobs last
    page = page.replace('%%CSS%%', _CSS)
    page = page.replace('%%VIS_SCRIPT_TAG%%', vis_script_tag)
    page = page.replace('%%JS%%', js)
    return page


def _build_issue_html(issues, abs_root):
    by_cat = defaultdict(list)
    for iss in issues:
        by_cat[iss['category']].append(iss)
    order = sorted(by_cat, key=lambda c: -len(by_cat[c]))
    parts = []
    for cat in order:
        grp = by_cat[cat]
        n_e = sum(1 for x in grp if x['severity'] == 'error')
        n_w = sum(1 for x in grp if x['severity'] == 'warn')
        bc = '#dc2626' if n_e else ('#d97706' if n_w else '#3b82f6')
        bt = f'{n_e}E' if n_e else (f'{n_w}W' if n_w else f'{len(grp)}I')
        parts.append(f'<div class="iss-cat"><span>{_esc(cat)}</span>'
                     f'<span class="iss-badge" style="background:{bc}18;color:{bc};border:1px solid {bc}44">'
                     f'{bt} &middot; {len(grp)}</span></div>')
        for iss in grp:
            col = SEV_COL.get(iss['severity'], '#64748b')
            bg = SEV_BG.get(iss['severity'], '#f8fafc')
            ico = SEV_ICO.get(iss['severity'], '.')
            node_js = json.dumps(iss.get('node', ''))
            ln_str = f':L{iss["line"]}' if iss.get('line') else ''
            if iss.get('file'):
                link = f'vscode://file/{abs_root}/{iss["file"]}' + (f':{iss["line"]}' if iss.get('line') else '')
                file_tag = (f'<a class="iss-file" href="{link}" title="Open in VS Code / Cursor / Windsurf" '
                            f'onclick="event.stopPropagation()">{_esc(iss["file"])}{ln_str}</a>')
            else:
                file_tag = ''
            snip = ''
            if iss.get('snippet'):
                rows = ''
                for s in iss['snippet']:
                    hit = ' snip-hit' if str(s['n']) == str(iss.get('line')) else ''
                    rows += (f'<div class="snip-line{hit}"><span class="snip-n">{s["n"]}</span>'
                             f'<span class="snip-t">{_esc(s["text"])}</span></div>')
                snip = f'<div class="snip">{rows}</div>'
            parts.append(
                f'<div class="iss-card" style="border-left-color:{col};background:{bg}" '
                f'onclick="jumpToNode({node_js})">'
                f'<div class="iss-row1">'
                f'<span class="iss-sev" style="color:{col}">[{ico}] {_esc(iss["type"]).upper()}</span>'
                f'{file_tag}'
                f'<button class="iss-copy" onclick="event.stopPropagation();copyIssueContext({iss["idx"]},this)" '
                f'title="Copy AI debugging prompt">Copy AI Context</button>'
                f'</div>'
                f'<div class="iss-node" style="color:{col}">{_esc(iss.get("node",""))}</div>'
                f'<div class="iss-detail">{_esc(iss["detail"])}</div>'
                f'{snip}</div>')
    return '\n'.join(parts)


def _ide_link(abs_root, rel):
    return f'vscode://file/{abs_root}/{rel}'


# colour per file category (kept consistent with the graph legend)
def _cat_colour(rel):
    r = rel.lower()
    if '/api/' in r or 'route' in r:        return '#3b82f6'
    if '/components/' in r or r.endswith(('.tsx', '.vue', '.svelte')): return '#ec4899'
    if '/lib/' in r or '/utils' in r:       return '#6366f1'
    if '/pages/' in r or '/app/' in r:      return '#64748b'
    return '#94a3b8'


def _build_flows_html(flows, abs_root):
    """Numbered step-by-step call sequences per entry point (read top-to-bottom).

    Each flow renders as: STEP 1 (entry) -> STEP 2 (its direct imports) -> STEP 3
    (their imports), so a reader can follow how a request/page actually moves
    through the code — a Data Flow view auto-built from real imports.
    """
    if not flows:
        return '<p class="sub">No entry points detected (no API routes, pages, or main files found).</p>'
    parts = []
    for f in flows:
        steps = f.get('steps') or [[f['entry']]]
        node_js = json.dumps(f['entry'].split('/')[-1])
        # title row
        col = _cat_colour(f['entry'])
        parts.append(
            f'<div class="flow-title">'
            f'<span class="flow-dot" style="background:{col}"></span>'
            f'<a class="flow-entry" href="{_ide_link(abs_root, f["entry"])}">{_esc(f["entry"])}</a>'
            f'<span class="flow-badge">{f["reach_count"]} files in {len(steps)} step(s)</span>'
            f'</div>')
        # numbered step strip
        cells = []
        for i, layer in enumerate(steps, 1):
            shown = layer[:6]
            files_html = ''.join(
                f'<a class="flow-file" href="{_ide_link(abs_root, r)}" '
                f'title="{_esc(r)}" onclick="event.stopPropagation();jumpToNode('
                f'{json.dumps(r.split("/")[-1])})">{_esc(r.split("/")[-1])}</a>'
                for r in shown)
            extra = f'<span class="flow-more">+{len(layer) - 6}</span>' if len(layer) > 6 else ''
            label = 'entry' if i == 1 else f'depth {i - 1}'
            cells.append(
                f'<div class="flow-step"><div class="flow-n">{i}</div>'
                f'<div class="flow-step-label">{label}</div>'
                f'<div class="flow-step-files">{files_html}{extra}</div></div>')
            if i < len(steps):
                cells.append('<div class="flow-arr">&#8594;</div>')
        parts.append(f'<div class="flow">{"".join(cells)}</div>')
    return '\n'.join(parts)


def _build_api_html(apis, abs_root):
    """Clean grouped HTTP route reference (API tab)."""
    if not apis:
        return '<p class="sub">No API routes detected (no api/ folder or route handlers found).</p>'
    parts = []
    for grp in apis:
        parts.append(f'<div class="api-group">{_esc(grp["group"])}</div>')
        for r in grp['routes']:
            badges = ''.join(
                f'<span class="api-badge" style="background:{c}18;color:{c};'
                f'border:1px solid {c}44">{_esc(m)}</span>'
                for m, c in zip(r['methods'], r['colors']))
            link = _ide_link(abs_root, r['file'])
            node_js = json.dumps(r['file'].split('/')[-1])
            parts.append(
                f'<div class="api-row" onclick="jumpToNode({node_js})">'
                f'<a class="api-ep" href="{link}" onclick="event.stopPropagation()" '
                f'title="{_esc(r["file"])}">{_esc(r["path"])}</a>'
                f'<span class="api-methods">{badges}</span></div>')
    return '\n'.join(parts)


def _build_sensitive_html(sensitive, abs_root):
    """Auto-derived security surfaces: files touching auth/crypto/secrets/etc."""
    if not sensitive:
        return '<p class="sub">No security-sensitive surfaces detected by pattern.</p>'
    parts = []
    for i, layer in enumerate(sensitive, 1):
        rows = ''
        for fobj in layer['files']:
            rel = fobj['rel']
            hits = ', '.join(_esc(h) for h in fobj['hits'][:5])
            node_js = json.dumps(rel.split('/')[-1])
            rows += (
                f'<div class="sec-file-row" onclick="jumpToNode({node_js})">'
                f'<a class="sec-file" href="{_ide_link(abs_root, rel)}" '
                f'onclick="event.stopPropagation()">{_esc(rel)}</a>'
                f'<span class="sec-hits">{hits}</span></div>')
        parts.append(
            f'<div class="sec-layer">'
            f'<div class="sec-n">{i}</div>'
            f'<div class="sec-body">'
            f'<b>{_esc(layer["label"])}</b> '
            f'<span class="sec-count">{layer["count"]} file(s)</span>'
            f'<div class="sec-files">{rows}</div>'
            f'</div></div>')
    return '\n'.join(parts)


# ── CSS ───────────────────────────────────────────────────────────────────────
_CSS = r"""
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#fff;--bg2:#f8f9fa;--bg3:#f0f2f5;--bg4:#e8eaed;
  --border:#d1d5db;--border2:#e5e7eb;
  --text:#111827;--text2:#374151;--text3:#6b7280;--text4:#9ca3af;
  --accent:#2563eb;--accent-bg:#eff6ff;--accent-light:#dbeafe;
  --green:#16a34a;--green-bg:#f0fdf4;--green-light:#dcfce7;
  --amber:#d97706;--amber-bg:#fffbeb;--amber-light:#fef3c7;
  --red:#dc2626;--red-bg:#fef2f2;--red-light:#fee2e2;
  --mono:'JetBrains Mono',ui-monospace,monospace;
  --sans:'Inter','Segoe UI',system-ui,sans-serif;
  --r:6px;--shadow:0 1px 3px rgba(0,0,0,.08);--shadow-md:0 4px 6px -1px rgba(0,0,0,.1);
}
html,body{height:100%;overflow:hidden;font-family:var(--sans);background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased;display:flex;flex-direction:column}
#header{flex-shrink:0;height:48px;padding:0 20px;background:var(--bg);border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;box-shadow:var(--shadow);z-index:10}
#tabs{flex-shrink:0;height:40px;padding:0 12px;gap:2px;background:var(--bg);border-bottom:1px solid var(--border);display:flex;align-items:stretch}
#tab-body{flex:1;display:flex;overflow:hidden;position:relative}
#sidebar{width:292px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;background:var(--bg)}
#graph-pane{flex:1;display:flex;overflow:hidden;position:relative}
#graph{flex:1;width:100%;height:100%;background:var(--bg3)}
.cpane{flex:1;overflow-y:auto;padding:28px 40px;display:none;background:var(--bg);font-size:13px;line-height:1.7;color:var(--text2)}
.brand{font-size:15px;font-weight:700;color:var(--accent);letter-spacing:-.3px}
.hdr-sep{width:1px;height:20px;background:var(--border2);margin:0 10px}
.hdr-title{font-size:13px;font-weight:500;color:var(--text2)}
.hdr-meta{font-size:11px;color:var(--text4);font-family:var(--mono)}
.pill{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600}
.pill-live{background:var(--green-bg);color:var(--green);border:1px solid var(--green-light)}
.pill-warn{background:var(--amber-bg);color:var(--amber);border:1px solid var(--amber-light)}
.pill-err{background:var(--red-bg);color:var(--red);border:1px solid var(--red-light)}
.pill-dot{width:6px;height:6px;border-radius:50%;background:currentColor}
.age-fresh{background:var(--green-bg);color:var(--green);border:1px solid var(--green-light)}
.age-mid{background:var(--amber-bg);color:var(--amber);border:1px solid var(--amber-light)}
.age-old{background:var(--red-bg);color:var(--red);border:1px solid var(--red-light)}
.tab{padding:0 16px;font-size:12px;font-weight:500;cursor:pointer;color:var(--text3);border-bottom:2px solid transparent;display:flex;align-items:center;gap:6px;white-space:nowrap;transition:color .12s}
.tab:hover{color:var(--text2)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.tab-badge{padding:1px 6px;border-radius:10px;font-size:10px;font-weight:700}
.tab-err{background:var(--red-bg);color:var(--red)}
.tab-warn{background:var(--amber-bg);color:var(--amber)}
.tab-info{background:var(--accent-bg);color:var(--accent)}
#sb-hdr{flex-shrink:0;padding:9px 14px;background:var(--accent-bg);font-size:11px;font-weight:600;color:var(--accent);border-bottom:1px solid var(--accent-light);letter-spacing:.2px}
#sb-node{flex-shrink:0;padding:11px 14px;font-size:12px;line-height:1.6;color:var(--text3);font-style:italic;border-bottom:1px solid var(--border2);min-height:80px;max-height:200px;overflow-y:auto}
#sb-scroll{flex:1;overflow-y:auto}
.sb-sec{padding:10px 14px;border-bottom:1px solid var(--border2)}
.sb-lbl{font-size:10px;color:var(--text4);text-transform:uppercase;letter-spacing:.6px;font-weight:600;margin-bottom:7px}
.leg-row{display:flex;align-items:center;padding:3px 6px;border-radius:4px;cursor:pointer}
.leg-row:hover{background:var(--accent-bg)}
.leg-dot{width:11px;height:11px;border-radius:3px;margin-right:8px;flex-shrink:0;border:1px solid rgba(0,0,0,.1)}
.leg-name{flex:1;font-size:11px;color:var(--text2)}
.leg-cnt{font-size:10px;color:var(--text4);font-family:var(--mono)}
#fbox{width:100%;padding:5px 9px;background:var(--bg);border:1px solid var(--border);border-radius:var(--r);color:var(--text);font-size:11px;font-family:var(--sans);margin-bottom:7px}
#fbox:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-light)}
.btn-row{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:4px}
.btn{padding:3px 9px;font-size:11px;background:var(--bg2);color:var(--text2);border:1px solid var(--border);border-radius:var(--r);cursor:pointer;font-family:var(--sans);font-weight:500;transition:all .12s}
.btn:hover{background:var(--accent-bg);color:var(--accent);border-color:var(--accent-light)}
.btn.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn.done,.btn-primary.done,.iss-copy.done{background:var(--green);color:#fff;border-color:var(--green)}
.sld-row{margin-bottom:8px}
.sld-lbl{display:flex;justify-content:space-between;font-size:10px;color:var(--text3);margin-bottom:3px}
.sld-lbl span{font-family:var(--mono);color:var(--accent);font-weight:600}
.sld-row input[type=range]{width:100%;height:4px;-webkit-appearance:none;appearance:none;background:var(--bg4);border-radius:3px;outline:none;cursor:pointer}
.sld-row input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:13px;height:13px;border-radius:50%;background:var(--accent);cursor:pointer;border:2px solid #fff;box-shadow:0 1px 2px rgba(0,0,0,.2)}
.sld-row input[type=range]::-moz-range-thumb{width:13px;height:13px;border-radius:50%;background:var(--accent);cursor:pointer;border:2px solid #fff}
.ctbl{width:100%;border-collapse:collapse;font-size:11px}
.ctbl th{background:var(--bg3);color:var(--text3);padding:4px 8px;text-align:left;font-weight:600;font-size:10px;text-transform:uppercase;border-bottom:1px solid var(--border)}
.c-num{color:var(--text4);font-family:var(--mono);width:24px}
.c-sz{text-align:right;font-family:var(--mono);font-weight:600;color:var(--text3)}
.ctbl tr{cursor:pointer}
.ctbl tr:hover td{background:var(--accent-bg);color:var(--accent)}
.ctbl td{padding:4px 8px;border-bottom:1px solid var(--border2);color:var(--text2)}
#sb-foot{flex-shrink:0;padding:7px 14px;font-size:10px;color:var(--text4);font-family:var(--mono);border-top:1px solid var(--border2)}
.cpane h1{font-size:20px;font-weight:700;color:var(--text);margin:0 0 4px;padding-bottom:10px;border-bottom:2px solid var(--border)}
.cpane .sub{color:var(--text3);font-size:12px;margin-bottom:18px}
.cpane h2{font-size:14px;font-weight:600;color:var(--text);margin:24px 0 8px;padding-left:8px;border-left:3px solid var(--accent)}
.cpane code{background:var(--bg3);color:var(--accent);padding:1px 5px;border-radius:3px;font-size:11px;font-family:var(--mono);border:1px solid var(--border2)}
.iss-summary{display:flex;gap:10px;padding:12px;background:var(--bg2);border:1px solid var(--border2);border-radius:8px;margin-bottom:16px}
.iss-stat{text-align:center;flex:1}
.iss-n{font-size:22px;font-weight:700}
.iss-l{font-size:10px;color:var(--text4);text-transform:uppercase;letter-spacing:.4px}
.iss-cat{display:flex;align-items:center;justify-content:space-between;padding:10px 0 5px;margin-top:8px;border-bottom:2px solid var(--border2);font-size:12px;font-weight:700;color:var(--text)}
.iss-badge{padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}
.iss-card{border-left:3px solid;border-radius:0 var(--r) var(--r) 0;padding:9px 13px;margin:5px 0;cursor:pointer;transition:box-shadow .12s}
.iss-card:hover{box-shadow:var(--shadow-md)}
.iss-row1{display:flex;align-items:center;gap:8px;margin-bottom:3px;flex-wrap:wrap}
.iss-sev{font-size:10px;font-weight:700;letter-spacing:.4px;white-space:nowrap}
a.iss-file{font-family:var(--mono);font-size:10px;color:var(--text4);background:var(--bg3);padding:1px 5px;border-radius:3px;border:1px solid var(--border2);text-decoration:none;cursor:pointer}
a.iss-file:hover{background:var(--accent-bg);color:var(--accent);border-color:var(--accent-light)}
.iss-copy{margin-left:auto;padding:2px 8px;font-size:10px;font-weight:600;background:var(--accent-bg);color:var(--accent);border:1px solid var(--accent-light);border-radius:var(--r);cursor:pointer;font-family:var(--sans);white-space:nowrap;transition:all .12s}
.iss-copy:hover{background:var(--accent);color:#fff}
.iss-node{font-size:12px;font-weight:600;margin-bottom:2px}
.iss-detail{font-size:11px;color:var(--text3);line-height:1.5}
.snip{margin-top:7px;background:#0f172a;border-radius:var(--r);padding:6px 0;overflow-x:auto;font-family:var(--mono);font-size:10px;line-height:1.55}
.snip-line{display:flex;padding:0 10px;white-space:pre}
.snip-n{color:#475569;width:34px;flex-shrink:0;text-align:right;padding-right:10px;user-select:none}
.snip-t{color:#cbd5e1;white-space:pre}
.snip-hit{background:rgba(251,191,36,.12)}
.snip-hit .snip-t{color:#fde68a}
.snip-hit .snip-n{color:#f59e0b;font-weight:700}
.ai-card{background:var(--bg2);border:1px solid var(--border2);border-radius:8px;padding:14px 16px;margin-bottom:14px}
.ai-card-h{font-size:13px;font-weight:700;color:var(--text);margin-bottom:6px}
.ai-file{font-size:11px;color:var(--text3);margin:4px 0;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.btn-primary{padding:6px 14px;font-size:12px;font-weight:600;background:var(--accent);color:#fff;border:1px solid var(--accent);border-radius:var(--r);cursor:pointer;font-family:var(--sans);margin-right:6px;transition:all .12s}
.btn-primary:hover{background:#1d4ed8}
.kbd-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;font-size:11px;color:var(--text2)}
kbd{display:inline-block;padding:1px 6px;font-family:var(--mono);font-size:10px;background:var(--bg);border:1px solid var(--border);border-bottom-width:2px;border-radius:4px;color:var(--text2)}
#ctx{position:fixed;display:none;background:var(--bg);border:1px solid var(--border);border-radius:8px;z-index:9999;min-width:170px;padding:4px 0;box-shadow:var(--shadow-md)}
#ctx div{padding:7px 14px;font-size:12px;cursor:pointer;color:var(--text2)}
#ctx div:hover{background:var(--accent-bg);color:var(--accent)}
/* Flows tab — numbered step-by-step call sequences */
.flow-title{display:flex;align-items:center;gap:8px;margin:22px 0 8px}
.flow-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.flow-entry{font-family:var(--mono);font-size:13px;font-weight:700;color:var(--text);text-decoration:none}
.flow-entry:hover{color:var(--accent)}
.flow-badge{margin-left:auto;font-size:10px;font-weight:600;color:var(--text3);background:var(--bg3);border:1px solid var(--border2);border-radius:10px;padding:1px 8px;white-space:nowrap}
.flow{display:flex;flex-wrap:wrap;align-items:stretch;gap:0;margin-bottom:14px;padding:12px;background:var(--bg2);border:1px solid var(--border2);border-radius:8px}
.flow-step{background:var(--bg);border:1px solid var(--border);border-radius:var(--r);padding:8px 11px;min-width:130px;max-width:200px}
.flow-n{width:18px;height:18px;background:var(--accent);color:#fff;border-radius:50%;font-size:9px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;margin-bottom:5px}
.flow-step-label{font-size:9px;text-transform:uppercase;letter-spacing:.4px;color:var(--text4);margin-bottom:4px}
.flow-step-files{display:flex;flex-direction:column;gap:2px}
.flow-file{font-family:var(--mono);font-size:10px;color:var(--accent);text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.flow-file:hover{text-decoration:underline}
.flow-more{font-size:10px;color:var(--text4)}
.flow-arr{font-size:18px;color:var(--text4);padding:0 7px;align-self:center;flex-shrink:0}
/* Security tab */
.sec-layer{display:flex;gap:13px;padding:13px 0;border-bottom:1px solid var(--border2)}
.sec-n{width:26px;height:26px;background:var(--accent);color:#fff;border-radius:50%;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px}
.sec-body{flex:1;min-width:0}
.sec-body>b{font-size:13px;color:var(--text)}
.sec-count{font-size:11px;color:var(--text3);margin-left:6px}
.sec-files{margin-top:7px;display:flex;flex-direction:column;gap:3px}
.sec-file-row{display:flex;align-items:baseline;gap:10px;padding:3px 7px;border-radius:5px;cursor:pointer;transition:background .1s}
.sec-file-row:hover{background:var(--accent-bg)}
.sec-file{font-family:var(--mono);font-size:11px;color:var(--accent);text-decoration:none;flex-shrink:0}
.sec-hits{font-size:10px;color:var(--text4);font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* API Reference tab */
.api-group{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--text3);margin:18px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--border2)}
.api-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:5px 8px;border-radius:5px;cursor:pointer;transition:background .1s}
.api-row:hover{background:var(--accent-bg)}
.api-ep{font-family:var(--mono);font-size:12px;color:var(--text);text-decoration:none;flex-shrink:1;word-break:break-all}
.api-ep:hover{color:var(--accent)}
.api-methods{display:flex;gap:4px;flex-shrink:0}
.api-badge{font-size:9px;font-weight:700;font-family:var(--mono);padding:1px 6px;border-radius:4px;letter-spacing:.3px}
"""

# ── JS (uses placeholders __NODES__, __EDGES__, __ISSUES__, __META__) ─────────
_JS = r"""
const nodesData = __NODES__;
const edgesData = __EDGES__;
const ISSUES    = __ISSUES__;
const META      = __META__;
const TAB_ORDER = ['graph','issues','flows','security','api','hubs','aicontext'];

function switchTab(name, el){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  if(el) el.classList.add('active');
  else { const t=document.querySelector('.tab[data-tab="'+name+'"]'); if(t)t.classList.add('active'); }
  const sb=document.getElementById('sidebar'), gp=document.getElementById('graph-pane');
  sb.style.display = name==='graph'?'flex':'none';
  gp.style.display = name==='graph'?'flex':'none';
  ['issues','flows','security','api','hubs','aicontext'].forEach(id=>{const p=document.getElementById(id+'-pane'); if(p)p.style.display=(id===name)?'block':'none';});
  if(name==='graph' && typeof network!=='undefined') setTimeout(()=>network.redraw(),50);
}

function clipboard(text,btn){
  const done=()=>{if(!btn)return;const o=btn.textContent;btn.textContent='Copied!';btn.classList.add('done');setTimeout(()=>{btn.textContent=o;btn.classList.remove('done');},1400);};
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(done,()=>fb(text,done));}else fb(text,done);
  function fb(t,d){const ta=document.createElement('textarea');ta.value=t;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}document.body.removeChild(ta);if(d)d();}
}
function copyText(t,btn){clipboard(t,btn);}

function issuePrompt(iss){
  const lineAttr=iss.line?' line="'+iss.line+'"':'';
  let snip='';
  if(iss.snippet&&iss.snippet.length) snip='\n  <code>\n'+iss.snippet.map(s=>'    '+s).join('\n')+'\n  </code>';
  const node=nodesData.find(n=>n._sf===iss.file)||nodesData.find(n=>n._label===iss.node);
  let deps='';
  if(node){const nbr=network.getConnectedNodes(node.id).map(id=>nodesData.find(x=>x.id===id)).filter(Boolean).sort((a,b)=>b._d-a._d).slice(0,6).map(x=>x._label);if(nbr.length)deps='\n  <dependencies>'+nbr.join(', ')+'</dependencies>';}
  return '<context project="'+META.project+'">\n  <file path="'+(iss.file||'')+'"'+lineAttr+' />\n  <issue severity="'+iss.severity+'" type="'+iss.type+'" category="'+iss.category+'">'+iss.detail+'</issue>'+deps+snip+'\n</context>\n<task>Fix this issue. Match the existing patterns in the file and do not break unrelated code. Verify the build/tests pass before finishing.</task>';
}
function copyIssueContext(idx,btn){const iss=ISSUES.find(i=>i.idx===idx);if(iss)clipboard(issuePrompt(iss),btn);}

function copyProjectContext(btn){
  const s=META.stats;
  let t='# '+META.project+' — Architecture Briefing ('+META.buildTs+')\n\n';
  t+='- '+s.nodes+' files, '+s.edges+' dependencies, '+s.communities+' modules\n';
  t+='- Issues: '+s.issues.total+' ('+s.issues.error+' errors, '+s.issues.warn+' warnings, '+s.issues.info+' info)\n\n';
  t+='## Architectural hubs (high blast radius)\n';
  META.hubs.forEach(h=>t+='- `'+h.file+'` — '+h.edges+' edges\n');
  t+='\n## Modules\n';
  META.communities.forEach(c=>t+='- ['+c.id+'] '+c.name+' ('+c.size+' files)\n');
  t+='\nFull detail incl. every issue with line numbers: read nodo-context.json';
  clipboard(t,btn);
}
function issuesToMd(list){
  let t='# '+META.project+' issues backlog ('+list.length+')\n\n';
  const byCat={};list.forEach(i=>{(byCat[i.category]=byCat[i.category]||[]).push(i);});
  Object.keys(byCat).sort((a,b)=>byCat[b].length-byCat[a].length).forEach(cat=>{
    t+='## '+cat+' ('+byCat[cat].length+')\n';
    byCat[cat].forEach(i=>{const ln=i.line?':L'+i.line:'';t+='- [ ] **['+i.severity.toUpperCase()+'] '+i.type+'** `'+(i.file||'')+ln+'` — '+i.detail.slice(0,160)+'\n';});
    t+='\n';
  });
  return t;
}
function copyAllIssues(btn){clipboard(issuesToMd(ISSUES),btn);}
function copyIssuesBySeverity(sev,btn){clipboard(issuesToMd(ISSUES.filter(i=>i.severity===sev)),btn);}
function openInIde(file,line){if(!file)return;window.location.href='vscode://file/'+META.absRoot+'/'+file+(line?':'+line:'');}

function paintAgePill(){
  const pill=document.getElementById('age-pill');if(!pill)return;
  const built=new Date(META.buildDate);const days=Math.floor((Date.now()-built.getTime())/86400000);
  let cls='age-fresh',label='built today';
  if(days>=30){cls='age-old';label='stale · '+days+'d old';}
  else if(days>=7){cls='age-mid';label=days+'d old';}
  else if(days>=1){label=days+'d old';}
  pill.className='pill '+cls;pill.textContent=label;
}
paintAgePill();

const container=document.getElementById('graph');
const dataset_n=new vis.DataSet(nodesData);
const dataset_e=new vis.DataSet(edgesData);
const network=new vis.Network(container,{nodes:dataset_n,edges:dataset_e},{
  nodes:{shape:'dot',scaling:{min:8,max:30},font:{face:'Inter,sans-serif',color:'#111827',size:11,strokeWidth:3,strokeColor:'#ffffff',vadjust:-2}},
  edges:{width:1,color:{color:'#cbd5e1',opacity:0.4},smooth:{type:'continuous',roundness:0.2},arrows:{to:{enabled:false}}},
  physics:{enabled:true,forceAtlas2Based:{gravitationalConstant:-55,centralGravity:0.005,springLength:90,springConstant:0.08,damping:0.4},solver:'forceAtlas2Based',stabilization:{iterations:150,updateInterval:25}},
  interaction:{hover:true,tooltipDelay:100,hideEdgesOnDrag:true,keyboard:{enabled:true}}
});

network.on('click',params=>{
  if(!params.nodes.length){dataset_n.update(nodesData.map(n=>({id:n.id,opacity:1})));const el=document.getElementById('sb-node');el.style.fontStyle='italic';el.innerHTML='Click a node to inspect it';return;}
  const nid=params.nodes[0];const n=nodesData.find(x=>x.id===nid);if(!n)return;
  const nbrIds=network.getConnectedNodes(nid);const conn=new Set([nid,...nbrIds]);
  dataset_n.update(nodesData.map(x=>({id:x.id,opacity:conn.has(x.id)?1:0.08})));
  const nodeIssues=ISSUES.filter(i=>i.node===n._label||i.file===n._sf);
  const sc={error:'#dc2626',warn:'#d97706',info:'#3b82f6'};
  const issHtml=nodeIssues.length?'<div style="margin-top:8px">'+nodeIssues.map(i=>'<div style="margin:3px 0;padding:5px 8px;background:'+({error:'#fef2f2',warn:'#fffbeb',info:'#eff6ff'}[i.severity]||'#f8fafc')+';border-left:3px solid '+(sc[i.severity]||'#64748b')+';border-radius:0 4px 4px 0;font-size:10px"><b style="color:'+(sc[i.severity]||'#64748b')+'">'+i.type+'</b> — '+i.detail.slice(0,90)+'…</div>').join('')+'</div>':'<div style="margin-top:6px;font-size:10px;color:var(--green)">✓ No issues flagged</div>';
  const nbrLabels=nbrIds.map(id=>nodesData.find(x=>x.id===id)).filter(Boolean).sort((a,b)=>b._d-a._d).slice(0,8).map(x=>'<span style="display:inline-block;background:var(--bg3);border:1px solid var(--border2);border-radius:3px;padding:1px 5px;margin:2px 2px 0 0;font-size:9px;font-family:var(--mono);cursor:pointer" onclick="jumpToNode('+JSON.stringify(x._label)+')">'+x._label+'</span>').join('');
  const nbrHtml=nbrIds.length?'<div style="margin-top:8px"><div style="font-size:9px;text-transform:uppercase;letter-spacing:.5px;color:var(--text4);margin-bottom:3px">Connects to ('+nbrIds.length+', top '+Math.min(8,nbrIds.length)+')</div>'+nbrLabels+'</div>':'';
  const hub=n._hub?'<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;background:#f5f3ff;color:#7c3aed;border:1px solid #ddd6fe;margin-left:4px">★ HUB</span>':'';
  const el=document.getElementById('sb-node');el.style.fontStyle='normal';
  el.innerHTML='<b style="font-size:13px;color:var(--text)">'+n._label+'</b><div style="margin:5px 0"><span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;background:'+n.color.background+';color:#fff">'+n._cat+'</span>'+hub+'</div>'+(n._sf?'<div style="font-family:var(--mono);font-size:10px;color:var(--text4);word-break:break-all;margin-bottom:4px">'+n._sf+'</div>':'')+'<div style="font-size:11px;color:var(--text3)">Edges: <b style="color:var(--text)">'+n._d+'</b> · Rank: <b style="color:var(--text)">#'+n._rank+'</b> · Module: <b style="color:var(--text)">'+n._comm+'</b></div>'+issHtml+nbrHtml;
});

let physicsOn=true,issOnly=false,hubsOn=false,ctxNid=null,labelsOn=true,labelSize=11;
function filterNodes(q){if(!q.trim()){resetAll();return;}const ql=q.toLowerCase();dataset_n.update(nodesData.map(n=>({id:n.id,hidden:!(n._label.toLowerCase().includes(ql)||n._sf.toLowerCase().includes(ql))})));}
function filterCat(c){dataset_n.update(nodesData.map(n=>({id:n.id,hidden:n._cat!==c})));}
function filterCommunity(c){dataset_n.update(nodesData.map(n=>({id:n.id,hidden:n._comm!==c})));setTimeout(()=>network.fit(),350);}
function resetAll(){const b=document.getElementById('fbox');if(b)b.value='';dataset_n.update(nodesData.map(n=>({id:n.id,hidden:false,opacity:1})));issOnly=false;hubsOn=false;document.getElementById('btn-iss').classList.remove('on');const bh=document.getElementById('btn-hub');if(bh)bh.classList.remove('on');}
function togglePhysics(){physicsOn=!physicsOn;network.setOptions({physics:{enabled:physicsOn}});const b=document.getElementById('btn-phy');b.textContent='Physics: '+(physicsOn?'On':'Off');b.classList.toggle('on',!physicsOn);}
function toggleIssuesOnly(){issOnly=!issOnly;document.getElementById('btn-iss').classList.toggle('on',issOnly);if(issOnly){dataset_n.update(nodesData.map(n=>({id:n.id,hidden:!n._issue})));setTimeout(()=>network.fit(),350);}else resetAll();}
function toggleHubs(){hubsOn=!hubsOn;const bh=document.getElementById('btn-hub');if(bh)bh.classList.toggle('on',hubsOn);if(hubsOn){const hubFiles=new Set(META.hubs.map(h=>h.file));const ids=new Set();nodesData.forEach(n=>{if(hubFiles.has(n._sf))ids.add(n.id);});const show=new Set(ids);ids.forEach(id=>network.getConnectedNodes(id).forEach(x=>show.add(x)));dataset_n.update(nodesData.map(n=>({id:n.id,opacity:show.has(n.id)?1:0.06})));}else dataset_n.update(nodesData.map(n=>({id:n.id,opacity:1})));}
function setNodeScale(v){const f=parseFloat(v);document.getElementById('sz-val').innerHTML=f.toFixed(1)+'×';dataset_n.update(nodesData.map(n=>({id:n.id,size:Math.max(3,n._base*f),borderWidth:Math.max(1,Math.round(n._bw*Math.sqrt(f)))})));}
function setEdgeScale(v){const f=parseFloat(v);document.getElementById('ew-val').innerHTML=f.toFixed(1)+'×';network.setOptions({edges:{width:f}});}
function setLabelSize(v){labelSize=parseInt(v);document.getElementById('lb-val').textContent=labelSize+'px';applyLabels();}
function applyLabels(){dataset_n.update(nodesData.map(n=>({id:n.id,label:labelsOn?(n.label):undefined,font:{size:labelSize,color:'#111827',face:'Inter,sans-serif',strokeWidth:3,strokeColor:'#ffffff'}})));}
function toggleLabels(){labelsOn=!labelsOn;document.getElementById('btn-lbl').textContent='Labels: '+(labelsOn?'On':'Off');document.getElementById('btn-lbl').classList.toggle('on',labelsOn);applyLabels();}
function jumpToNode(label){const n=nodesData.find(x=>x._label===label);if(!n)return;switchTab('graph',document.querySelector('.tab[data-tab="graph"]'));setTimeout(()=>{resetAll();network.focus(n.id,{scale:2.0,animation:{duration:600}});network.selectNodes([n.id]);const conn=new Set([n.id,...network.getConnectedNodes(n.id)]);dataset_n.update(nodesData.map(x=>({id:x.id,opacity:conn.has(x.id)?1:0.08})));},200);}

network.on('oncontext',p=>{p.event.preventDefault();ctxNid=p.nodes&&p.nodes.length?p.nodes[0]:null;const m=document.getElementById('ctx');m.style.left=p.event.clientX+'px';m.style.top=p.event.clientY+'px';m.style.display='block';});
document.addEventListener('click',()=>document.getElementById('ctx').style.display='none');
function ctxNeighbours(){if(!ctxNid)return;const conn=new Set([ctxNid,...network.getConnectedNodes(ctxNid)]);dataset_n.update(nodesData.map(n=>({id:n.id,hidden:!conn.has(n.id)})));setTimeout(()=>network.fit(),350);}
function ctxCopyContext(){if(!ctxNid)return;const n=nodesData.find(x=>x.id===ctxNid);if(!n)return;const iss=ISSUES.find(i=>i.file===n._sf||i.node===n._label);if(iss){clipboard(issuePrompt(iss),null);flashToast('Issue context copied');return;}const nbr=network.getConnectedNodes(n.id).map(id=>nodesData.find(x=>x.id===id)).filter(Boolean).sort((a,b)=>b._d-a._d).slice(0,8).map(x=>x._label);const t='<context project="'+META.project+'">\n  <node label="'+n._label+'" file="'+n._sf+'" edges="'+n._d+'" module="'+n._comm+'" />\n  <dependencies>'+nbr.join(', ')+'</dependencies>\n</context>\n<task>Explain what this file does and how it fits the architecture.</task>';clipboard(t,null);flashToast('Node context copied');}
function ctxOpenIde(){if(!ctxNid)return;const n=nodesData.find(x=>x.id===ctxNid);if(n&&n._sf)openInIde(n._sf,'');}
function ctxShowIssues(){switchTab('issues',document.querySelector('.tab[data-tab="issues"]'));}

function flashToast(msg){let el=document.getElementById('toast');if(!el){el=document.createElement('div');el.id='toast';el.style.cssText='position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:#111827;color:#fff;padding:8px 16px;border-radius:8px;font-size:12px;font-weight:600;z-index:99999;box-shadow:0 4px 12px rgba(0,0,0,.25);opacity:0;transition:opacity .2s';document.body.appendChild(el);}el.textContent=msg;el.style.opacity='1';clearTimeout(el._t);el._t=setTimeout(()=>el.style.opacity='0',1600);}

document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'){if(e.key==='Escape')e.target.blur();return;}
  if(e.metaKey||e.ctrlKey||e.altKey)return;
  const k=e.key;
  if(k>='1'&&k<='4'){switchTab(TAB_ORDER[parseInt(k)-1]);}
  else if(k==='/'){e.preventDefault();switchTab('graph');const b=document.getElementById('fbox');if(b)b.focus();}
  else if(k==='i'){toggleIssuesOnly();}
  else if(k==='h'){switchTab('graph');toggleHubs();}
  else if(k==='f'){network.fit();}
  else if(k==='r'){resetAll();}
  else if(k==='c'){copyProjectContext(null);flashToast('Project summary copied');}
  else if(k==='Escape'){resetAll();network.unselectAll();}
});
"""


# ── Page skeleton ─────────────────────────────────────────────────────────────
_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%%PROJECT%% — Nodo Architecture Map</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
%%VIS_CSS_TAG%%
<style>%%CSS%%</style>
</head>
<body>

<div id="header">
  <div style="display:flex;align-items:center">
    <span class="brand">%%PROJECT%%</span>
    <div class="hdr-sep"></div>
    <span class="hdr-title">Architecture Map</span>
  </div>
  <div style="display:flex;align-items:center;gap:12px">
    <span class="hdr-meta">%%TOTAL_NODES%% files &middot; %%TOTAL_EDGES%% deps &middot; %%TOTAL_COMMS%% modules</span>
    <span id="age-pill" class="pill" title="Snapshot age — regenerate to refresh">built %%BUILD_DATE%%</span>
    <span class="pill %%PILL_CLS%%"><span class="pill-dot"></span>%%PILL_TXT%%</span>
  </div>
</div>

<div id="tabs">
  <div class="tab active" data-tab="graph" onclick="switchTab('graph',this)">Topology Graph</div>
  <div class="tab" data-tab="issues" onclick="switchTab('issues',this)">Issues <span class="tab-badge %%ISS_BADGE%%">%%TOTAL_ISS%%</span></div>
  <div class="tab" data-tab="flows" onclick="switchTab('flows',this)">Data Flow <span class="tab-badge tab-info">%%FLOW_COUNT%%</span></div>
  <div class="tab" data-tab="security" onclick="switchTab('security',this)">Security <span class="tab-badge tab-info">%%SENS_COUNT%%</span></div>
  <div class="tab" data-tab="api" onclick="switchTab('api',this)">API Reference <span class="tab-badge tab-info">%%API_COUNT%%</span></div>
  <div class="tab" data-tab="hubs" onclick="switchTab('hubs',this)">Hubs &amp; Modules</div>
  <div class="tab" data-tab="aicontext" onclick="switchTab('aicontext',this)">AI Context</div>
</div>

<div id="tab-body">

  <div id="sidebar">
    <div id="sb-hdr">NODE INFO</div>
    <div id="sb-node">Click a node to inspect it</div>
    <div id="sb-scroll">
      <div class="sb-sec">
        <div class="sb-lbl">File Types <small style="text-transform:none;letter-spacing:0;font-weight:400">(click to filter)</small></div>
        %%LEGEND%%
      </div>
      <div class="sb-sec">
        <div class="sb-lbl">Search &amp; Filter</div>
        <input id="fbox" type="text" placeholder="File name or path..." oninput="filterNodes(this.value)">
        <div class="btn-row">
          <span class="btn" onclick="network.fit()">Fit All</span>
          <span class="btn" onclick="resetAll()">Reset</span>
          <span class="btn" id="btn-phy" onclick="togglePhysics()">Physics: On</span>
        </div>
        <div class="btn-row">
          <span class="btn" id="btn-iss" onclick="toggleIssuesOnly()">Issues Only</span>
          <span class="btn" id="btn-hub" onclick="toggleHubs()" title="Highlight top hubs (key: h)">Hot Paths</span>
        </div>
        <div class="btn-row">
          <span class="btn" onclick="copyProjectContext(this)" title="Copy project summary for an AI agent (key: c)">Copy AI Summary</span>
        </div>
      </div>
      <div class="sb-sec">
        <div class="sb-lbl">Display</div>
        <div class="sld-row"><label class="sld-lbl">Node size <span id="sz-val">1.0&times;</span></label><input id="sz-slider" type="range" min="0.4" max="3" step="0.1" value="1" oninput="setNodeScale(this.value)"></div>
        <div class="sld-row"><label class="sld-lbl">Edge width <span id="ew-val">1.0&times;</span></label><input id="ew-slider" type="range" min="0.2" max="4" step="0.1" value="1" oninput="setEdgeScale(this.value)"></div>
        <div class="sld-row"><label class="sld-lbl">Label size <span id="lb-val">11px</span></label><input id="lb-slider" type="range" min="6" max="22" step="1" value="11" oninput="setLabelSize(this.value)"></div>
        <div class="btn-row" style="margin-top:6px"><span class="btn on" id="btn-lbl" onclick="toggleLabels()">Labels: On</span></div>
      </div>
      <div class="sb-sec">
        <div class="sb-lbl">Modules <small style="text-transform:none;letter-spacing:0;font-weight:400">(click to isolate)</small></div>
        <table class="ctbl"><thead><tr><th>#</th><th>Name</th><th>N</th></tr></thead><tbody>%%COMM_ROWS%%</tbody></table>
      </div>
    </div>
    <div id="sb-foot">%%TOTAL_NODES%% files &middot; %%TOTAL_EDGES%% deps &middot; %%N_ERR%%E %%N_WARN%%W %%N_INFO%%i</div>
  </div>

  <div id="graph-pane"><div id="graph"></div></div>

  <div id="issues-pane" class="cpane">
    <h1>Issues</h1>
    <p class="sub">Static analysis + graph topology. Click a card to jump to that file in the graph. Each card has an IDE link and a Copy AI Context button.</p>
    <div class="iss-summary">
      <div class="iss-stat"><div class="iss-n" style="color:var(--red)">%%N_ERR%%</div><div class="iss-l">Errors</div></div>
      <div class="iss-stat"><div class="iss-n" style="color:var(--amber)">%%N_WARN%%</div><div class="iss-l">Warnings</div></div>
      <div class="iss-stat"><div class="iss-n" style="color:var(--accent)">%%N_INFO%%</div><div class="iss-l">Info</div></div>
      <div class="iss-stat"><div class="iss-n">%%TOTAL_NODES%%</div><div class="iss-l">Files</div></div>
      <div class="iss-stat"><div class="iss-n">%%TOTAL_EDGES%%</div><div class="iss-l">Deps</div></div>
    </div>
    %%ISSUE_HTML%%
  </div>

  <div id="flows-pane" class="cpane">
    <h1>Data Flow</h1>
    <p class="sub">How each entry point (API route, page, or main file) moves through the code: step 1 is the entry, then each step shows the files imported at that depth. Read left to right. Click any file to jump to it in the graph. Auto-derived from real imports.</p>
    %%FLOWS_HTML%%
  </div>

  <div id="security-pane" class="cpane">
    <h1>Security &amp; Sensitive Surfaces</h1>
    <p class="sub">Auto-classified files that touch authentication, cryptography, secrets, payments, the database, network calls, or user input. These are the layers to review first in any audit. Click a file to inspect it in the graph or open it in your editor.</p>
    %%SENSITIVE_HTML%%
  </div>

  <div id="api-pane" class="cpane">
    <h1>API Reference</h1>
    <p class="sub">All HTTP routes detected in the project, grouped by domain, with the methods each handles. Click a route to jump to it in the graph; click the path to open the file in your editor.</p>
    %%API_HTML%%
  </div>

  <div id="hubs-pane" class="cpane">
    <h1>Hubs &amp; Modules</h1>
    <p class="sub">The highest-degree files (change them carefully) and the detected module clusters.</p>
    <div id="hub-list"></div>
    <h2>Modules</h2>
    <div id="mod-list"></div>
  </div>

  <div id="aicontext-pane" class="cpane">
    <h1>AI Context</h1>
    <p class="sub">Structured context for pasting into Claude Code, Cursor, Windsurf, or any LLM agent. Everything copies as clean Markdown/XML — no DOM parsing needed.</p>
    <div class="ai-card">
      <div class="ai-card-h">Sibling artifacts (point your terminal agent here)</div>
      <p style="margin-bottom:8px">Nodo writes machine-readable files next to this page:</p>
      <div class="ai-file"><code>nodo-context.json</code> &mdash; full graph, hubs, modules, every issue with line numbers + snippets. <button class="btn" onclick="copyText('Read nodo-context.json to understand this project architecture and current issues before making changes.',this)">Copy instruction</button></div>
      <div class="ai-file"><code>nodo-context.md</code> &mdash; token-cheap summary.</div>
    </div>
    <div class="ai-card">
      <div class="ai-card-h">Whole-project summary</div>
      <p style="margin-bottom:8px">Compact briefing: stats, hubs, modules, issue tally.</p>
      <button class="btn-primary" onclick="copyProjectContext(this)">Copy project summary</button>
    </div>
    <div class="ai-card">
      <div class="ai-card-h">All issues as a refactor backlog</div>
      <p style="margin-bottom:8px">Every flagged issue as a Markdown checklist with paths + line numbers.</p>
      <button class="btn-primary" onclick="copyAllIssues(this)">Copy all %%TOTAL_ISS%% issues</button>
      <button class="btn" onclick="copyIssuesBySeverity('warn',this)">Warnings only</button>
      <button class="btn" onclick="copyIssuesBySeverity('error',this)">Errors only</button>
    </div>
    <div class="ai-card">
      <div class="ai-card-h">Keyboard shortcuts</div>
      <div class="kbd-grid">
        <div><kbd>1</kbd>-<kbd>4</kbd> switch tabs</div><div><kbd>/</kbd> focus search</div>
        <div><kbd>i</kbd> issues-only</div><div><kbd>h</kbd> hot paths (hubs)</div>
        <div><kbd>f</kbd> fit graph</div><div><kbd>r</kbd> reset</div>
        <div><kbd>c</kbd> copy summary</div><div><kbd>Esc</kbd> clear</div>
      </div>
    </div>
  </div>

</div>

<div id="ctx">
  <div onclick="ctxNeighbours()">Show neighbours only</div>
  <div onclick="ctxCopyContext()">Copy AI context</div>
  <div onclick="ctxOpenIde()">Open in editor (VS Code)</div>
  <div onclick="ctxShowIssues()">Go to Issues tab</div>
  <div onclick="resetAll()">Show all nodes</div>
  <div onclick="network.fit()">Fit view</div>
</div>

%%VIS_SCRIPT_TAG%%
<script>
%%JS%%
// populate Hubs & Modules tab
(function(){
  const hl=document.getElementById('hub-list');
  if(hl) hl.innerHTML='<table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr><th style="text-align:left;padding:6px;border-bottom:2px solid var(--border2)">File</th><th style="text-align:right;padding:6px;border-bottom:2px solid var(--border2)">Edges</th></tr></thead><tbody>'+META.hubs.map(h=>'<tr style="cursor:pointer" onclick="jumpToNode(\''+h.file.split('/').pop()+'\')"><td style="padding:5px;font-family:var(--mono);font-size:11px;border-bottom:1px solid var(--border2)">'+h.file+'</td><td style="padding:5px;text-align:right;font-weight:600;border-bottom:1px solid var(--border2)">'+h.edges+'</td></tr>').join('')+'</tbody></table>';
  const ml=document.getElementById('mod-list');
  if(ml) ml.innerHTML=META.communities.map(c=>'<div style="margin:6px 0;padding:8px 12px;background:var(--bg2);border:1px solid var(--border2);border-radius:6px"><b>['+c.id+'] '+c.name+'</b> <span style="color:var(--text4);font-size:11px">— '+c.size+' files</span><div style="font-family:var(--mono);font-size:10px;color:var(--text3);margin-top:3px">'+(c.sample||[]).join(', ')+'</div></div>').join('');
})();
</script>
</body>
</html>"""
