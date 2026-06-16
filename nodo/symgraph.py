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


def _clean_doc(s):
    """First line of a docstring/comment, stripped of comment syntax and quotes."""
    s = s.strip().strip('"\'`')
    for pre in ('/**', '*/', '//', '#', '*'):
        s = s.strip().lstrip(pre)
    s = s.strip().strip('"\'`').strip()
    first = next((ln.strip(' *') for ln in s.splitlines() if ln.strip(' *')), '')
    return first[:120]


_CLASS_KW = ('class', 'struct', 'interface', 'trait', 'enum', 'record', 'union',
             'protocol', 'namespace', 'object')
_HERITAGE = {'class_heritage', 'base_class_clause', 'extends_clause', 'implements_clause',
             'super_interfaces', 'superclass', 'extends_interfaces'}


def _kind_of(t):
    if any(k in t for k in _CLASS_KW):
        return 'class'
    if 'method' in t:
        return 'method'
    return 'func'


def _bases(node, txt, field, ast_index):
    """Base/parent type names for a class-ish node, best-effort across languages."""
    out = []
    sup = field(node, 'superclasses')          # Python argument_list
    if sup is not None:
        for c in sup.children:
            if c.type in ('identifier', 'attribute'):
                out.append(txt(c).split('.')[-1])
    sc = field(node, 'superclass')             # Java / some grammars (a field)
    if sc is not None:
        for d in ast_index._walk(sc):
            if d.type in ('type_identifier', 'identifier'):
                out.append(txt(d).split('.')[-1])
    for c in node.children:                    # JS class_heritage / C++ base_class_clause / …
        if c.type in _HERITAGE:
            for d in ast_index._walk(c):
                if d.type in ('identifier', 'type_identifier', 'scoped_type_identifier',
                              'simple_identifier'):
                    out.append(txt(d).split('::')[-1].split('.')[-1])
    return out


def _symbols(rel, text):
    """(symbols, inherits): symbols=[(name, kind, start, end, doc)]; inherits=
    [(class, base)]. Grammar-agnostic — works for any installed tree-sitter grammar
    (C/C++/Go/Rust/Java/… not just JS/TS/Python) via the shared def-type matcher."""
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

    def doc_of(n):
        if is_py:
            body = field(n, 'body')
            if body is not None:
                for c in body.children:
                    if c.is_named:
                        if c.type == 'expression_statement' and c.children and \
                                c.children[0].type == 'string':
                            return _clean_doc(txt(c.children[0]))
                        break
            return ''
        prev = getattr(n, 'prev_named_sibling', None)
        if prev is not None and prev.type == 'comment':
            return _clean_doc(txt(prev))
        return ''

    syms, inh = [], []
    for n in ast_index._walk(tree.root_node):
        t = n.type
        if ast_index._is_def_type(t):
            nm = ast_index._name_of(n, txt, field)
            if not nm:
                continue
            kind = _kind_of(t)
            syms.append((nm, kind, n.start_point[0] + 1, n.end_point[0] + 1, doc_of(n)))
            if kind == 'class':
                for b in _bases(n, txt, field, ast_index):
                    if b and b != nm:
                        inh.append((nm, b))
        elif t == 'variable_declarator':            # JS: const f = () => …
            nm, val = field(n, 'name'), field(n, 'value')
            if (nm and nm.type == 'identifier' and val is not None
                    and val.type in ('arrow_function', 'function_expression', 'function')):
                syms.append((txt(nm), 'func', n.start_point[0] + 1, n.end_point[0] + 1, doc_of(n)))
    return syms, inh


def build_symbol_graph(nodes, file_texts, cap=40000):
    """Return {available, nodes, edges, by_file, counts}. Empty unless AST is active."""
    if not available():
        return {'available': False, 'nodes': [], 'edges': [], 'by_file': {}, 'counts': {}}
    from . import callgraph
    cg = callgraph.build_call_graph(nodes, file_texts, cap=cap)

    by_file, defined, classes = defaultdict(list), set(), set()
    inherits, contains = [], []
    from . import callgraph as _cg
    for n in nodes:
        rel = n['rel']
        if not _cg._eligible(os.path.splitext(rel)[1].lower()):   # any tree-sitter grammar
            continue
        text = file_texts.get(rel, '')
        if not text:
            continue
        syms, inh = _symbols(rel, text)
        cranges = [(nm, s, e) for nm, kind, s, e, _d in syms if kind == 'class']
        seen_names = set()
        for nm, kind, start, end, doc in syms:
            if nm in seen_names:
                continue
            seen_names.add(nm)
            by_file[rel].append({'name': nm, 'kind': kind, 'line': start, 'doc': doc})
            defined.add(nm)
            if kind == 'class':
                classes.add(nm)
        for nm, kind, start, end, doc in syms:           # symbol → enclosing class (innermost)
            for cn, cs, ce in sorted(cranges, key=lambda r: r[2] - r[1]):
                if cs <= start <= ce and cn != nm:
                    contains.append((rel, cn, nm))
                    break
        inherits.extend((cls, base) for cls, base in inh)

    gnodes, sym_id = [], {}
    def_edges, contains_edges, calls_edges, inh_edges = [], [], [], []
    for rel in sorted(by_file):
        gnodes.append({'id': f'file:{rel}', 'label': rel.split('/')[-1], 'kind': 'file', 'rel': rel})
        for s in by_file[rel]:
            sid = f'sym:{rel}:{s["name"]}'
            sym_id.setdefault(s['name'], sid)        # name → first definition
            node = {'id': sid, 'label': s['name'], 'kind': 'symbol',
                    'symtype': s['kind'], 'rel': rel, 'line': s['line']}
            if s.get('doc'):
                node['rationale'] = s['doc']
            gnodes.append(node)
            def_edges.append({'from': f'file:{rel}', 'to': sid, 'type': 'defines'})
    for rel, cn, meth in contains:                       # class → method containment
        contains_edges.append({'from': f'sym:{rel}:{cn}', 'to': f'sym:{rel}:{meth}', 'type': 'contains'})
    for e in cg.get('edges', []):
        a, b = sym_id.get(e['from']), sym_id.get(e['to'])
        if a and b and a != b:
            calls_edges.append({'from': a, 'to': b, 'type': 'calls'})
    for cls, base in inherits:
        a, b = sym_id.get(cls), sym_id.get(base)
        if a and b and base in classes and a != b:
            inh_edges.append({'from': a, 'to': b, 'type': 'inherits'})

    # Relationship edges (calls/inherits/contains) are the valuable, traversal-worthy
    # ones — keep them ahead of the bulky `defines` edges so a cap never starves them.
    seen, ded = set(), []
    for e in inh_edges + calls_edges + contains_edges + def_edges:
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
              'contains': sum(1 for e in ded if e['type'] == 'contains'),
              'inherits': sum(1 for e in ded if e['type'] == 'inherits')}
    return {'available': True, 'nodes': gnodes, 'edges': ded,
            'by_file': {k: by_file[k] for k in sorted(by_file)}, 'counts': counts}
