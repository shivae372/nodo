"""
Optional tree-sitter backend (EXPERIMENTAL) — accurate import/symbol extraction.

Off by default. Enabled with `--ast`. Nodo's promise is clone-and-run with zero
dependencies, so this module is the *only* place that touches a third-party
library, it is imported lazily, and EVERY failure path returns None so the
caller silently falls back to the regex extractor. Installing tree-sitter only
*upgrades* accuracy; its absence never breaks a run.

To use:  pip install tree-sitter tree-sitter-languages   (then run with --ast)

Why it matters: the regex resolver can't see through generics, decorators, or
re-export barrels. A real parse tree can — which (later) lets the deliberately
disabled arg-count contract check be turned back on without false positives.
"""
import os

_PARSERS = {}          # ext -> parser | None (cache; None = unavailable)
_CHECKED = False
_AVAILABLE = False

# ext -> tree-sitter-languages name
_LANG_NAME = {
    '.py': 'python',
    '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.ts': 'typescript', '.tsx': 'tsx', '.mts': 'typescript', '.cts': 'typescript',
    '.go': 'go', '.rs': 'rust', '.java': 'java', '.rb': 'ruby', '.php': 'php',
    '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.hpp': 'cpp', '.cc': 'cpp', '.cs': 'c_sharp',
}


def available():
    """True if tree-sitter is importable. Never raises."""
    global _CHECKED, _AVAILABLE
    if _CHECKED:
        return _AVAILABLE
    _CHECKED = True
    try:
        import tree_sitter_languages  # noqa: F401
        _AVAILABLE = True
    except Exception:
        try:
            import tree_sitter  # noqa: F401  (some setups register grammars differently)
            _AVAILABLE = True
        except Exception:
            _AVAILABLE = False
    return _AVAILABLE


def _get_parser(ext):
    if ext in _PARSERS:
        return _PARSERS[ext]
    parser = None
    name = _LANG_NAME.get(ext)
    if name and available():
        try:
            from tree_sitter_languages import get_parser
            parser = get_parser(name)
        except Exception:
            parser = None
    _PARSERS[ext] = parser
    return parser


def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children)


def extract_imports_ast(rel, text):
    """Return a list of raw import target strings via tree-sitter, or None to
    signal the caller to use the regex path. Catches everything."""
    ext = os.path.splitext(rel)[1].lower()
    parser = _get_parser(ext)
    if parser is None:
        return None
    try:
        tree = parser.parse(bytes(text, 'utf-8'))
        src = text.encode('utf-8')
        out = []

        def txt(node):
            return src[node.start_byte:node.end_byte].decode('utf-8', 'ignore')

        for n in _walk(tree.root_node):
            t = n.type
            # JS/TS: import ... from 'x'  /  require('x')  /  import('x')
            if t in ('import_statement', 'export_statement', 'import_require_clause',
                     'call_expression'):
                for c in _walk(n):
                    if c.type in ('string', 'string_fragment'):
                        s = txt(c).strip('\'"`')
                        if s and ('/' in s or s.startswith('.') or s.isidentifier()):
                            out.append(s)
                            break
            # Python: import a.b / from a.b import c
            elif t == 'import_from_statement':
                mod = n.child_by_field_name('module_name')
                if mod is not None:
                    out.append(txt(mod))
            elif t == 'import_statement' and rel.endswith('.py'):
                out.append(txt(n).replace('import', '', 1).strip().split(' as ')[0].split(',')[0].strip())
        # de-dup, keep order
        seen, uniq = set(), []
        for s in out:
            if s and s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq
    except Exception:
        return None
