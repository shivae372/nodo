"""
Optional tree-sitter backend — accurate import/symbol extraction via real parse
trees. Enabled with `--ast`.

Nodo's promise is clone-and-run with zero dependencies, so this is the ONLY
module that touches a third-party library, it is imported lazily, and EVERY
failure path returns None so the caller silently falls back to the regex
extractor. Installing tree-sitter only *upgrades* accuracy; its absence never
breaks a run.

    pip install tree-sitter tree-sitter-language-pack        # then run with --ast

Why it matters: a parse tree catches imports the regex can miss (multi-line,
unusual formatting, re-export edge cases) and distinguishes a real `require(...)`
from an identically-named function call — so more edges resolve and fewer files
look like false orphans.
"""
import os
import re

_PARSERS = {}          # ext -> parser | None (cache)
_CHECKED = False
_AVAILABLE = False
_GET_PARSER = None

# ext -> tree-sitter language name (as known to tree_sitter_language_pack)
_LANG_NAME = {
    '.py': 'python',
    '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.ts': 'typescript', '.tsx': 'tsx', '.mts': 'typescript', '.cts': 'typescript',
    '.go': 'go', '.rs': 'rust', '.java': 'java', '.rb': 'ruby', '.php': 'php',
    '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.hpp': 'cpp', '.cc': 'cpp', '.cxx': 'cpp',
    '.cs': 'csharp', '.kt': 'kotlin', '.kts': 'kotlin', '.swift': 'swift',
    '.scala': 'scala', '.sc': 'scala', '.dart': 'dart', '.lua': 'lua',
    '.sol': 'solidity', '.sh': 'bash', '.bash': 'bash',
}

_PY = {'.py'}
_JS = {'.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts'}

# Extension → grammar names taught via lessons (e.g. {'.zig': 'zig'}). Lets a
# lesson light up real AST extraction for any of the language-pack's grammars
# that nodo didn't map out of the box — no per-language code needed.
_LESSON_LANG = {}


def set_lesson_grammars(mapping):
    """Register ext→grammar names from lessons; busts the affected parser cache."""
    global _LESSON_LANG
    _LESSON_LANG = {k: v for k, v in (mapping or {}).items() if isinstance(v, str) and v}
    for e in list(_PARSERS):
        if e in _LESSON_LANG:
            _PARSERS.pop(e, None)


def _grammar_name(ext):
    return _LESSON_LANG.get(ext) or _LANG_NAME.get(ext)


def available():
    """True if a tree-sitter parser backend is importable. Never raises."""
    global _CHECKED, _AVAILABLE, _GET_PARSER
    if _CHECKED:
        return _AVAILABLE
    _CHECKED = True
    try:
        from tree_sitter_language_pack import get_parser
        _GET_PARSER = get_parser
        _AVAILABLE = True
    except Exception:
        try:
            from tree_sitter_languages import get_parser  # older fallback
            _GET_PARSER = get_parser
            _AVAILABLE = True
        except Exception:
            _AVAILABLE = False
    return _AVAILABLE


def _get_parser(ext):
    if ext in _PARSERS:
        return _PARSERS[ext]
    parser = None
    name = _grammar_name(ext)
    if name and available():
        try:
            parser = _GET_PARSER(name)
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
        src = text.encode('utf-8')
        tree = parser.parse(src)

        def txt(node):
            return src[node.start_byte:node.end_byte].decode('utf-8', 'ignore')

        def field(node, name):
            try:
                return node.child_by_field_name(name)
            except Exception:
                return None

        out = []
        if ext in _PY:
            for n in _walk(tree.root_node):
                if n.type == 'import_from_statement':
                    m = field(n, 'module_name')
                    if m is None:
                        for c in n.children:
                            if c.type in ('relative_import', 'dotted_name'):
                                m = c
                                break
                    if m is not None:
                        out.append(txt(m))
                elif n.type == 'import_statement':
                    for c in n.children:
                        if c.type == 'dotted_name':
                            out.append(txt(c))
                        elif c.type == 'aliased_import':
                            nm = field(c, 'name')
                            if nm is not None:
                                out.append(txt(nm))
        elif ext in _JS:
            for n in _walk(tree.root_node):
                if n.type in ('import_statement', 'export_statement'):
                    s = field(n, 'source')
                    if s is None:
                        for c in n.children:
                            if c.type == 'string':
                                s = c
                                break
                    if s is not None:
                        out.append(txt(s).strip('\'"`'))
                elif n.type == 'call_expression':
                    fn = field(n, 'function')
                    if fn is not None and txt(fn) in ('require', 'import'):
                        args = field(n, 'arguments')
                        if args is not None:
                            for c in _walk(args):
                                if c.type in ('string', 'string_fragment'):
                                    out.append(txt(c).strip('\'"`'))
                                    break
        else:
            lang = _grammar_name(ext)
            if lang in ('c', 'cpp'):
                for n in _walk(tree.root_node):       # local #include "x.h" (system <...> skipped → resolves to files)
                    if n.type == 'preproc_include':
                        for c in n.children:
                            if c.type == 'string_literal':
                                out.append(txt(c).strip('"').strip("'"))
            elif lang == 'go':
                for n in _walk(tree.root_node):        # import "pkg/path" (package-level)
                    if n.type == 'import_spec':
                        for c in n.children:
                            if 'string' in c.type:
                                out.append(txt(c).strip('"').strip('`'))
            elif lang == 'java':
                for n in _walk(tree.root_node):        # import com.x.Y (package-level)
                    if n.type == 'import_declaration':
                        for c in n.children:
                            if c.type in ('scoped_identifier', 'identifier'):
                                out.append(txt(c))
                                break
            else:
                return None  # no AST import handler → use regex fallback

        seen, uniq = set(), []
        for s in out:
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq
    except Exception:
        return None


# definition node types whose `name` field is the symbol
_DEF_TYPES = {
    'function_definition', 'class_definition',                      # python
    'function_declaration', 'generator_function_declaration',
    'class_declaration', 'abstract_class_declaration',
    'interface_declaration', 'type_alias_declaration', 'enum_declaration',
    'method_definition',
}
_FUNCY = {'arrow_function', 'function', 'function_expression',
          'class', 'class_expression', 'generator_function'}

# Definition node types for the OTHER languages (Go/Rust/Java/C/C++/Ruby/PHP/C#),
# unioned with _DEF_TYPES. Node-type names are language-unique enough that a single
# union works; each is named via its `name` field (or, for C/C++, the declarator).
_EXTRA_DEF_TYPES = {
    'method_declaration', 'type_spec', 'record_declaration',           # go / java / c#
    'function_item', 'struct_item', 'enum_item', 'trait_item',         # rust
    'mod_item', 'const_item',
    'class_specifier', 'struct_specifier', 'struct_declaration',       # c++ / c#
    'method', 'singleton_method', 'module', 'trait_declaration',       # ruby / php
    'class_definition', 'object_definition', 'function_signature',     # scala / dart
}
_ALL_DEF_TYPES = _DEF_TYPES | _EXTRA_DEF_TYPES

# Grammar-agnostic definition detection. tree-sitter grammars name definition
# nodes by a strong convention: a def keyword (function/class/struct/trait/…)
# plus a structural suffix (_declaration/_definition/_item/_specifier/_signature).
# Matching that pattern — instead of hand-listing node types per language — gives
# uniform symbol extraction across 40+ tree-sitter languages. The name-resolver
# (`_name_of`) then filters out anything without a real name, and the suffix gate
# excludes containers/calls (function_body, method_invocation, function_type, …).
_DEF_KW = re.compile(
    r'(?:^|_)(func|function|fn|method|constructor|destructor|class|struct|union|'
    r'enum|interface|trait|impl|protocol|module|namespace|object|record|macro|'
    r'subroutine|procedure|signature|contract)(?:$|_)')
_DEF_SUFFIX = ('_declaration', '_definition', '_specifier', '_item', '_spec', '_signature')


def _is_def_type(t):
    if t in _ALL_DEF_TYPES:
        return True
    return bool(_DEF_KW.search(t)) and t.endswith(_DEF_SUFFIX)


def _name_of(node, txt, field):
    """Definition name: the `name` field when present, else (C/C++) the function's
    own identifier by following the declarator chain (NOT a parameter). None if absent."""
    nm = field(node, 'name')
    if nm is not None:
        return txt(nm)
    if node.type == 'function_definition':          # C / C++
        decl = field(node, 'declarator')
        # unwrap pointer/reference/parenthesized declarators
        for _ in range(6):
            if decl is None or decl.type == 'function_declarator':
                break
            inner = field(decl, 'declarator')
            if inner is None:
                break
            decl = inner
        if decl is not None and decl.type == 'function_declarator':
            name_node = field(decl, 'declarator')
            if name_node is not None:
                return txt(name_node)
        return None
    # generic fallback (e.g. Kotlin): first identifier-like DIRECT child — direct,
    # not deep, so we get the definition's own name and never a parameter.
    for c in node.children:
        if c.type in ('identifier', 'simple_identifier', 'type_identifier',
                      'constant', 'name'):
            return txt(c)
    return None


def extract_defs_ast(rel, text):
    """Return [(name, line)] for definitions via tree-sitter, or None to fall back.
    More accurate than regex: real names, no matches inside strings/comments, and
    only function/class-valued consts (not every local literal)."""
    ext = os.path.splitext(rel)[1].lower()
    parser = _get_parser(ext)
    if parser is None:
        return None
    try:
        src = text.encode('utf-8')
        tree = parser.parse(src)

        def txt(node):
            return src[node.start_byte:node.end_byte].decode('utf-8', 'ignore')

        def field(node, name):
            try:
                return node.child_by_field_name(name)
            except Exception:
                return None

        out = []
        for n in _walk(tree.root_node):
            t = n.type
            if _is_def_type(t):
                name = _name_of(n, txt, field)
                if name:
                    out.append((name, n.start_point[0] + 1))
            elif t == 'variable_declarator':
                val = field(n, 'value')
                if val is not None and val.type in _FUNCY:
                    nm = field(n, 'name')
                    if nm is not None and nm.type == 'identifier':
                        out.append((txt(nm), n.start_point[0] + 1))
        return out
    except Exception:
        return None


def extract_calls_ast(rel, text):
    """Return {name: [arg_count, ...]} for calls to a bare identifier (JS/TS only),
    via tree-sitter, or None. Member calls (obj.f()), `new F()`, and calls using
    spread (f(...args)) are excluded — counting those against a definition would be
    unsound. Accurate counting here is what makes the arg-mismatch check reliable
    (regex miscounts args inside template literals / nested literals)."""
    ext = os.path.splitext(rel)[1].lower()
    if ext not in _JS:
        return None
    parser = _get_parser(ext)
    if parser is None:
        return None
    try:
        src = text.encode('utf-8')
        tree = parser.parse(src)

        def txt(node):
            return src[node.start_byte:node.end_byte].decode('utf-8', 'ignore')

        def field(node, name):
            try:
                return node.child_by_field_name(name)
            except Exception:
                return None

        from collections import defaultdict
        calls = defaultdict(list)
        for n in _walk(tree.root_node):
            if n.type != 'call_expression':
                continue
            fn = field(n, 'function')
            if fn is None or fn.type != 'identifier':
                continue                                  # skip obj.f(), computed callees
            args = field(n, 'arguments')
            if args is None:
                continue
            cnt, spread = 0, False
            for c in args.children:
                if not c.is_named or c.type == 'comment':
                    continue                       # skip punctuation and comments
                if c.type == 'spread_element':
                    spread = True
                    break
                cnt += 1
            if not spread:
                calls[txt(fn)].append(cnt)
        return dict(calls)
    except Exception:
        return None


_SIG_TYPES = {'function_declaration', 'generator_function_declaration',
              'function_definition', 'method_definition'}


def extract_signatures_ast(rel, text):
    """Return {name: {params:int, variadic:bool, line:int}} for functions, via
    tree-sitter, or None to fall back. `params` is the count of parameter slots
    (excluding rest/`*args`/`**kwargs`); `variadic` is True when a rest parameter
    is present (so callers can pass any number). This is what makes an arg-count
    contract check SAFE — regex can't count TS params, a parse tree can."""
    ext = os.path.splitext(rel)[1].lower()
    parser = _get_parser(ext)
    if parser is None:
        return None
    is_py = ext in _PY
    try:
        src = text.encode('utf-8')
        tree = parser.parse(src)

        def txt(node):
            return src[node.start_byte:node.end_byte].decode('utf-8', 'ignore')

        def field(node, name):
            try:
                return node.child_by_field_name(name)
            except Exception:
                return None

        def count_params(params_node):
            if params_node is None:
                return 0, False
            # single unparenthesized arrow parameter: `x => ...`
            if not is_py and params_node.type in ('identifier', 'shorthand_property_identifier'):
                return 1, False
            count, variadic = 0, False
            for c in params_node.children:
                t = c.type
                ctext = txt(c).strip()
                if is_py:
                    if t in ('list_splat_pattern', 'dictionary_splat_pattern') or ctext.startswith('*'):
                        variadic = True
                        continue
                    if t in ('typed_default_parameter', 'typed_parameter',
                             'identifier', 'default_parameter'):
                        nm = ctext.split(':')[0].split('=')[0].strip()
                        if nm in ('self', 'cls') or not nm:
                            continue
                        count += 1
                else:
                    if t in ('rest_pattern', 'rest_parameter') or ctext.startswith('...'):
                        variadic = True
                        continue
                    if t in ('required_parameter', 'optional_parameter', 'identifier',
                             'assignment_pattern', 'object_pattern', 'array_pattern'):
                        count += 1
            return count, variadic

        sigs = {}
        for n in _walk(tree.root_node):
            t = n.type
            name = params = None
            if t in _SIG_TYPES:
                nm = field(n, 'name')
                name = txt(nm) if nm is not None else None
                params = field(n, 'parameters')
            elif t == 'variable_declarator':
                val = field(n, 'value')
                if val is not None and val.type in _FUNCY:
                    nm = field(n, 'name')
                    if nm is not None and nm.type == 'identifier':
                        name = txt(nm)
                        params = field(val, 'parameters') or field(val, 'parameter')
            if name:
                cnt, variadic = count_params(params)
                sigs[name] = {'params': cnt, 'variadic': variadic, 'line': n.start_point[0] + 1}
        return sigs
    except Exception:
        return None
