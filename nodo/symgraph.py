"""
Symbol-level graph (advanced mode) — functions, classes and methods as
first-class nodes, with `defines` (file → symbol), `calls` (symbol → symbol) and
`inherits` (class → base) edges. Deterministic, tree-sitter only (JS/TS/Python).

This is the finer-grained layer: file-level imports tell you *which files* relate;
the symbol graph tells you *which functions/classes* relate, and how. Output is
both hierarchical (`by_file`: each file's symbols) and flat (`nodes`/`edges`) so an
agent can traverse either way. Names are resolved by identifier (classic
approximation) — fast, private, no LLM.
"""
import os
from collections import defaultdict

_JSTS = {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts'}


def available():
    from . import callgraph
    return callgraph.available()


def _symbols(rel, text):
    """(symbols, inherits): symbols=[(name, kind, line)] (kind func/class/method);
    inherits=[(class, base)] from extends/implements/superclasses."""
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

    def field(n, k):
        try:
            return n.child_by_field_name(k)
        except Exception:
            return None

    is_py = ext == '.py'
    syms, inh = [], []
    for n in ast_index._walk(tree.root_node):
        t = n.type
        if is_py:
            if t == 'function_definition':
                nm = field(n, 'name')
                if nm:
                    syms.append((txt(nm), 'func', n.start_point[0] + 1))
            elif t == 'class_definition':
                nm = field(n, 'name')
                if nm:
                    cname = txt(nm)
                    syms.append((cname, 'class', n.start_point[0] + 1))
                    sup = field(n, 'superclasses')
                    if sup:
                        for c in sup.children:
                            if c.type in ('identifier', 'attribute'):
                                inh.append((cname, txt(c).split('.')[-1]))
        else:
            if t in ('function_declaration', 'generator_function_declaration'):
                nm = field(n, 'name')
                if nm and nm.type == 'identifier':
                    syms.append((txt(nm), 'func', n.start_point[0] + 1))
            elif t == 'method_definition':
                nm = field(n, 'name')
                if nm and nm.type in ('property_identifier', 'identifier'):
                    syms.append((txt(nm), 'method', n.start_point[0] + 1))
            elif t == 'class_declaration':
                nm = field(n, 'name')
                if nm:
                    cname = txt(nm)
                    syms.append((cname, 'class', n.start_point[0] + 1))
                    for c in n.children:
                        if c.type == 'class_heritage':
                            for d in ast_index._walk(c):
                                if d.type in ('identifier', 'type_identifier'):
                                    base = txt(d)
                                    if base != cname:
                                        inh.append((cname, base))
            elif t == 'variable_declarator':
                nm, val = field(n, 'name'), field(n, 'value')
                if (nm and nm.type == 'identifier' and val is not None
                        and val.type in ('arrow_function', 'function_expression', 'function')):
                    syms.append((txt(nm), 'func', n.start_point[0] + 1))
    return syms, inh


def build_symbol_graph(nodes, file_texts, cap=8000):
    """Return {available, nodes, edges, by_file, counts}. Empty unless AST is active."""
    if not available():
        return {'available': False, 'nodes': [], 'edges': [], 'by_file': {}, 'counts': {}}
    from . import callgraph
    cg = callgraph.build_call_graph(nodes, file_texts, cap=cap)

    by_file, defined, classes, inherits = defaultdict(list), set(), set(), []
    for n in nodes:
        rel = n['rel']
        if os.path.splitext(rel)[1].lower() not in (_JSTS | {'.py'}):
            continue
        text = file_texts.get(rel, '')
        if not text:
            continue
        syms, inh = _symbols(rel, text)
        seen_names = set()
        for nm, kind, line in syms:
            if nm in seen_names:
                continue
            seen_names.add(nm)
            by_file[rel].append({'name': nm, 'kind': kind, 'line': line})
            defined.add(nm)
            if kind == 'class':
                classes.add(nm)
        inherits.extend((cls, base) for cls, base in inh)

    gnodes, gedges, sym_id = [], [], {}
    for rel in sorted(by_file):
        gnodes.append({'id': f'file:{rel}', 'label': rel.split('/')[-1], 'kind': 'file', 'rel': rel})
        for s in by_file[rel]:
            sid = f'sym:{rel}:{s["name"]}'
            sym_id.setdefault(s['name'], sid)        # name → first definition
            gnodes.append({'id': sid, 'label': s['name'], 'kind': 'symbol',
                           'symtype': s['kind'], 'rel': rel, 'line': s['line']})
            gedges.append({'from': f'file:{rel}', 'to': sid, 'type': 'defines'})
    for e in cg.get('edges', []):
        a, b = sym_id.get(e['from']), sym_id.get(e['to'])
        if a and b and a != b:
            gedges.append({'from': a, 'to': b, 'type': 'calls'})
    for cls, base in inherits:
        a, b = sym_id.get(cls), sym_id.get(base)
        if a and b and base in classes and a != b:
            gedges.append({'from': a, 'to': b, 'type': 'inherits'})

    seen, ded = set(), []
    for e in gedges:
        k = (e['from'], e['to'], e['type'])
        if k in seen:
            continue
        seen.add(k)
        ded.append(e)
    ded = ded[:cap]
    counts = {'files': len(by_file),
              'symbols': sum(len(v) for v in by_file.values()),
              'classes': len(classes),
              'defines': sum(1 for e in ded if e['type'] == 'defines'),
              'calls': sum(1 for e in ded if e['type'] == 'calls'),
              'inherits': sum(1 for e in ded if e['type'] == 'inherits')}
    return {'available': True, 'nodes': gnodes, 'edges': ded,
            'by_file': {k: by_file[k] for k in sorted(by_file)}, 'counts': counts}
