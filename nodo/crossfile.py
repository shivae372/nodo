"""
Cross-file detectors — the bugs an LLM editing one file in isolation cannot see.

A frontier model reading a single file will out-reason any heuristic on *local*
logic. But it is blind to whole-repo structure it doesn't have in context:

  - broken_contracts():  an exported symbol's call sites in OTHER files don't
                         match how it's now defined (arg-count mismatch), or a
                         named import points at a symbol the module no longer
                         exports. The classic "AI edited file A, broke files B/C".
  - missing_guard():     a guard/check applied to N sibling files but absent on a
                         few — the dangerous outlier (e.g. auth on 9/10 routes).
  - cycles():            import cycles (subtle init / runtime-order bugs).
  - orphans():           exported symbols nothing imports (dead surface area).
  - duplication_drift(): near-identical blocks across files that have diverged
                         (one copy fixed, others stale).

All pure-stdlib, deterministic. Heuristic (regex-level), so findings are flagged
as warn/info, not error — they point the agent at a spot to verify, cheaply.
"""
import re
from collections import defaultdict, Counter


def _is_js(rel):
    return rel.lower().endswith(('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'))


def _is_py(rel):
    return rel.lower().endswith('.py')


_TEST_RE = re.compile(
    r'(^|/)(tests?|specs?|__tests?__|e2e|__mocks__)/'  # test/ tests/ spec/ … even top-level
    r'|(\.|_)(test|spec)\.'                            # foo.test.js  foo_spec.rb
    r'|(^|/)test_[^/]*\.py$'                           # python test_foo.py
    r'|(^|/)conftest\.py$',                            # pytest conftest
    re.I)


def _is_test(rel):
    return bool(_TEST_RE.search(rel))


def _strip_comments(text):
    """Best-effort comment removal so keyword detectors don't match a guard/term
    that only appears in a comment (e.g. a `// TODO: add requireAuth` masking the
    fact the guard is actually missing). Conservative and language-agnostic."""
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)   # /* block */
    out = []
    for line in text.split('\n'):
        line = re.sub(r'//.*$', '', line)                # // line comment
        line = re.sub(r'(^|\s)#.*$', r'\1', line)        # # line comment (py/rb/sh)
        out.append(line)
    return '\n'.join(out)


def _split_top_level(s):
    """Split a string on top-level commas only (ignore commas inside (), [], {}, <>)."""
    parts, depth, cur = [], 0, ''
    for ch in s:
        if ch in '([{<':
            depth += 1
        elif ch in ')]}>':
            depth = max(0, depth - 1)
        if ch == ',' and depth == 0:
            parts.append(cur); cur = ''
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return [p for p in parts if p.strip()]


def _balanced_params(text, open_idx):
    """Given index of a '(', return the substring inside the matched ')' (handles
    nested parens and multi-line). Returns None if unbalanced."""
    depth = 0
    out = []
    for ch in text[open_idx:]:
        if ch == '(':
            depth += 1
            if depth == 1:
                continue
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return ''.join(out)
        if depth >= 1:
            out.append(ch)
    return None


# ── Symbol definitions + exports ──────────────────────────────────────────────
def _extract_defs(rel, text):
    """Return {name: {'params': int|None, 'line': n}} for top-level exported defs.

    Handles multi-line signatures and `export { a, b }` re-export blocks. params
    counts top-level commas only (so an object/array param counts as 1)."""
    defs = {}
    if _is_js(rel):
        # export [async] function NAME[<generics>]( ... )  — multi-line safe
        for m in re.finditer(r'export\s+(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(', text):
            inner = _balanced_params(text, text.index('(', m.end() - 1))
            if inner is not None:
                line = text[:m.start()].count('\n') + 1
                params = _split_top_level(inner)
                variadic = any('...' in p or '?' in p or '=' in p for p in params)
                defs[m.group(1)] = {'params': len(params), 'line': line, 'variadic': variadic}
        # export type NAME / export interface NAME / export enum NAME
        for m in re.finditer(r'export\s+(?:type|interface|enum)\s+(\w+)', text):
            defs.setdefault(m.group(1), {'params': None, 'line': text[:m.start()].count('\n') + 1})
        # export const NAME = [async] ( ... ) =>   — multi-line safe
        for m in re.finditer(r'export\s+(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(', text):
            inner = _balanced_params(text, m.end() - 1)
            if inner is not None and '=>' in text[m.end():m.end() + len(inner) + 8]:
                line = text[:m.start()].count('\n') + 1
                defs.setdefault(m.group(1), {'params': len(_split_top_level(inner)), 'line': line})
        # plain exported const / class
        for m in re.finditer(r'export\s+(?:const|let|var|class)\s+(\w+)', text):
            line = text[:m.start()].count('\n') + 1
            defs.setdefault(m.group(1), {'params': None, 'line': line})
        # default exports: export default function/class NAME
        for m in re.finditer(r'export\s+default\s+(?:async\s+)?(?:function|class)\s+(\w+)', text):
            defs.setdefault(m.group(1), {'params': None, 'line': text[:m.start()].count('\n') + 1})
        # re-export: export { a, b as c }
        for m in re.finditer(r'export\s*\{([^}]*)\}', text):
            for raw in m.group(1).split(','):
                nm = raw.split(' as ')[-1].strip()
                if nm:
                    defs.setdefault(nm, {'params': None, 'line': text[:m.start()].count('\n') + 1})
    elif _is_py(rel):
        for m in re.finditer(r'^def\s+(\w+)\s*\(', text, re.M):
            inner = _balanced_params(text, m.end() - 1)
            if inner is not None:
                params = [p for p in _split_top_level(inner)
                          if p.strip() not in ('self', 'cls')]
                variadic = any('*' in p or '=' in p for p in params)
                line = text[:m.start()].count('\n') + 1
                defs[m.group(1)] = {'params': len(params), 'line': line, 'variadic': variadic}
    return defs


def _extract_named_imports(rel, text):
    """Return [(name, source, line, is_type)] for named imports from project paths.

    Handles multi-line `import { a,\n b } from '...'` blocks (common in formatted
    code) and marks TypeScript type-only imports."""
    out = []
    if _is_js(rel):
        # DOTALL across the braces so multi-line import blocks are captured
        for m in re.finditer(
                r'import\s+(type\s+)?\{(.*?)\}\s*from\s*[\'"]([^\'"]+)[\'"]',
                text, re.DOTALL):
            source = m.group(3)
            if not (source.startswith('.') or source.startswith('@/') or source.startswith('~')):
                continue
            is_type_import = bool(m.group(1))
            line = text[:m.start()].count('\n') + 1
            for raw in m.group(2).split(','):
                is_type = is_type_import or raw.strip().startswith('type ')
                nm = raw.replace('type ', '').split(' as ')[0].strip()
                if nm:
                    out.append((nm, source, line, is_type))
        # re-export drift: `export { a, b as c } from './y'` re-exports a/b from y,
        # so y MUST still export them — a removal breaks every downstream importer
        # at build time. Treat the re-exported names as named imports of y (the
        # `export *` barrel guard in broken_contracts still suppresses wildcards).
        for m in re.finditer(
                r'export\s+(type\s+)?\{(.*?)\}\s*from\s*[\'"]([^\'"]+)[\'"]',
                text, re.DOTALL):
            source = m.group(3)
            if not (source.startswith('.') or source.startswith('@/') or source.startswith('~')):
                continue
            is_type_re = bool(m.group(1))
            line = text[:m.start()].count('\n') + 1
            for raw in m.group(2).split(','):
                is_type = is_type_re or raw.strip().startswith('type ')
                nm = raw.replace('type ', '').split(' as ')[0].strip()
                if nm and nm != '*':
                    out.append((nm, source, line, is_type))
    elif _is_py(rel):
        for i, ln in enumerate(text.split('\n')):
            m = re.match(r'from\s+([.\w]+)\s+import\s+(.+)', ln)
            if m and (m.group(1).startswith('.') or '.' in m.group(1)):
                for raw in m.group(2).split(','):
                    nm = raw.split(' as ')[0].strip().strip('()')
                    if nm and nm != '*':
                        out.append((nm, m.group(1), i + 1, False))
    return out


def _call_arg_count(text, name):
    """Arg counts seen at call sites of `name(...)`, counting top-level commas only
    (so a single object/array argument counts as 1, not its inner commas)."""
    counts = []
    for m in re.finditer(rf'\b{re.escape(name)}\s*\(', text):
        # skip definitions (preceded by function/def), method calls (obj.name()),
        # and constructor calls (new Name()) — none are a plain call to the import.
        pre = text[max(0, m.start() - 12):m.start()]
        if re.search(r'(function|def)\s*$', pre):
            continue
        if pre.rstrip().endswith('.') or re.search(r'\bnew\s+$', pre):
            continue
        inner = _balanced_params(text, m.end() - 1)
        if inner is None:
            continue
        inner = inner.strip()
        counts.append(0 if not inner else len(_split_top_level(inner)))
    return counts


def _mk(sev, cat, typ, rel, detail, line=''):
    return {'severity': sev, 'category': cat, 'type': typ, 'node': rel.split('/')[-1],
            'file': rel, 'detail': detail, 'line': str(line) if line else '', 'snippet': []}


# ── 1. Broken contracts ───────────────────────────────────────────────────────
def broken_contracts(nodes, edges, file_texts, defs_by_file, id_to_rel):
    """Imports of a symbol that the source no longer exports (rename/removal)."""
    issues = []
    # map a rough module key (path without ext) -> rel for resolution
    rel_by_noext = {}
    for n in nodes:
        rel_by_noext[re.sub(r'\.[^./]+$', '', n['rel'])] = n['rel']

    edge_targets = defaultdict(set)
    for e in edges:
        edge_targets[id_to_rel[e['source']]].add(id_to_rel[e['target']])

    for n in nodes:
        rel = n['rel']
        text = file_texts.get(rel, '')
        if not text:
            continue
        imports = _extract_named_imports(rel, text)
        targets = edge_targets.get(rel, set())
        for name, source, line, is_type in imports:
            if is_type:
                continue  # TS types are erased; lower risk, harder to parse safely
            src_stem = re.sub(r'\.[^./]+$', '', source.split('/')[-1])
            # resolve to targets whose file STEM exactly equals the import's last
            # segment (so '@/lib/google' matches lib/google.ts, NOT
            # lib/googleConnectionTokens.ts). Require a unique, parsed match.
            matches = [t for t in targets
                       if re.sub(r'\.[^./]+$', '', t.rsplit('/', 1)[-1]) == src_stem
                       and t in defs_by_file]
            if len(matches) != 1:
                continue  # ambiguous or unresolved → don't risk a false positive
            tgt = matches[0]
            tdefs = defs_by_file[tgt]
            tgt_text = file_texts.get(tgt, '')
            # CONFIDENCE GUARD: if the target re-exports a wildcard (`export * from`)
            # or uses CommonJS dynamic exports, the symbol may legitimately come
            # through transitively — we cannot prove it's missing, so stay silent.
            # (This is the exact false-positive class from the audit: a barrel that
            #  does `export * from './x'` looked like it was missing the symbol.)
            if (re.search(r'export\s*\*', tgt_text)
                    or 'module.exports' in tgt_text
                    or re.search(r'\bexports\.', tgt_text)):
                continue
            if name not in tdefs and name[0].islower():
                issues.append(_mk(
                    'warn', 'Contract', 'Imported symbol not exported by source',
                    rel,
                    f"`{name}` is imported from `{source}` but `{tgt}` does not "
                    f"appear to export it (renamed or removed?). This breaks at "
                    f"build/runtime and an AI editing only one file would miss it.",
                    line))
    return issues


def arg_mismatches(nodes, file_texts, defs_by_file, id_to_rel, edges):
    """Call sites whose arg count exceeds the definition's param count."""
    issues = []
    edge_targets = defaultdict(set)
    for e in edges:
        edge_targets[id_to_rel[e['source']]].add(id_to_rel[e['target']])

    for n in nodes:
        rel = n['rel']
        text = file_texts.get(rel, '')
        if not text:
            continue
        for tgt in edge_targets.get(rel, set()):
            for name, meta in defs_by_file.get(tgt, {}).items():
                if meta['params'] is None or meta['params'] == 0:
                    continue
                if meta.get('variadic'):
                    continue  # rest/optional/default params accept variable counts
                # require the symbol to be explicitly named-imported here (high
                # confidence it's the same function, not a coincidental name)
                if not any(nm == name for nm, _s, _l, _t in _extract_named_imports(rel, text)):
                    continue
                for cnt in _call_arg_count(text, name):
                    if cnt > meta['params']:
                        issues.append(_mk(
                            'warn', 'Contract', 'Call passes more args than defined',
                            rel,
                            f"`{name}()` is called with {cnt} arg(s) here but is defined "
                            f"with {meta['params']} in `{tgt}`. Cross-file signature drift "
                            f"an LLM editing one side won't catch.",
                            ''))
                        break
    return issues


# ── 2. Missing-guard outlier ──────────────────────────────────────────────────
# (pattern_label, regex) — "sibling" files (same parent dir) that mostly contain
# the pattern but a few don't are flagged.
GUARD_PATTERNS = [
    ('auth/permission guard', re.compile(
        r'\b(auth|getUser|getSession|requireAuth|authorize|adminGuard|isAdmin|'
        r'verifyToken|currentUser|ensureAuth)\b', re.I)),
    ('input validation', re.compile(
        r'\b(validate|schema\.parse|zod|joi|sanitize|assert\w*|checkArgs)\b', re.I)),
    ('error handling', re.compile(r'\b(try\s*\{|try:|catch\s*\(|except\b)')),
]


def missing_guard(nodes, file_texts, min_siblings=4, min_ratio=0.7):
    """Within a directory of similar files, flag the ones missing a guard the
    majority share."""
    issues = []
    by_dir = defaultdict(list)
    for n in nodes:
        rel = n['rel']
        if n['category'] in ('api', 'page') or '/api/' in rel or '/routes/' in rel:
            by_dir[rel.rsplit('/', 1)[0]].append(n)
    # also group api routes more broadly: all files under any .../api/**
    api_group = [n for n in nodes if '/api/' in n['rel'] or n['category'] == 'api']

    def check_group(group, scope):
        if len(group) < min_siblings:
            return
        # match guards in CODE only — a guard named in a comment doesn't count
        stripped = {n['rel']: _strip_comments(file_texts.get(n['rel'], '')) for n in group}
        for label, rx in GUARD_PATTERNS:
            have = []
            lack = []
            for n in group:
                (have if rx.search(stripped[n['rel']]) else lack).append(n)
            if not have:
                continue
            ratio = len(have) / len(group)
            # majority have it, a small minority don't → the minority is suspicious
            if ratio >= min_ratio and 0 < len(lack) <= max(2, len(group) // 5):
                for n in lack:
                    issues.append(_mk(
                        'warn', 'Consistency', f'Missing {label} (outlier)',
                        n['rel'],
                        f"{len(have)}/{len(group)} files in {scope} use {label}, but this "
                        f"one doesn't. If it handles the same kind of request, this is "
                        f"likely a missing check — the exact outlier an LLM won't spot "
                        f"without reading all {len(group)} files.",
                        ''))

    seen = set()
    for d, group in by_dir.items():
        check_group(group, f'`{d}/`')
        seen.update(n['rel'] for n in group)
    return issues


# ── 3. Import cycles ──────────────────────────────────────────────────────────
def cycles(nodes, edges, id_to_rel, file_texts, max_report=12):
    """Detect directed import cycles via DFS; report the smallest representative
    cycles. Type-only import edges are excluded — they're erased at compile time
    and don't cause runtime cycles."""
    # build a set of (importer_rel, source_stem) that are type-only, to filter edges
    type_only = set()
    for n in nodes:
        for nm, src, _l, is_type in _extract_named_imports(n['rel'], file_texts.get(n['rel'], '')):
            if is_type:
                type_only.add((n['rel'], re.sub(r'\.[^./]+$', '', src.split('/')[-1])))

    def is_type_edge(s, t):
        s_rel, t_stem = id_to_rel[s], re.sub(r'\.[^./]+$', '', id_to_rel[t].rsplit('/', 1)[-1])
        return (s_rel, t_stem) in type_only

    adj = defaultdict(list)
    for e in edges:
        if is_type_edge(e['source'], e['target']):
            continue
        adj[e['source']].append(e['target'])

    issues = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = defaultdict(int)
    stack = []
    found = []

    def dfs(u):
        color[u] = GRAY
        stack.append(u)
        for v in adj.get(u, []):
            if color[v] == GRAY:
                # cycle: from v's position in stack to top
                if v in stack:
                    idx = stack.index(v)
                    cyc = stack[idx:] + [v]
                    found.append(list(cyc))
            elif color[v] == WHITE:
                dfs(v)
        stack.pop()
        color[u] = BLACK

    for n in nodes:
        if color[n['id']] == WHITE:
            dfs(n['id'])

    # dedupe cycles by their node-set, prefer shorter
    seen = set()
    uniq = []
    for cyc in sorted(found, key=len):
        key = frozenset(cyc)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(cyc)

    for cyc in uniq[:max_report]:
        rels = [id_to_rel[c] for c in cyc]
        chain = ' -> '.join(r.split('/')[-1] for r in rels)
        issues.append(_mk(
            'warn', 'Architecture', 'Import cycle',
            rels[0],
            f"Circular import: {chain}. Cycles cause fragile init order and subtle "
            f"runtime bugs; break one edge. Full path: {' -> '.join(rels)}",
            ''))
    return issues


# ── 4. Orphaned exports ───────────────────────────────────────────────────────
def orphans(nodes, edges, file_texts, defs_by_file, id_to_rel):
    """Exported symbols that nothing in the project imports by name."""
    issues = []
    # collect every named import across the repo
    imported_names = Counter()
    for n in nodes:
        for name, _src, _line, _t in _extract_named_imports(n['rel'], file_texts.get(n['rel'], '')):
            imported_names[name] += 1

    # a file that IS imported somewhere (has inbound edges) but exports symbols
    # never imported by name → those exports may be dead surface
    inbound = defaultdict(int)
    for e in edges:
        inbound[id_to_rel[e['target']]] += 1

    entry_pat = re.compile(r'(index|main|app|__init__|route|page|layout)\.', re.I)
    for n in nodes:
        rel = n['rel']
        if entry_pat.search(n['label']):
            continue
        # Only flag files that ARE reached (inbound > 0) but expose dead exports.
        # Files nothing imports at all are handled by orphaned_but_substantial,
        # so we don't double-report them here.
        if inbound.get(rel, 0) == 0:
            continue
        d = defs_by_file.get(rel, {})
        if not d:
            continue
        dead = [name for name in d
                if imported_names.get(name, 0) == 0 and name[0].islower() and len(name) > 2]
        # only flag if the file has several exports and most are unused
        if len(d) >= 2 and len(dead) >= 2 and len(dead) == len(d):
            issues.append(_mk(
                'info', 'Dead Code', 'Exported symbols never imported',
                rel,
                f"Exports {', '.join('`'+x+'`' for x in dead[:5])}"
                + (f" (+{len(dead)-5})" if len(dead) > 5 else '')
                + " but nothing imports them by name. Likely dead surface area "
                  "(or used dynamically / as an entrypoint).",
                ''))
    return issues


# ── 5. Duplication drift ──────────────────────────────────────────────────────
def duplication_drift(nodes, file_texts, block=6, min_files=2):
    """Find sizable identical line-blocks repeated across files (copy-paste).

    Flags the cluster so a reviewer can check whether the copies have diverged.
    Conservative: only blocks of >= `block` non-trivial lines.
    """
    issues = []
    # hash normalized windows of N lines -> list of (rel, start_line)
    window_map = defaultdict(list)
    for n in nodes:
        rel = n['rel']
        text = file_texts.get(rel, '')
        if not text:
            continue
        raw = text.split('\n')
        # Normalize whitespace AND literals so blocks that differ only in their
        # string/number values still register as duplicates (drift catches the
        # copy-paste-with-tweaked-constants case, not just byte-identical blocks).
        norm = []
        for l in raw:
            s = re.sub(r'\s+', ' ', l).strip()
            s = re.sub(r'(["\'`]).*?\1', '"S"', s)     # string literals → "S"
            s = re.sub(r'\b\d+(?:\.\d+)?\b', 'N', s)   # numbers → N
            norm.append(s)
        for i in range(len(norm) - block + 1):
            win = norm[i:i + block]
            # skip windows that are mostly trivial (blank/braces/imports)
            meaningful = [w for w in win if len(w) > 8 and not w.startswith(('import', 'from', '//', '*', '#'))]
            if len(meaningful) < block - 1:
                continue
            key = '\n'.join(win)
            window_map[key].append((rel, i + 1))

    reported = set()
    for key, hits in window_map.items():
        files = {h[0] for h in hits}
        if len(files) >= min_files:
            sig = frozenset(files)
            if sig in reported:
                continue
            reported.add(sig)
            flist = sorted(files)
            issues.append(_mk(
                'info', 'Duplication', 'Duplicated block across files',
                flist[0],
                f"A {block}-line block is duplicated across {len(files)} files: "
                f"{', '.join('`'+f+'`' for f in flist[:4])}"
                + (f" (+{len(flist)-4})" if len(flist) > 4 else '')
                + ". If one copy gets fixed and the others don't, they silently drift. "
                  "Consider extracting a shared function.",
                ''))
    return issues[:15]  # cap noise


def _arg_mismatch_ast(scope, file_texts, defs_by_file, id_to_rel, edges, ast_index):
    """AST-backed: a call passes MORE positional args than the imported function
    declares (and the function has no rest param). Both the signature and the
    call's arg count come from tree-sitter, so template literals / nested literals
    can't cause a miscount. Conservative: only named-imported, non-variadic targets;
    spread calls and member/constructor calls are excluded by the AST extractors."""
    issues = []
    edge_targets = defaultdict(set)
    for e in edges:
        s, t = id_to_rel.get(e['source']), id_to_rel.get(e['target'])
        if s and t:
            edge_targets[s].add(t)
    for n in scope:
        rel = n['rel']
        if not _is_js(rel):
            continue
        text = file_texts.get(rel, '')
        if not text:
            continue
        calls = ast_index.extract_calls_ast(rel, text)
        if not calls:
            continue
        named = {nm for nm, _s, _l, _t in _extract_named_imports(rel, text)}
        for name, counts in calls.items():
            if name not in named:
                continue                       # only symbols explicitly imported here
            sig = tgt_rel = None
            for tgt in edge_targets.get(rel, set()):
                meta = defs_by_file.get(tgt, {}).get(name)
                if meta and meta.get('params') is not None:
                    sig, tgt_rel = meta, tgt
                    break
            if not sig or sig.get('variadic') or sig['params'] == 0:
                continue
            for cnt in counts:
                if cnt > sig['params']:
                    issues.append(_mk(
                        'warn', 'Contract', 'Call passes more args than defined', rel,
                        f"`{name}()` is called with {cnt} arg(s) here but is defined with "
                        f"{sig['params']} in `{tgt_rel}`. Cross-file signature drift an AI "
                        f"editing one side won't catch.", ''))
                    break
    return issues


# ── 6. Orphaned-but-substantial: the "broken feature" smell ───────────────────
_ENTRYISH = re.compile(r'(index|main|app|__init__|setup|conftest|server|cli|'
                       r'page|layout|route|bootstrap|entry)\.', re.I)


def orphaned_but_substantial(nodes, edges, file_texts, defs_by_file, soft_refs,
                             max_report=8, max_ratio=0.20):
    """A file with real surface area (many exports / lots of code) that NOTHING
    imports — "implemented but disconnected". The broken-feature smell an AI agent
    introduces when it builds a feature and never wires it in. Graphify can't see
    this; it's a pure structural signal.

    Reliability gates (so it can NEVER flood — a flood would destroy trust):
      - tests and entry-point files are excluded from the eligible pool;
      - if "disconnected" files are a large fraction of the pool (resolver gaps,
        or a plugin/dynamic-import architecture), the signal is unreliable, so we
        stay SILENT rather than emit dozens of dubious warnings;
      - otherwise we report only the most substantial, capped at `max_report`."""
    inbound = defaultdict(int)
    id_to_rel = {n['id']: n['rel'] for n in nodes}
    for e in edges:
        if e['target'] in id_to_rel:           # target is an in-scope file
            inbound[id_to_rel[e['target']]] += 1

    eligible = 0
    cands = []
    for n in nodes:
        rel = n['rel']
        if _ENTRYISH.search(n['label']) or _is_test(rel):
            continue
        eligible += 1
        base = re.sub(r'\.[^./]+$', '', n['label']).lower()
        if base in soft_refs or inbound.get(rel, 0) > 0:
            continue                            # referenced anywhere → connected
        ndefs = len(defs_by_file.get(rel, {}))
        loc = n.get('loc', 0)
        if ndefs >= 3 or loc >= 40:
            cands.append((n, ndefs, loc))

    if not cands:
        return []
    # Flood protection — silence beats false positives — WITHOUT penalising small
    # projects where one orphan is naturally a high fraction of the files:
    ratio = (len(cands) / eligible) if eligible else 0.0
    if len(cands) > max_report and ratio > max_ratio:
        return []                       # many candidates AND a large share → flood
    if eligible >= 8 and ratio > 0.5:
        return []                       # most of a non-trivial repo "disconnected"
                                        # → resolver gap / plugin arch, untrustworthy

    cands.sort(key=lambda c: (c[1], c[2]), reverse=True)  # most surface area first
    issues = []
    for n, ndefs, loc in cands[:max_report]:
        why = (f'{ndefs} exported symbol(s)' if ndefs >= 3 else f'{loc} lines of code')
        issues.append(_mk(
            'warn', 'Dead Code', 'Disconnected feature (implemented but unreferenced)',
            n['rel'],
            f"This file has real surface area ({why}) but nothing in the project "
            f"imports it and its name appears in no import. Likely an implemented "
            f"feature that was never wired in (or reachable only dynamically). "
            f"Verify it's actually used.",
            ''))
    return issues


# ── 7. Platform-gated dead UI ──────────────────────────────────────────────────
def platform_gated_dead_ui(nodes, file_texts):
    """A component whose handlers all call an injected platform bridge with
    optional chaining (e.g. `window.electronAPI?.x()`) and no fallback — it
    silently no-ops outside that platform (browser/SSR)."""
    issues = []
    bridge_re = re.compile(r'window\.([A-Za-z_]\w*)\?\.')
    for n in nodes:
        rel = n['rel']
        if not _is_js(rel):
            continue
        text = file_texts.get(rel, '')
        if not text:
            continue
        bridges = Counter(bridge_re.findall(text))
        if not bridges:
            continue
        bridge, count = bridges.most_common(1)[0]
        # only treat injected platform bridges (electron-style or *API) as gates
        if not (bridge == 'electronAPI' or bridge == 'electron' or bridge.endswith('API')):
            continue
        if count < 2:
            continue
        # a non-optional guard or fallback means it's handled — skip
        if (re.search(rf'window\.{re.escape(bridge)}\b(?!\?)', text)
                or re.search(r'\belse\b', text)
                or 'typeof window' in text):
            continue
        issues.append(_mk(
            'warn', 'Dead Code', 'Platform-gated dead UI',
            rel,
            f"All {count} handlers call `window.{bridge}?.*` with optional chaining "
            f"and no fallback — this component silently no-ops when `{bridge}` is "
            f"absent (a browser/SSR build, or before the bridge loads). Add a guard "
            f"or a fallback path.",
            ''))
    return issues


# ── Orchestrator ──────────────────────────────────────────────────────────────
def detect_cross_file(nodes, edges, file_texts, include_reference=False, soft_refs=None):
    """Run all cross-file detectors and return a combined issue list.

    Reference/vendored files are excluded from analysis by default (pass
    include_reference=True to include them). `soft_refs` is the set of
    import-target basenames across the whole repo — used so a file referenced
    anywhere is never mislabelled as dead."""
    id_to_rel = {n['id']: n['rel'] for n in nodes}
    if soft_refs is None:
        from .scanner import all_import_target_basenames
        soft_refs = all_import_target_basenames(file_texts)

    # analysis scope: app-tier, non-test files. Reference/vendored code is excluded
    # unless opted in, and test scaffolding is excluded from structural analysis
    # (a test file legitimately imports nothing and is imported by nothing).
    scope = [n for n in nodes
             if (include_reference or n.get('tier') != 'reference')
             and not _is_test(n['rel'])]

    from . import scanner
    use_ast = getattr(scanner, '_USE_AST', False)
    ast_index = None
    if use_ast:
        from . import ast_index as _ai
        ast_index = _ai

    defs_by_file = {}
    for n in scope:
        rel = n['rel']
        t = file_texts.get(rel, '')
        if not t or not (_is_js(rel) or _is_py(rel)):
            continue
        d = _extract_defs(rel, t)                      # regex baseline (names + some params)
        if ast_index is not None:
            sigs = ast_index.extract_signatures_ast(rel, t)
            if sigs is not None:
                for nm, meta in sigs.items():          # AST params/variadic are authoritative
                    d[nm] = meta
                for nm, line in (ast_index.extract_defs_ast(rel, t) or []):
                    d.setdefault(nm, {'params': None, 'line': line})  # classes/consts → names
        if d:
            defs_by_file[rel] = d

    issues = []
    issues += broken_contracts(scope, edges, file_texts, defs_by_file, id_to_rel)
    # Arg-count mismatch runs ONLY in AST mode, using AST signatures AND AST
    # call-argument counts on BOTH sides — regex miscounts args inside template
    # literals / nested literals, which produced false positives. Accurate parse
    # trees make it safe.
    if use_ast:
        issues += _arg_mismatch_ast(scope, file_texts, defs_by_file, id_to_rel, edges, ast_index)
    issues += missing_guard(scope, file_texts)
    issues += cycles(scope, edges, id_to_rel, file_texts)
    issues += orphans(scope, edges, file_texts, defs_by_file, id_to_rel)
    issues += orphaned_but_substantial(scope, edges, file_texts, defs_by_file, soft_refs)
    issues += platform_gated_dead_ui(scope, file_texts)
    issues += duplication_drift(scope, file_texts)
    return issues
