"""
File discovery + dependency-graph construction.

Zero external dependencies. Resolves import/require statements across a project
into a node/edge graph. Each source file is a node; each resolved import is an
edge. Works language-agnostically with per-language import resolvers.

Resolution is bundler-like: it tries an exact path match first, then a
*unique* suffix match (handles off-by-one relative depths, tsconfig `baseUrl`,
and aliased roots), then a *unique* basename match. Uniqueness is the guard
against inventing phantom edges. This directly kills the "false orphan" class
where a file is clearly imported but the exact path didn't resolve.
"""
import hashlib
import os
import re
from pathlib import Path

# Directories we never descend into.
DEFAULT_IGNORE_DIRS = {
    'node_modules', '.git', '.next', '.nuxt', 'dist', 'build', 'out',
    '__pycache__', '.venv', 'venv', 'env', '.turbo', '.cache', 'coverage',
    '.vercel', '.netlify', 'vendor', 'target', '.idea', '.vscode',
    '.svelte-kit', '.parcel-cache', 'bower_components', '.pytest_cache',
    '.mypy_cache', '.tox', 'site-packages', '.gradle', 'Pods', '.expo',
}

# Path segments that mark a file as "reference / vendored / non-app" — we still
# scan these (they're useful context) but they are TIERED OUT of issue counts by
# default, so third-party noise never drowns your own code's findings.
REFERENCE_SEGMENTS = {
    'reference', 'references', 'third_party', 'third-party', 'thirdparty',
    'external', 'externals', 'vendored', 'examples', 'example', 'samples',
    'sample', 'fixtures', 'fixture', 'testdata', 'test-data', 'mocks',
    '__mocks__', 'snapshots', '__snapshots__', 'demo', 'demos',
    'benchmark', 'benchmarks', 'bench', 'perf', 'perf-measures',
    'perf_measures', 'profiling',
}

# Extensions we treat as source and try to parse imports from.
SOURCE_EXTS = {
    '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts', '.vue', '.svelte',
    '.py', '.rb', '.go', '.rs', '.java', '.kt', '.php', '.cs', '.swift',
    '.c', '.h', '.cpp', '.hpp', '.cc', '.m', '.scala', '.dart', '.ex',
    '.exs', '.elm', '.sql',
}

# Documentation/spec extensions — indexed for semantic recall (`--explain`),
# never added to the dependency graph.
DOC_EXTS = {'.md', '.mdx', '.markdown', '.txt', '.rst', '.adoc'}

# Binary/visual assets — discovered for the multimodal pass and linked to the
# nodes near them. Their *contents* are interpreted by the Claude skill (vision),
# not by nodo's core, so the core stays no-network / zero-dependency.
ASSET_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.svg',
    '.pdf', '.mp4', '.mov', '.webm',
}

# Extension priority for resolving collisions (two files share a path stem).
# A bundler prefers .ts over .js, etc.; we mirror that so edges are deterministic.
EXT_PRIORITY = [
    '.ts', '.tsx', '.d.ts', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs',
    '.vue', '.svelte', '.py', '.go', '.rs', '.java', '.kt', '.rb', '.php',
    '.cs', '.swift', '.c', '.h', '.cpp', '.hpp', '.cc', '.m', '.scala',
    '.dart', '.ex', '.exs', '.elm', '.sql',
]

# Optional AST backend (tree-sitter) — off unless enabled via --ast. When on and
# the grammar is installed, import/symbol extraction uses real parse trees;
# otherwise it silently falls back to the regex path below. Zero-dep by default.
_USE_AST = False


def enable_ast():
    global _USE_AST
    _USE_AST = True


# How files get categorized for colouring/grouping. Order matters: first match wins.
# Tuned to be useful across typical project layouts without assuming a framework.
CATEGORY_RULES = [
    ('test',      lambda p: bool(re.search(r'(\.test\.|\.spec\.|__tests__|/tests?/|_test\.)', p))),
    ('config',    lambda p: bool(re.search(r'(config|\.config\.|tsconfig|webpack|vite\.|rollup|babel|eslint|prettier)', p, re.I)) and '/src/' not in p),
    ('api',       lambda p: bool(re.search(r'(/api/|/routes?/|/controllers?/|/endpoints?/|/handlers?/|route\.(t|j)s)', p, re.I))),
    ('component', lambda p: bool(re.search(r'(/components?/|/ui/|/views?/|/widgets?/|\.vue$|\.svelte$)', p, re.I))),
    ('page',      lambda p: bool(re.search(r'(/pages?/|/screens?/|/app/.*page\.|/app/.*layout\.)', p, re.I))),
    ('store',     lambda p: bool(re.search(r'(/store/|/stores/|/state/|/redux/|/context/|/hooks?/)', p, re.I))),
    ('model',     lambda p: bool(re.search(r'(/models?/|/schema/|/entities/|/migrations?/|\.sql$)', p, re.I))),
    ('style',     lambda p: bool(re.search(r'\.(css|scss|sass|less|styl)$', p, re.I))),
    ('lib',       lambda p: bool(re.search(r'(/lib/|/libs/|/utils?/|/helpers?/|/services?/|/core/|/shared/|/common/)', p, re.I))),
]


def categorize(rel_path):
    p = rel_path.replace('\\', '/')
    for key, test in CATEGORY_RULES:
        try:
            if test(p):
                return key
        except re.error:
            continue
    return 'other'


def tier_of(rel_path, reference_segments=None):
    """'reference' if any path segment marks the file as vendored/non-app, else 'app'."""
    segs = set(s.lower() for s in rel_path.replace('\\', '/').split('/'))
    refs = REFERENCE_SEGMENTS | (set(s.lower() for s in reference_segments) if reference_segments else set())
    return 'reference' if segs & refs else 'app'


def load_gitignore(root):
    """Best-effort parse of .gitignore into simple directory names to skip.
    Only plain directory entries (no globs) to stay dependency-free."""
    extra = set()
    gi = root / '.gitignore'
    if gi.exists():
        try:
            for line in gi.read_text(encoding='utf-8', errors='ignore').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('!'):
                    continue
                name = line.rstrip('/').lstrip('/')
                if name and '/' not in name and '*' not in name and name[0] != '.':
                    extra.add(name)
        except Exception:
            pass
    return extra


def _walk(root, ignore_dirs, exts, max_file_kb, diagnostics=None):
    """Yield (abs_path, rel_path) for files whose extension is in `exts`.
    Records oversized/unstattable files into `diagnostics` (no silent skips)."""
    root = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ignore_dirs and (not d.startswith('.') or d == '.github')]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in exts:
                continue
            abs_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_path, root).replace('\\', '/')
            try:
                if os.path.getsize(abs_path) > max_file_kb * 1024:
                    if diagnostics is not None:
                        diagnostics.setdefault('skipped_large', []).append(rel)
                    continue
            except OSError:
                if diagnostics is not None:
                    diagnostics.setdefault('stat_errors', []).append(rel)
                continue
            yield abs_path, rel


def discover_files(root, ignore_dirs, max_file_kb=512, diagnostics=None):
    """Yield (abs_path, rel_path) for every source file under root."""
    yield from _walk(root, ignore_dirs, SOURCE_EXTS, max_file_kb, diagnostics)


def discover_docs(root, ignore_dirs, max_file_kb=1024):
    """Return {rel: text} for documentation files (md/txt/rst/...)."""
    out = {}
    for abs_path, rel in _walk(root, ignore_dirs, DOC_EXTS, max_file_kb):
        try:
            out[rel] = Path(abs_path).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            out[rel] = ''
    return out


def discover_assets(root, ignore_dirs, max_file_kb=51200):
    """Return [{rel, type, size, tier}] for visual/binary assets (images, pdf, video)."""
    out = []
    for abs_path, rel in _walk(root, ignore_dirs, ASSET_EXTS, max_file_kb):
        try:
            size = os.path.getsize(abs_path)
        except OSError:
            size = 0
        out.append({
            'rel': rel,
            'type': os.path.splitext(rel)[1].lower().lstrip('.'),
            'size': size,
            'tier': tier_of(rel),
        })
    return out


# ── Import extraction per language family ────────────────────────────────────
JS_IMPORT_RES = [
    re.compile(r'''import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]'''),
    re.compile(r'''require\(\s*['"]([^'"]+)['"]\s*\)'''),
    re.compile(r'''import\(\s*['"]([^'"]+)['"]\s*\)'''),
    # `export * from`, `export * as ns from`, `export { a } from`
    re.compile(r'''export\s+(?:\*(?:\s+as\s+\w+)?|\{[^}]*\})\s+from\s+['"]([^'"]+)['"]'''),
    # dynamic import / require with a STATIC template literal (no ${} interpolation)
    re.compile(r'''import\(\s*`([^`$]+)`\s*\)'''),
    re.compile(r'''require\(\s*`([^`$]+)`\s*\)'''),
]
PY_IMPORT_RES = [
    re.compile(r'^\s*from\s+([.\w]+)\s+import\b', re.M),
    re.compile(r'^\s*import\s+([.\w]+)', re.M),
]
GENERIC_IMPORT_RES = [
    re.compile(r'''(?:import|use|include|require|from)\s+['"<]([^'">\s]+)['">]'''),
]


def extract_imports(rel_path, text):
    """Return raw import target strings found in a file's text."""
    if _USE_AST:
        try:
            from . import ast_index
            ast_hits = ast_index.extract_imports_ast(rel_path, text)
            if ast_hits is not None:
                return ast_hits
        except Exception:
            pass  # any AST failure → fall through to the regex path
    ext = os.path.splitext(rel_path)[1].lower()
    if ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts', '.vue', '.svelte'):
        regexes = JS_IMPORT_RES
    elif ext == '.py':
        regexes = PY_IMPORT_RES
    else:
        regexes = GENERIC_IMPORT_RES
    out = []
    for rx in regexes:
        out.extend(rx.findall(text))
    return out


def all_import_target_basenames(file_texts):
    """Set of lowercased basenames (no extension) of EVERY import target string
    in the corpus, resolved or not. Used as a 'soft reference' set so a file that
    is clearly referenced somewhere is never mislabelled dead code."""
    bases = set()
    for rel, text in file_texts.items():
        if not text:
            continue
        for target in extract_imports(rel, text):
            base = re.sub(r'\.[^./]+$', '', target.replace('\\', '/').rstrip('/').split('/')[-1])
            if base:
                bases.add(base.lower())
    return bases


# ── Resolving import strings to actual files in the project ──────────────────
def _ext_rank(rel):
    ext = os.path.splitext(rel)[1].lower()
    try:
        return EXT_PRIORITY.index(ext)
    except ValueError:
        return len(EXT_PRIORITY)


def _build_resolution_index(rel_paths):
    """Index files for resolution. Buckets map to *lists* (collision-aware) sorted
    by extension priority then path length, so the winner is deterministic."""
    by_noext = {}      # 'src/lib/foo' -> [rel, ...]
    by_basename = {}   # 'foo' -> [rel, ...]
    for rp in rel_paths:
        noext = re.sub(r'\.[^./]+$', '', rp)
        by_noext.setdefault(noext, []).append(rp)
        if noext.endswith('/index'):
            by_noext.setdefault(noext[:-len('/index')], []).append(rp)
        if noext.endswith('/__init__'):
            by_noext.setdefault(noext[:-len('/__init__')], []).append(rp)
        base = noext.split('/')[-1]
        by_basename.setdefault(base, []).append(rp)
    for d in (by_noext, by_basename):
        for k in list(d.keys()):
            d[k] = sorted(set(d[k]), key=lambda r: (_ext_rank(r), len(r), r))
    return {'noext': by_noext, 'basename': by_basename, 'noext_keys': list(by_noext.keys())}


def _pick(bucket):
    return bucket[0] if bucket else None


def _match_exact(cand, idx):
    cand = cand.strip('/')
    by = idx['noext']
    if cand in by:
        return _pick(by[cand])
    for suffix in ('/index', '/__init__'):
        if cand + suffix in by:
            return _pick(by[cand + suffix])
    return None


def _match_unique_suffix(cand, idx):
    """Resolve when exactly one distinct file is indexed under a path ending in
    the candidate's trailing segment(s). Handles off-by-one relative depths and
    aliased/base roots (e.g. '../../lib/audio' for 'src/app/lib/audio') without
    inventing phantom edges — a match is accepted only when it is unique.

    Tries the last two path segments first (specific), then the last one."""
    cand = cand.strip('/')
    if not cand:
        return None
    segs = cand.split('/')
    for n in (2, 1):
        if len(segs) < n:
            continue
        tail = '/'.join(segs[-n:])
        # a bare, short basename is too ambiguous to match on alone
        if n == 1 and len(tail) < 4:
            continue
        suffix = '/' + tail
        files = set()
        for k in idx['noext_keys']:
            if k == tail or k.endswith(suffix):
                files.update(idx['noext'][k])
        files = sorted(files, key=lambda r: (_ext_rank(r), len(r), r))
        if len(files) == 1:
            return files[0]
    return None


def resolve_import(importer_rel, target, idx):
    """Resolve one import string to a project-relative file path, or None if external."""
    target = target.strip()
    if not target:
        return None

    cands = []
    if target.startswith('.'):
        importer_dir = os.path.dirname(importer_rel)
        if re.match(r'^\.+/', target) or target in ('.', '..'):
            cands.append(os.path.normpath(os.path.join(importer_dir, target)).replace('\\', '/'))
        else:
            # Python-style relative: leading dots = parent levels
            dots = len(target) - len(target.lstrip('.'))
            mod = target.lstrip('.').replace('.', '/')
            up = importer_dir
            for _ in range(max(0, dots - 1)):
                up = os.path.dirname(up)
            cands.append(os.path.normpath(os.path.join(up, mod)).replace('\\', '/'))
    else:
        aliased = re.sub(r'^@[/]?', '', target)   # '@/lib/x' -> 'lib/x'
        aliased = re.sub(r'^~/', '', aliased)     # '~/lib/x' -> 'lib/x'
        py_path = target.replace('.', '/')        # 'app.lib.x' -> 'app/lib/x'
        for c in (target, aliased, py_path):
            cands.append(c)
            for prefix in ('src/', 'app/', 'lib/', 'packages/', 'source/'):
                cands.append(prefix + c)

    # 1) exact path match (highest confidence)
    for c in cands:
        hit = _match_exact(c, idx)
        if hit:
            return hit
    # 2) unique-suffix fallback — off-by-one relative depth, aliased/base roots
    for c in cands:
        hit = _match_unique_suffix(c, idx)
        if hit:
            return hit
    # 3) unique-basename fallback (only when globally unique → safe)
    base = re.sub(r'\.[^./]+$', '', target.replace('\\', '/').rstrip('/').split('/')[-1])
    bucket = idx['basename'].get(base)
    if bucket and len(bucket) == 1:
        return bucket[0]
    return None


def build_graph(root, ignore_dirs=None, respect_gitignore=True, max_file_kb=512,
                reference_segments=None, cache=None, diagnostics=None):
    """Scan `root` and return (nodes, edges, file_texts).

    nodes:      list of {id, label, rel, category, loc, tier, kind}
    edges:      list of {source, target, kind}  (file-id -> file-id)
    file_texts: {rel: text}  (cached so detectors don't re-read)

    cache:       optional {rel: {mtime,size,ast,imports}} parse cache, mutated in
                 place — unchanged files skip re-parsing (results are identical).
    diagnostics: optional dict; records skipped/oversized/read-error files and
                 cache hit/parse counts so nothing is dropped silently.
    """
    root = Path(root).resolve()
    ignore = set(DEFAULT_IGNORE_DIRS)
    if ignore_dirs:
        ignore |= set(ignore_dirs)
    if respect_gitignore:
        ignore |= load_gitignore(root)

    files = list(discover_files(root, ignore, max_file_kb, diagnostics))
    rel_paths = [rel for _, rel in files]
    idx = _build_resolution_index(rel_paths)

    file_texts = {}
    raw_imports = {}
    for abs_path, rel in files:
        try:
            text = Path(abs_path).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            text = ''
            if diagnostics is not None:
                diagnostics.setdefault('read_errors', []).append(rel)
        file_texts[rel] = text
        # Cache key is a CONTENT hash (+ parser mode), so it's correct even when an
        # edit preserves mtime/size (e.g. a git checkout). We already hold the text,
        # so hashing is cheap; a cached run is byte-identical to a clean one.
        h = hashlib.sha1(text.encode('utf-8', 'ignore')).hexdigest()
        ce = cache.get(rel) if isinstance(cache, dict) else None
        if ce and ce.get('hash') == h and ce.get('ast') == _USE_AST:
            raw_imports[rel] = ce.get('imports', [])
            if diagnostics is not None:
                diagnostics['cache_hits'] = diagnostics.get('cache_hits', 0) + 1
        else:
            imps = extract_imports(rel, text)
            raw_imports[rel] = imps
            if isinstance(cache, dict):
                cache[rel] = {'hash': h, 'ast': _USE_AST, 'imports': imps}
            if diagnostics is not None:
                diagnostics['parsed'] = diagnostics.get('parsed', 0) + 1
    # drop cache entries for files that no longer exist
    if isinstance(cache, dict):
        present = set(rel_paths)
        for r in [k for k in cache if k not in present]:
            del cache[r]

    id_of = {rel: i for i, rel in enumerate(rel_paths)}
    nodes = []
    for rel in rel_paths:
        loc = file_texts[rel].count('\n') + 1 if file_texts[rel] else 0
        nodes.append({
            'id': id_of[rel],
            'label': rel.split('/')[-1],
            'rel': rel,
            'category': categorize(rel),
            'loc': loc,
            'tier': tier_of(rel, reference_segments),
            'kind': 'code',
        })

    seen = set()
    edges = []
    for rel in rel_paths:
        src_id = id_of[rel]
        for target in raw_imports[rel]:
            resolved = resolve_import(rel, target, idx)
            if resolved and resolved != rel:
                key = (src_id, id_of[resolved])
                if key not in seen:
                    seen.add(key)
                    edges.append({'source': src_id, 'target': id_of[resolved], 'kind': 'import'})

    return nodes, edges, file_texts
