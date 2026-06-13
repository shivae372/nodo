"""
Derived insights — auto-generated "Flows" and "Sensitive surfaces" analyses.

Data Flow and Security views generated from the dependency graph so they work on
ANY project with zero configuration. No framework assumptions beyond generic
patterns.

  - entry_flows():   pick entry points (API routes, pages, CLI mains) and trace
                     a few levels of imports inward — "this endpoint touches X, Y, Z".
  - sensitive_map(): classify files that handle auth, crypto, secrets, payments,
                     database, or external network calls into security layers.
  - api_routes():    derive a clean, grouped HTTP route list from API files +
                     the methods they export/handle (a real API reference).
"""
import re
from collections import defaultdict


# ── Entry-point detection ─────────────────────────────────────────────────────
def _is_entry(node):
    rel = node['rel'].lower()
    cat = node['category']
    base = rel.split('/')[-1]
    if cat in ('api', 'page'):
        return True
    if base in ('main.py', '__main__.py', 'index.js', 'index.ts', 'server.js',
                'server.ts', 'app.py', 'cli.py', 'manage.py'):
        return True
    if re.search(r'(route|handler|endpoint|controller)\.(t|j)s$', rel):
        return True
    return False


def entry_flows(nodes, edges, limit=20, depth=2):
    """For each entry point, return what it reaches via imports (depth-limited).

    Returns [{entry, category, reaches: [rel,...], reach_count}], sorted by reach.
    """
    id_to = {n['id']: n for n in nodes}
    out_adj = defaultdict(list)   # who do I import
    for e in edges:
        out_adj[e['source']].append(e['target'])

    entries = [n for n in nodes if _is_entry(n)]

    flows = []
    for ent in entries:
        seen = set()
        # ordered BFS layers — step 1 is the entry, step 2 its direct imports, etc.
        layers = [[ent['rel']]]
        frontier = [ent['id']]
        for _ in range(depth):
            nxt = []
            layer_rels = []
            for nid in frontier:
                for t in out_adj.get(nid, []):
                    if t not in seen and t != ent['id']:
                        seen.add(t)
                        nxt.append(t)
                        layer_rels.append(id_to[t]['rel'])
            if layer_rels:
                layers.append(sorted(layer_rels))
            frontier = nxt
        reaches = sorted(id_to[t]['rel'] for t in seen)
        if reaches:
            flows.append({
                'entry': ent['rel'],
                'category': ent['category'],
                'reaches': reaches,
                'reach_count': len(reaches),
                'steps': layers,   # ordered call sequence by import depth
            })
    # most-reaching entry points first — they're the most important paths
    flows.sort(key=lambda f: f['reach_count'], reverse=True)
    return flows[:limit]


# ── Sensitive-surface classification ─────────────────────────────────────────
# (layer, label, regex over path + content) — generic across languages/frameworks.
SENSITIVE_LAYERS = [
    ('auth', 'Authentication & Authorization',
     re.compile(r'\b(getUser\(|getSession\(|signIn|signOut|jwt\.|verifyToken|'
                r'authenticate|authorize|requireAuth|isAdmin|adminGuard|'
                r'hasPermission|checkRole|rbac|\.auth\.|passport\.|bcrypt\.compare|'
                r'oauth|login|logout)\b', re.I)),
    ('crypto', 'Cryptography & Hashing',
     re.compile(r'\b(crypto|encrypt|decrypt|hash|hmac|bcrypt|argon2|scrypt|'
                r'cipher|signature|sign\(|verify\(|randomBytes|secretbox)\b', re.I)),
    ('secrets', 'Secrets & Environment',
     re.compile(r'(process\.env|os\.environ|getenv|API_KEY|SECRET|PRIVATE_KEY|'
                r'\.env|dotenv|credentials)', re.I)),
    ('payment', 'Payments & Billing',
     re.compile(r'\b(stripe\.|paypal|dodopayments|\.charges\.|\.subscriptions\.|'
                r'createCheckout|createInvoice|payment_intent|webhookSecret|'
                r'priceId|productId)\b', re.I)),
    ('database', 'Database & Data Access',
     re.compile(r'(\.from\([\'"]|prisma\.|mongoose\.|sequelize|knex\(|'
                r'\.rpc\(|INSERT INTO|UPDATE .*SET|DELETE FROM|CREATE TABLE|'
                r'\.insert\(|\.update\(|\.delete\(|\.select\()', re.I)),
    ('network', 'External Network Calls',
     re.compile(r'\b(fetch\(|axios|http\.|https\.|requests\.|urllib|got\(|'
                r'webhook|api\.|\.post\(|\.get\()', re.I)),
    ('upload', 'File Upload & User Input',
     re.compile(r'\b(multer|upload|formidable|multipart|req\.body|req\.file|'
                r'sanitize|validate|dangerouslySetInnerHTML|eval\()', re.I)),
]


def sensitive_map(nodes, file_texts, per_layer=12):
    """Classify files into security-relevant layers by path + content patterns.

    Returns [{layer, label, files: [{rel, hits:[matched terms]}], count}].
    """
    layers = []
    for key, label, rx in SENSITIVE_LAYERS:
        matched = []
        for n in nodes:
            rel = n['rel']
            text = file_texts.get(rel, '')
            # match on path OR content; content match is stronger signal
            path_hit = rx.search(rel)
            body_hits = rx.findall(text) if text else []
            if path_hit or body_hits:
                terms = set()
                if path_hit:
                    terms.add(path_hit.group(0).lower())
                for h in body_hits[:5]:
                    t = (h if isinstance(h, str) else h[0]).strip().lower()
                    if t:
                        terms.add(t[:24])
                matched.append({'rel': rel, 'hits': sorted(terms)[:6],
                                'strength': len(body_hits) + (2 if path_hit else 0)})
        matched.sort(key=lambda m: m['strength'], reverse=True)
        if matched:
            layers.append({
                'layer': key, 'label': label,
                'files': [{'rel': m['rel'], 'hits': m['hits']} for m in matched[:per_layer]],
                'count': len(matched),
            })
    return layers


# ── API reference ─────────────────────────────────────────────────────────────
_HTTP_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']

# how to turn a file path into a clean URL-ish route, per framework convention
def _route_path(rel):
    p = rel
    # Next.js app router: app/api/x/route.ts -> /api/x ; app/x/page.tsx -> /x
    p = re.sub(r'/route\.(t|j)sx?$', '', p)
    p = re.sub(r'/(page|layout|index)\.(t|j)sx?$', '', p)
    # strip a leading src/ or app dir marker but keep /api
    p = re.sub(r'^(src/)?app/', '/', p)
    p = re.sub(r'^(src/)?pages/', '/', p)
    p = re.sub(r'^src/', '/', p)
    # strip remaining extension for non-route files
    p = re.sub(r'\.(t|j)sx?$|\.py$|\.rb$|\.go$', '', p)
    if not p.startswith('/'):
        p = '/' + p
    p = re.sub(r'//+', '/', p)
    return p


def _methods_in(text):
    """Detect HTTP methods a route file handles (exports or registers)."""
    found = []
    for m in _HTTP_METHODS:
        # Next.js: `export async function GET` / `export const GET`
        # Express/Flask/etc: `.get(` `.post(` `@app.route(..., methods=['GET'])`
        if (re.search(rf'\bexport\s+(async\s+)?(function|const)\s+{m}\b', text)
                or re.search(rf'\.{m.lower()}\s*\(', text)
                or re.search(rf"methods\s*=\s*\[[^\]]*['\"]{m}['\"]", text, re.I)
                or re.search(rf"@\w+\.{m.lower()}\b", text)):
            found.append(m)
    return found


def api_routes(nodes, file_texts):
    """Return grouped route list: [{group, routes:[{path, methods, file}]}].

    A file is a route if its category is 'api' or its path looks like one. The
    group is the first meaningful path segment (e.g. 'account', 'admin').
    """
    METHOD_COLOR = {
        'GET': '#3b82f6', 'POST': '#10b981', 'PUT': '#f59e0b',
        'PATCH': '#8b5cf6', 'DELETE': '#dc2626', 'HEAD': '#64748b',
        'OPTIONS': '#64748b',
    }
    routes = []
    for n in nodes:
        rel = n['rel']
        is_api = n['category'] == 'api' or re.search(r'/api/|/routes?/|route\.(t|j)s', rel)
        if not is_api:
            continue
        text = file_texts.get(rel, '')
        methods = _methods_in(text) or ['—']
        path = _route_path(rel)
        routes.append({'path': path, 'methods': methods, 'file': rel,
                       'colors': [METHOD_COLOR.get(m, '#64748b') for m in methods]})
    routes.sort(key=lambda r: r['path'])

    # group by the segment after /api/ (or the first segment)
    groups = defaultdict(list)
    for r in routes:
        parts = [s for s in r['path'].split('/') if s]
        if 'api' in parts:
            idx = parts.index('api')
            grp = parts[idx + 1] if idx + 1 < len(parts) else 'api'
        else:
            grp = parts[0] if parts else 'root'
        grp = re.sub(r'\[.*?\]', '', grp) or 'root'
        groups[grp].append(r)

    return [{'group': g.replace('-', ' ').title(), 'routes': groups[g]}
            for g in sorted(groups)]
