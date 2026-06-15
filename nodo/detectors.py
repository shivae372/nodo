"""
Issue detection — generic code smells + project-defined custom rules.

Built-in detectors are language-aware where it matters and skip test files for
the noisier checks. Projects can add their own regex rules via a config file
(see config.py), so the tool adapts to any codebase without editing source.

Every issue carries: severity, category, type, file, line, detail, snippet.
"""
import re
from collections import defaultdict


SEVERITY_ORDER = {'error': 0, 'warn': 1, 'info': 2}


def _snippet(lines, line_no, ctx=1):
    if not line_no:
        return []
    lo = max(0, line_no - 1 - ctx)
    hi = min(len(lines), line_no + ctx)
    return [{'n': i + 1, 'text': lines[i][:160]} for i in range(lo, hi)]


_TEST_RE = re.compile(
    r'(^|/)(tests?|specs?|__tests?__|e2e|__mocks__)/'  # test/ tests/ spec/ … even top-level
    r'|(\.|_)(test|spec)\.'                            # foo.test.js  foo_spec.rb
    r'|(^|/)test_[^/]*\.py$'                           # python test_foo.py
    r'|(^|/)conftest\.py$',                            # pytest conftest
    re.I)


def _is_test(rel):
    return bool(_TEST_RE.search(rel))


def _is_js(rel):
    return rel.lower().endswith(('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte'))


def _is_py(rel):
    return rel.lower().endswith('.py')


# ── Built-in line-level detectors ─────────────────────────────────────────────
# Each entry: (severity, category, type, detail, predicate(line)->bool, lang_filter)
# lang_filter: None=all, 'js', 'py'
LINE_RULES = [
    ('info', 'Debugging', 'console.log left in code',
     'console.log ships to production logs. Use a logger or remove before release.',
     lambda l: 'console.log(' in l and not l.lstrip().startswith('//'), 'js'),

    ('warn', 'Security', 'Math.random() for value',
     'Math.random() is not cryptographically secure. For tokens/IDs/slugs use crypto.randomUUID() or crypto.getRandomValues().',
     lambda l: 'Math.random()' in l and not l.lstrip().startswith('//'), 'js'),

    ('warn', 'Security', 'dangerouslySetInnerHTML',
     'Injects raw HTML — an XSS vector if the value is ever user-derived. Confirm the source is a static constant or sanitized.',
     lambda l: 'dangerouslySetInnerHTML' in l and '__html' in l, 'js'),

    ('warn', 'Security', 'eval() / new Function()',
     'Dynamic code execution. Avoid eval/new Function on any value that could be attacker-influenced.',
     lambda l: bool(re.search(r'\beval\(|new Function\(', l)) and not l.lstrip().startswith('//'), 'js'),

    ('warn', 'Reliability', 'Empty catch block',
     'An empty catch silently swallows errors — failures become invisible. Log or re-throw.',
     lambda l: bool(re.search(r'catch\s*\([^)]*\)\s*\{\s*\}', l)), 'js'),

    ('warn', 'Type Safety', 'TypeScript check suppressed',
     '@ts-ignore / @ts-nocheck hides a real type error. Fix the underlying type instead.',
     lambda l: '@ts-ignore' in l or '@ts-nocheck' in l, 'js'),

    ('info', 'Type Safety', 'any escape hatch',
     '`as any` / `: any` disables type-checking. Prefer a concrete type or `unknown` + a guard.',
     lambda l: (' as any' in l or re.search(r':\s*any[\s;,)\]]', l)) and not l.lstrip().startswith('//'), 'js'),

    ('info', 'Tech Debt', 'ESLint rule suppressed',
     'A lint rule is disabled here. Confirm the suppression is justified, not hiding a bug.',
     lambda l: 'eslint-disable' in l, 'js'),

    ('info', 'Config', 'Unchecked env non-null assertion',
     'process.env.X! throws unhelpfully at runtime if the var is unset. Validate env at boot.',
     lambda l: bool(re.search(r'process\.env\.\w+!', l)), 'js'),

    ('warn', 'Reliability', 'fetch() without timeout',
     'A bare fetch() can hang forever if the peer stalls. Wrap with AbortController/timeout.',
     lambda l: bool(re.search(r'\bawait\s+fetch\(', l)) and 'signal' not in l and 'timeout' not in l.lower(), 'js'),

    ('warn', 'Security', 'Possible hardcoded secret',
     'A long literal assigned to a key/token/secret/password variable. Move to an env var.',
     lambda l: bool(re.search(r'''(?i)(api[_-]?key|secret|token|password|passwd|access[_-]?key)\s*[:=]\s*['"][A-Za-z0-9_\-/+]{16,}['"]''', l)), None),

    ('warn', 'Security', 'Possible SQL injection',
     'A SQL statement built with string concatenation/interpolation. Use parameterized queries / prepared statements.',
     lambda l: bool(re.search(r'(?i)\b(select|insert\s+into|update|delete\s+from)\b', l))
               and bool(re.search(r'(\+\s*\w|\$\{|%s\b|%\(|\.format\(|f["\'])', l)), None),

    ('warn', 'Security', 'Unsafe deserialization',
     'pickle/marshal/yaml.load on untrusted input can execute arbitrary code. Use a safe loader (yaml.safe_load, json).',
     lambda l: bool(re.search(r'\b(pickle\.loads?|marshal\.loads|yaml\.load)\s*\(', l))
               and 'SafeLoader' not in l, 'py'),

    ('info', 'Debugging', 'print() left in code',
     'A stray print() — use logging in shipped Python code.',
     lambda l: bool(re.match(r'\s*print\(', l)) and not l.lstrip().startswith('#'), 'py'),

    ('warn', 'Reliability', 'Bare except',
     'except: with no type swallows everything including KeyboardInterrupt. Catch a specific exception.',
     lambda l: bool(re.match(r'\s*except\s*:', l)), 'py'),
]

# TODO/FIXME markers handled specially (need to capture the note text).
MARKER_RE = re.compile(r'(?://|#|/\*|\*)\s*(TODO|FIXME|HACK|XXX)\b[:\s]?(.*)')


def run_builtin_detectors(nodes, edges, file_texts, include_reference=False, soft_refs=None):
    """Return a list of issue dicts from the built-in detector set.

    Reference/vendored files (tier='reference') are skipped by default so
    third-party noise never drowns your own code — pass include_reference=True to
    analyse them too. `soft_refs` is the set of import-target basenames across the
    repo; a file whose basename appears there is never called dead code."""
    issues = []
    deg = _degree(len(nodes), edges)
    id_to_node = {n['id']: n for n in nodes}
    if soft_refs is None:
        from .scanner import all_import_target_basenames
        soft_refs = all_import_target_basenames(file_texts)

    def _skip_tier(n):
        return (not include_reference) and n.get('tier') == 'reference'

    # ── topology issues ──
    ranked = sorted(deg.items(), key=lambda kv: kv[1], reverse=True)
    if ranked:
        # god objects: top-degree nodes far above the median
        degrees = sorted(deg.values())
        median = degrees[len(degrees) // 2] if degrees else 0
        threshold = max(20, median * 8)
        for nid, d in ranked:
            if d >= threshold and not _skip_tier(id_to_node[nid]):
                n = id_to_node[nid]
                issues.append(_mk('warn', 'Coupling', 'High-coupling hub',
                    n['label'], n['rel'],
                    f'{d} connections — far above the project median ({median}). A change here has a large blast radius; consider splitting responsibilities.',
                    '', []))

    # dead / isolated code (no edges, not an entrypoint-looking file).
    # Suppressed when: the file is reference-tier, its basename is referenced
    # anywhere (soft ref), or it's substantial (crossfile's "disconnected feature"
    # detector owns those, with a sharper message).
    entry_pat = re.compile(r'(index|main|app|__init__|setup|conftest|page|layout|route)\.', re.I)
    for n in nodes:
        if _skip_tier(n):
            continue
        base = re.sub(r'\.[^./]+$', '', n['label']).lower()
        if (deg.get(n['id'], 0) == 0 and not entry_pat.search(n['label'])
                and not _is_test(n['rel']) and base not in soft_refs
                and n.get('loc', 0) < 40):
            if n['category'] in ('lib', 'component', 'model', 'store'):
                issues.append(_mk('info', 'Dead Code', 'Possibly unused file',
                    n['label'], n['rel'],
                    'No resolved imports in or out, and the filename is not referenced anywhere. Likely dead code (or imported dynamically).',
                    '', []))

    # ── line-level detectors ──
    for n in nodes:
        if _skip_tier(n):
            continue
        rel = n['rel']
        text = file_texts.get(rel, '')
        if not text:
            continue
        lines = text.split('\n')
        is_test = _is_test(rel)
        is_js = _is_js(rel)
        is_py = _is_py(rel)
        # CLI / script entrypoints legitimately use print()/console output —
        # suppress the Debugging smell there to avoid noise.
        is_cli = bool(re.search(r'(__main__|/cli|cli\.|/bin/|/scripts?/|manage\.py|setup\.py|conftest)', rel)) \
            or ("if __name__ == '__main__'" in text) or ('if __name__ == "__main__"' in text)

        for sev, cat, typ, detail, pred, lang in LINE_RULES:
            if lang == 'js' and not is_js:
                continue
            if lang == 'py' and not is_py:
                continue
            # debugging/info smells are noisy in tests — skip there
            if is_test and cat in ('Debugging', 'Tech Debt'):
                continue
            # print()/console output is expected in CLI entrypoints
            if is_cli and cat == 'Debugging':
                continue
            for i, line in enumerate(lines):
                try:
                    if pred(line):
                        issues.append(_mk(sev, cat, typ, n['label'], rel,
                                          detail + f'  `{line.strip()[:90]}`',
                                          i + 1, _snippet(lines, i + 1)))
                except re.error:
                    break

        # markers
        for i, line in enumerate(lines):
            m = MARKER_RE.search(line)
            if m:
                kind, note = m.group(1), m.group(2).strip()[:80]
                sev = 'warn' if kind in ('FIXME', 'HACK') else 'info'
                issues.append(_mk(sev, 'Tech Debt', f'{kind} marker', n['label'], rel,
                                  f'{kind}: {note or "(no note)"}',
                                  i + 1, _snippet(lines, i + 1)))

        # intra-file complexity: a cyclomatic-style proxy (count decision points).
        # High branching in a single file is a refactor signal. Conservative
        # threshold + globally capped, so it stays low-noise.
        if not is_test:
            dp = len(re.findall(r'\b(if|for|while|case|switch|catch|elif|except)\b|&&|\|\|', text))
            if dp >= 60:
                issues.append(_mk('info', 'Complexity', 'High decision complexity',
                                  n['label'], rel,
                                  f'{dp} decision points (if/for/while/case/&&/||) in one file — '
                                  f'high branching; consider splitting into smaller units.',
                                  '', []))

    return issues


def run_custom_rules(nodes, file_texts, custom_rules):
    """Apply project-defined regex rules from config.

    custom_rules: list of {name, pattern, severity, category, detail, [include], [exclude]}
    """
    issues = []
    compiled = []
    for r in custom_rules:
        try:
            rx = re.compile(r['pattern'])
        except (re.error, KeyError):
            continue
        inc = re.compile(r['include']) if r.get('include') else None
        exc = re.compile(r['exclude']) if r.get('exclude') else None
        compiled.append((r, rx, inc, exc))

    for n in nodes:
        rel = n['rel']
        text = file_texts.get(rel, '')
        if not text:
            continue
        lines = text.split('\n')
        for r, rx, inc, exc in compiled:
            if inc and not inc.search(rel):
                continue
            if exc and exc.search(rel):
                continue
            for i, line in enumerate(lines):
                if rx.search(line):
                    issues.append(_mk(
                        r.get('severity', 'warn'),
                        r.get('category', 'Custom'),
                        r.get('name', 'Custom rule'),
                        n['label'], rel,
                        r.get('detail', 'Matched a project custom rule.') + f'  `{line.strip()[:90]}`',
                        i + 1, _snippet(lines, i + 1)))
    return issues


def _mk(sev, cat, typ, node, rel, detail, line, snippet):
    return {
        'severity': sev, 'category': cat, 'type': typ,
        'node': node, 'file': rel, 'detail': detail,
        'line': str(line) if line else '', 'snippet': snippet,
    }


def _degree(num_nodes, edges):
    deg = defaultdict(int)
    for e in edges:
        deg[e['source']] += 1
        deg[e['target']] += 1
    for i in range(num_nodes):
        deg.setdefault(i, 0)
    return dict(deg)


# Per-issue confidence so an agent can triage: act on HIGH, weigh MEDIUM, treat
# LOW as a hint. Keyed by issue type; defaults to medium. HIGH = structural facts
# or AST-verified; LOW = fuzzy/noisy heuristics.
_CONFIDENCE = {
    # structural facts / AST-verified → high
    'Import cycle': 'high',
    'Call passes more args than defined': 'high',
    'Imported symbol not exported by source': 'high',
    'Platform-gated dead UI': 'high',
    'Possible hardcoded secret': 'high',
    # fuzzy / noisy heuristics → low
    'Duplicated block across files': 'low',
    'High decision complexity': 'low',
    'console.log left in code': 'low',
    'print() left in code': 'low',
    'any escape hatch': 'low',
    'ESLint rule suppressed': 'low',
    'Possibly unused file': 'low',
    'Exported symbols never imported': 'low',
}


def _confidence_for(issue_type):
    if issue_type in _CONFIDENCE:
        return _CONFIDENCE[issue_type]
    t = issue_type.lower()
    if t.endswith('more)') or 'marker' in t:      # capped summaries / TODO markers
        return 'low'
    return 'medium'


def _apply_confidence(issues):
    for iss in issues:
        iss['confidence'] = _confidence_for(iss['type'])
    return issues


def _cap_per_type(issues, cap=25):
    """Bound noise: keep at most `cap` findings of any single type, replacing the
    overflow with one summary line. A linter that prints 163 `as any` warnings
    buries the signal — this guarantees no single detector can dominate the
    report, on a codebase of any size. High-signal detectors (contracts, cycles,
    disconnected features) are naturally well under the cap and unaffected."""
    kept = defaultdict(int)
    cat_of = {}
    out = []
    extra = defaultdict(int)
    for iss in issues:
        t = iss['type']
        cat_of.setdefault(t, iss['category'])
        if kept[t] < cap:
            out.append(iss)
            kept[t] += 1
        else:
            extra[t] += 1
    for t, n in extra.items():
        out.append(_mk('info', cat_of[t], f'{t} (+{n} more)', '', '',
                       f'{n} additional "{t}" finding(s) omitted to keep the report '
                       f'high-signal (showing the first {cap}). See the full list in '
                       f'nodo-context.json or run a focused query.', '', []))
    return out


def detect_all(nodes, edges, file_texts, custom_rules=None, include_reference=False):
    from .scanner import all_import_target_basenames
    soft_refs = all_import_target_basenames(file_texts)
    issues = run_builtin_detectors(nodes, edges, file_texts,
                                   include_reference=include_reference, soft_refs=soft_refs)
    if custom_rules:
        issues += run_custom_rules(nodes, file_texts, custom_rules)
    # cross-file detectors: the bugs an LLM editing one file can't see
    try:
        from .crossfile import detect_cross_file
        import sys
        # cycle DFS can go deep on large graphs; lift the limit for the call
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, len(nodes) * 4 + 1000))
        try:
            issues += detect_cross_file(nodes, edges, file_texts,
                                        include_reference=include_reference, soft_refs=soft_refs)
        finally:
            sys.setrecursionlimit(old_limit)
    except Exception as e:
        # never let an analysis bug break the whole run
        print(f'[nodo] cross-file analysis skipped: {e}')
    issues = _cap_per_type(issues)
    issues = _apply_confidence(issues)
    issues.sort(key=lambda x: (SEVERITY_ORDER.get(x['severity'], 9), x['category'], x['file']))
    for i, iss in enumerate(issues):
        iss['idx'] = i
    return issues
