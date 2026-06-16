"""
Function-level call graph — the fine-grained, "advanced" semantic layer.

Deterministic and offline: built from tree-sitter parse trees, so it only runs
when AST parsing is active (`--deep` / `--ast`). Edges are function → function
("caller calls callee"), kept ONLY when the callee resolves to a symbol DEFINED
in the project — so it's a real call graph, not a guess. Enclosing scope comes
from node line-ranges (innermost def wins). JS/TS/Python — nodo's deepest set.

Names are resolved by identifier (a lightweight, classic call-graph approximation:
a call to `foo()` links to a project `foo` definition), not by full type
resolution — fast, private, no LLM. Claude reasons over the result.
"""
import os
import re
from collections import defaultdict

_JSTS = {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts'}


def available():
    from . import scanner, ast_index
    return getattr(scanner, '_USE_AST', False) and ast_index.available()


# Call-node types across grammars (JS/TS/C/C++/Rust, Python, Java, C#, …). We keep
# only BARE-identifier callees (foo(), not obj.foo()/new F()) so edges stay low-FP.
_CALL_TYPES = {'call_expression', 'call', 'method_invocation',
               'function_call_expression', 'invocation_expression'}


def _eligible(ext):
    from . import ast_index
    return ast_index._get_parser(ext) is not None


def _defs_and_calls(rel, text):
    """(defs, calls) for one file via tree-sitter: defs=[(name,start,end)],
    calls=[(callee,line)]. Grammar-agnostic (any installed grammar): definitions
    via the shared def-type matcher + name resolver; bare-identifier calls only."""
    from . import ast_index
    ext = os.path.splitext(rel)[1].lower()
    parser = ast_index._get_parser(ext)
    if parser is None:
        return [], []
    try:
        src = text.encode('utf-8')
        tree = parser.parse(src)
    except Exception:
        return [], []

    def txt(n):
        return src[n.start_byte:n.end_byte].decode('utf-8', 'ignore')

    def field(n, name):
        try:
            return n.child_by_field_name(name)
        except Exception:
            return None

    defs, calls = [], []
    for n in ast_index._walk(tree.root_node):
        t = n.type
        if ast_index._is_def_type(t):
            nm = ast_index._name_of(n, txt, field)
            if nm:
                defs.append((nm, n.start_point[0] + 1, n.end_point[0] + 1))
        elif t == 'variable_declarator':                 # JS: const f = () => …
            nm, val = field(n, 'name'), field(n, 'value')
            if (nm and nm.type == 'identifier' and val is not None
                    and val.type in ('arrow_function', 'function_expression', 'function')):
                defs.append((txt(nm), n.start_point[0] + 1, n.end_point[0] + 1))
        elif t in _CALL_TYPES:
            fn = field(n, 'function') or field(n, 'name')
            if fn is not None and fn.type in ('identifier', 'simple_identifier'):
                calls.append((txt(fn), n.start_point[0] + 1))
    return defs, calls


def build_call_graph(nodes, file_texts, cap=20000):
    """Return {'available', 'edges':[{from,to,file}], 'callers':{}, 'callees':{},
    'def_count'}. Empty (available=False) unless AST parsing is active."""
    empty = {'available': False, 'edges': [], 'callers': {}, 'callees': {}, 'def_count': 0}
    if not available():
        return empty
    per_file, defined = {}, set()
    for n in nodes:
        rel = n['rel']
        if not _eligible(os.path.splitext(rel)[1].lower()):   # any grammar tree-sitter has
            continue
        text = file_texts.get(rel, '')
        if not text:
            continue
        defs, calls = _defs_and_calls(rel, text)
        if defs or calls:
            per_file[rel] = (defs, calls)
            for nm, _s, _e in defs:
                defined.add(nm)
    edges = set()
    for rel, (defs, calls) in per_file.items():
        ranges = sorted(defs, key=lambda d: (d[2] - d[1], d[1]))   # innermost first
        for callee, line in calls:
            if callee not in defined:          # only resolved calls become edges
                continue
            for nm, s, e in ranges:
                if s <= line <= e and nm != callee:
                    edges.add((nm, callee, rel))   # innermost enclosing def is the caller
                    break
    edges = sorted(edges)[:cap]
    callers, callees = defaultdict(set), defaultdict(set)
    for a, b, _f in edges:
        callees[a].add(b)
        callers[b].add(a)
    return {'available': True,
            'edges': [{'from': a, 'to': b, 'file': f} for a, b, f in edges],
            'callers': {k: sorted(v) for k, v in callers.items()},
            'callees': {k: sorted(v) for k, v in callees.items()},
            'def_count': len(defined)}


def query_symbol_calls(cg, symbol):
    """Readable 'called by / calls' for one symbol, or None if it isn't in the graph."""
    if not cg.get('available'):
        return None
    callers = cg.get('callers', {}).get(symbol, [])
    callees = cg.get('callees', {}).get(symbol, [])
    if not callers and not callees:
        return None
    out = [f"CALL GRAPH  {symbol}", ""]
    out.append(f"CALLED BY ({len(callers)}):" if callers else "CALLED BY: nothing in-project")
    for c in callers[:30]:
        out.append(f"  <- {c}()")
    if len(callers) > 30:
        out.append(f"  … +{len(callers) - 30} more")
    out.append("")
    out.append(f"CALLS ({len(callees)}):" if callees else "CALLS: no in-project functions")
    for c in callees[:30]:
        out.append(f"  -> {c}()")
    if len(callees) > 30:
        out.append(f"  … +{len(callees) - 30} more")
    return '\n'.join(out)


def top_hubs(cg, n=10):
    """Most-called functions (call-graph in-degree) — semantic load-bearing fns."""
    deg = sorted(cg.get('callers', {}).items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return [(name, len(callers)) for name, callers in deg[:n]]


def to_mermaid(cg, max_edges=200):
    """Render the call graph as a Mermaid flowchart (paste into Markdown / PRs)."""
    def nid(s):
        return 'n_' + re.sub(r'\W', '_', s)
    lines = ['flowchart LR']
    seen = set()
    for e in cg.get('edges', [])[:max_edges]:
        a, b = e['from'], e['to']
        for x in (a, b):
            if x not in seen:
                seen.add(x)
                lines.append(f'  {nid(x)}["{x}()"]')
        lines.append(f'  {nid(a)} --> {nid(b)}')
    return '\n'.join(lines) + '\n'


def to_dot(cg, max_edges=600):
    """Render the call graph as Graphviz DOT (`dot -Tsvg`)."""
    lines = ['digraph callgraph {', '  rankdir=LR;', '  node [shape=box, fontsize=10];']
    for e in cg.get('edges', [])[:max_edges]:
        a = e['from'].replace('"', '')
        b = e['to'].replace('"', '')
        lines.append(f'  "{a}" -> "{b}";')
    lines.append('}')
    return '\n'.join(lines) + '\n'
