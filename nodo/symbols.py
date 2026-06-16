"""
Symbol-level index — definitions and references, zero-dependency.

File-level blast radius (query.py) answers "what imports this file". This adds
the finer question an agent actually asks: "where is `AudioEngine` defined and
who references it — or is it dead?" Symbol granularity makes the disconnected-
feature and dead-code findings self-verifying without grepping.

Regex-level (no parser), so it errs toward recall; treat reference counts as
"appears in N files", not a compiler-grade call graph. With --ast enabled and a
grammar installed, definitions come from real parse trees instead.
"""
import re
from collections import defaultdict

_IDENT = r'[A-Za-z_$][A-Za-z0-9_$]*'


def _is_js(rel):
    return rel.lower().endswith(('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts', '.vue', '.svelte'))


def _is_py(rel):
    return rel.lower().endswith('.py')


def _merge_defs(*lists):
    """Concatenate def lists, de-duping by (name, line), order-preserving."""
    seen, out = set(), []
    for lst in lists:
        for pair in lst:
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
    return out


def _lesson_defs(rel, text):
    """Definitions from taught def_patterns (additive), or [] if untaught."""
    from . import scanner
    if not getattr(scanner, '_LESSONS', None):
        return []
    from . import lessons as _l
    return _l.extract_defs(rel, text, scanner._LESSONS) or []


def _defs_in(rel, text):
    """Return [(name, line)] for definitions in one file (functions/classes/consts).
    Uses tree-sitter when the parser is active (more accurate), else regex. Taught
    lesson patterns are merged in so a learned language has symbols too."""
    from . import scanner, ast_index
    lesson_defs = _lesson_defs(rel, text)
    if getattr(scanner, '_USE_AST', False):
        ast_defs = ast_index.extract_defs_ast(rel, text)
        if ast_defs is not None:
            return _merge_defs(ast_defs, lesson_defs)
    defs = []
    if _is_js(rel):
        pats = [
            r'(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+(%s)' % _IDENT,
            r'(?:export\s+)?(?:abstract\s+)?class\s+(%s)' % _IDENT,
            r'(?:export\s+)?(?:const|let|var)\s+(%s)\s*=' % _IDENT,
            r'(?:export\s+)?(?:type|interface|enum)\s+(%s)' % _IDENT,
        ]
    elif _is_py(rel):
        pats = [
            r'^\s*(?:async\s+)?def\s+(%s)' % _IDENT,
            r'^\s*class\s+(%s)' % _IDENT,
        ]
    else:
        return _merge_defs(defs, lesson_defs)
    for pat in pats:
        for m in re.finditer(pat, text, re.M):
            defs.append((m.group(1), text[:m.start()].count('\n') + 1))
    return _merge_defs(defs, lesson_defs)


def build_symbol_index(nodes, file_texts):
    """{symbol: {'defs': [(rel, line)], 'ref_files': set(rel)}}.

    A reference is any whole-word occurrence of the symbol in a file other than
    on a line that defines it. Cheap and language-agnostic."""
    index = defaultdict(lambda: {'defs': [], 'ref_files': set()})
    # 1) definitions
    def_lines = defaultdict(set)  # rel -> {line numbers that are defs}
    for n in nodes:
        rel = n['rel']
        text = file_texts.get(rel, '')
        if not text:
            continue
        for name, line in _defs_in(rel, text):
            index[name]['defs'].append((rel, line))
            def_lines[rel].add(line)
    # 2) references (whole-word, excluding the defining line)
    names = [k for k in index.keys() if len(k) >= 3]
    if not names:
        return index
    big = re.compile(r'\b(' + '|'.join(re.escape(n) for n in names) + r')\b')
    for n in nodes:
        rel = n['rel']
        text = file_texts.get(rel, '')
        if not text:
            continue
        for i, line in enumerate(text.split('\n'), 1):
            if i in def_lines.get(rel, ()):  # skip the definition site itself
                continue
            for m in big.finditer(line):
                index[m.group(1)]['ref_files'].add(rel)
    return index


def query_symbol(nodes, file_texts, needle):
    """Human-readable report for one symbol: definition(s) + referencing files,
    or a confirmation that it is unreferenced. Returns a string, or None if the
    name isn't a defined symbol (so the caller can fall back to a file query)."""
    index = build_symbol_index(nodes, file_texts)
    # exact, then case-insensitive
    name = needle
    if name not in index:
        ci = [k for k in index if k.lower() == needle.lower()]
        if not ci:
            return None
        name = ci[0]

    info = index[name]
    if not info['defs']:
        return None
    out = [f"SYMBOL  {name}"]
    out.append("")
    out.append(f"DEFINED IN ({len(info['defs'])}):")
    for rel, line in sorted(info['defs']):
        out.append(f"  {rel}:L{line}")

    ref_files = sorted(f for f in info['ref_files'])
    out.append("")
    if ref_files:
        out.append(f"REFERENCED IN ({len(ref_files)} file(s)):")
        for rel in ref_files[:30]:
            out.append(f"  -> {rel}")
        if len(ref_files) > 30:
            out.append(f"  ... +{len(ref_files) - 30} more")
    else:
        out.append("REFERENCED IN: 0 files — nothing references this symbol.")
        out.append("  Confirmed unreferenced: likely dead code or an unwired feature")
        out.append("  (or reached only dynamically / via reflection).")
    return '\n'.join(out)
