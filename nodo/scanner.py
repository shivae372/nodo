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
    '.py', '.rb', '.go', '.rs', '.java', '.kt', '.kts', '.php', '.cs', '.swift',
    '.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.m', '.scala', '.sc', '.dart',
    '.lua', '.ex', '.exs', '.elm', '.sql', '.sol', '.sh', '.bash',
}

# Documentation/spec extensions — indexed for semantic recall (`--explain`),
# never added to the dependency graph.
DOC_EXTS = {'.md', '.mdx', '.markdown', '.txt', '.rst', '.adoc'}

# Binary/visual assets — discovered for the multimodal pass and linked to the
# nodes near them. Their *contents* are interpreted by the Claude skill (vision),
# not by nodo's core, so the core stays no-network / zero-dependency.
ASSET_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.svg',
    '.pdf', '.mp4', '.mov', '.webm', '.mp3', '.wav',
    # documents that markitdown converts to Markdown (token-cheap for agents)
    '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls',
    '.epub', '.rtf', '.odt', '.html', '.htm', '.csv', '.tsv',
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


# Optional learned lessons (see lessons.py) — taught languages + corrections.
# When set, taught extensions become first-class source files and their def/import
# regexes augment extraction. nodo stays offline; Claude writes the lessons.
_LESSONS = None


def enable_lessons(lessons):
    global _LESSONS
    _LESSONS = lessons or None
    try:                                  # let lesson `grammar` fields light up AST
        from . import ast_index, lessons as _l
        ast_index.set_lesson_grammars(_l.grammar_map(_LESSONS) if _LESSONS else {})
    except Exception:
        pass


def disable_lessons():
    global _LESSONS
    _LESSONS = None
    try:
        from . import ast_index
        ast_index.set_lesson_grammars({})
    except Exception:
        pass


def _effective_source_exts():
    if _LESSONS:
        from . import lessons as _l
        return SOURCE_EXTS | _l.taught_extensions(_LESSONS)
    return SOURCE_EXTS


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
    """Yield (abs_path, rel_path) for every source file under root.
    Includes extensions taught via lessons so a learned language is first-class."""
    yield from _walk(root, ignore_dirs, _effective_source_exts(), max_file_kb, diagnostics)


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

# ── Compiled-language families (unquoted import syntax the generic regex,
#    which requires a quote/'<' after the keyword, could never see) ──────────
RUST_USE_RE = re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([A-Za-z_][\w:]*)', re.M)
RUST_MOD_RE = re.compile(r'^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;', re.M)
GO_IMPORT_SINGLE_RE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
GO_IMPORT_BLOCK_RE = re.compile(r'^\s*import\s*\(([^)]*)\)', re.M | re.S)
GO_BLOCK_LINE_RE = re.compile(r'"([^"]+)"')
JAVA_IMPORT_RE = re.compile(r'^\s*import\s+(?:static\s+)?([\w.]+)', re.M)
CSHARP_USING_RE = re.compile(r'^\s*(?:global\s+)?using\s+(?:static\s+)?([\w.]+)\s*;', re.M)
PHP_USE_RE = re.compile(r'^\s*use\s+(?:function\s+|const\s+)?([\w\\]+)', re.M)


def _rust_imports(text):
    """`use`/`mod` targets as slashed paths. crate:: → bare (prefix pass finds
    src/), super::/self:: → ../ and ./ relative to the importer, `mod x;` → ./x
    (x.rs or x/mod.rs — _match_exact knows the /mod convention)."""
    out = []
    for target in RUST_USE_RE.findall(text):
        segs = [s for s in target.split('::') if s]
        if not segs:
            continue
        head = segs[0]
        if head in ('std', 'core', 'alloc'):        # stdlib → external
            continue
        if head == 'crate':
            rest = segs[1:]
            if rest:
                out.append('/'.join(rest))
        elif head == 'self':
            rest = segs[1:]
            if rest:
                out.append('./' + '/'.join(rest))
        elif head == 'super':
            ups = 1
            rest = segs[1:]
            while rest and rest[0] == 'super':
                ups += 1
                rest = rest[1:]
            if rest:
                out.append('../' * ups + '/'.join(rest))
        else:                                        # external crate OR local module
            out.append('/'.join(segs))
        # the full path may name a symbol, not a module — offer the parent too
        if head in ('crate', 'self') and len(segs) > 2:
            out.append('/'.join(segs[1:-1]) if head == 'crate'
                       else './' + '/'.join(segs[1:-1]))
    for m in RUST_MOD_RE.findall(text):
        out.append('./' + m)
    return out


# Go standard-library roots. Local packages in a Go module are imported via the
# module path (github.com/…) — a stdlib-rooted path can never be a local file, but
# its name often collides with one (`import "errors"` vs a local errors.go), which
# would forge edges and crown false hubs. Filter them at extraction.
GO_STDLIB = frozenset({
    'archive', 'bufio', 'builtin', 'bytes', 'cmp', 'compress', 'container',
    'context', 'crypto', 'database', 'debug', 'embed', 'encoding', 'errors',
    'expvar', 'flag', 'fmt', 'go', 'hash', 'html', 'image', 'index', 'io',
    'iter', 'log', 'maps', 'math', 'mime', 'net', 'os', 'path', 'plugin',
    'reflect', 'regexp', 'runtime', 'slices', 'sort', 'strconv', 'strings',
    'structs', 'sync', 'syscall', 'testing', 'text', 'time', 'unicode',
    'unique', 'unsafe', 'weak',
})


def _go_imports(text):
    """Import paths from single-line and block form, stdlib filtered out.
    Package paths point at DIRECTORIES — resolution's dir fallback maps them
    to the package's representative file."""
    raw = list(GO_IMPORT_SINGLE_RE.findall(text))
    for block in GO_IMPORT_BLOCK_RE.findall(text):
        raw.extend(GO_BLOCK_LINE_RE.findall(block))
    return [t for t in raw if t.split('/', 1)[0] not in GO_STDLIB]


def _dotted_imports(text, rx, sep='.'):
    """Dotted (Java/Kotlin/C#) or backslashed (PHP) module paths → slashed."""
    out = []
    for target in rx.findall(text):
        t = target.strip(sep).replace(sep, '/')
        if t and t != 'var':          # C#: `using var x = …` is a statement, not an import
            out.append(t)
    return out


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _lesson_imports(rel_path, text):
    """Import targets from taught import_patterns (additive), or [] if untaught."""
    if not _LESSONS:
        return []
    from . import lessons as _l
    return _l.extract_imports(rel_path, text, _LESSONS) or []


def extract_imports(rel_path, text):
    """Return raw import target strings found in a file's text.
    Lesson-taught import patterns are merged in (additive) so a learned language
    contributes edges even when the built-in extractors don't know it."""
    lesson_hits = _lesson_imports(rel_path, text)
    if _USE_AST:
        try:
            from . import ast_index
            ast_hits = ast_index.extract_imports_ast(rel_path, text)
            if ast_hits is not None:
                return _dedup(ast_hits + lesson_hits)
        except Exception:
            pass  # any AST failure → fall through to the regex path
    ext = os.path.splitext(rel_path)[1].lower()
    if ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts', '.vue', '.svelte'):
        regexes = JS_IMPORT_RES
    elif ext == '.py':
        regexes = PY_IMPORT_RES
    elif ext == '.rs':
        return _dedup(_rust_imports(text) + lesson_hits)
    elif ext == '.go':
        return _dedup(_go_imports(text) + lesson_hits)
    elif ext in ('.java', '.kt', '.kts', '.scala'):
        return _dedup(_dotted_imports(text, JAVA_IMPORT_RE) + lesson_hits)
    elif ext == '.cs':
        return _dedup(_dotted_imports(text, CSHARP_USING_RE) + lesson_hits)
    elif ext == '.php':
        return _dedup(_dotted_imports(text, PHP_USE_RE, sep='\\') + lesson_hits)
    else:
        regexes = GENERIC_IMPORT_RES
    out = []
    for rx in regexes:
        out.extend(rx.findall(text))
    return _dedup(out + lesson_hits)


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
        if noext.endswith('/mod'):     # Rust: foo/mod.rs ≡ the module `foo`
            by_noext.setdefault(noext[:-len('/mod')], []).append(rp)
        base = noext.split('/')[-1]
        by_basename.setdefault(base, []).append(rp)
    for d in (by_noext, by_basename):
        for k in list(d.keys()):
            d[k] = sorted(set(d[k]), key=lambda r: (_ext_rank(r), len(r), r))
    # Tail-segment indexes: the distinct files under every key's last 1 and last 2
    # path segments. Lets _match_unique_suffix answer each candidate with one dict
    # hit instead of scanning every key — that scan made import resolution
    # O(files × imports) (quadratic-ish), the dominant cost on repos with
    # thousands of files (external imports paid it in full on every candidate).
    by_tail1, by_tail2 = {}, {}
    for k, files in by_noext.items():
        segs = k.split('/')
        by_tail1.setdefault(segs[-1], set()).update(files)
        if len(segs) >= 2:
            by_tail2.setdefault('/'.join(segs[-2:]), set()).update(files)
    tails = {1: {t: tuple(sorted(f, key=lambda r: (_ext_rank(r), len(r), r)))
                 for t, f in by_tail1.items()},
             2: {t: tuple(sorted(f, key=lambda r: (_ext_rank(r), len(r), r)))
                 for t, f in by_tail2.items()}}
    # Directory-package index: Go / Java / PHP imports name a PACKAGE DIRECTORY,
    # not a file. Map each dir to a deterministic representative file (prefer
    # <dir>/<dirname>.*, then mod/index/__init__/main/lib, then ext priority),
    # with tail-segment indexes over dir paths. Consulted only after every
    # file-based resolution step fails — strictly additive.
    children = {}
    for rp in rel_paths:
        if '/' in rp:
            d = rp.rsplit('/', 1)[0]
            children.setdefault(d, []).append(rp)

    def _rep_rank(d):
        dirname = d.split('/')[-1]

        def rank(rp):
            stem = re.sub(r'\.[^./]+$', '', rp.rsplit('/', 1)[-1])
            return (0 if stem == dirname else
                    1 if stem in ('mod', 'index', '__init__', 'main', 'lib') else 2,
                    _ext_rank(rp), len(rp), rp)
        return rank

    dir_rep = {d: min(kids, key=_rep_rank(d)) for d, kids in children.items()}
    dtail1, dtail2 = {}, {}
    for d in dir_rep:
        segs = d.split('/')
        dtail1.setdefault(segs[-1], set()).add(d)
        if len(segs) >= 2:
            dtail2.setdefault('/'.join(segs[-2:]), set()).add(d)
    dirtails = {1: {t: sorted(v) for t, v in dtail1.items()},
                2: {t: sorted(v) for t, v in dtail2.items()}}
    return {'noext': by_noext, 'basename': by_basename,
            'noext_keys': list(by_noext.keys()), 'rels': set(rel_paths),
            'tails': tails, 'dirs': dir_rep, 'dirtails': dirtails}


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

    Tries the last two path segments first (specific), then the last one.
    O(1) per candidate: reads the precomputed tail-segment indexes instead of
    scanning every key (which was quadratic-ish in project size)."""
    cand = cand.strip('/')
    if not cand:
        return None
    segs = cand.split('/')
    tails = idx['tails']
    for n in (2, 1):
        if len(segs) < n:
            continue
        tail = '/'.join(segs[-n:])
        # a bare, short basename is too ambiguous to match on alone
        if n == 1 and len(tail) < 4:
            continue
        files = tails[n].get(tail)
        if files and len(files) == 1:
            return files[0]
    return None


def resolve_import(importer_rel, target, idx, want_why=False):
    """Resolve one import string to a project-relative file path, or None if external.

    With want_why=True, returns (rel, provenance) where provenance is
    'extracted' (exact path/index match), 'ambiguous' (unique suffix/basename
    fallback — resolved but not certain), or None. Default returns just rel
    (back-compatible)."""
    def _r(rel, why):
        return (rel, why) if want_why else rel
    target = target.strip()
    if not target:
        return _r(None, None)

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

    cands = list(dict.fromkeys(cands))   # dedupe, order-preserving (first match wins)

    # 1) exact path match (highest confidence) → EXTRACTED
    for c in cands:
        hit = _match_exact(c, idx)
        if hit:
            return _r(hit, 'extracted')
    # 2) unique-suffix fallback — off-by-one relative depth, aliased/base roots → AMBIGUOUS
    for c in cands:
        hit = _match_unique_suffix(c, idx)
        if hit:
            return _r(hit, 'ambiguous')
    # 3) unique-basename fallback (only when globally unique → safe) → AMBIGUOUS
    base = re.sub(r'\.[^./]+$', '', target.replace('\\', '/').rstrip('/').split('/')[-1])
    bucket = idx['basename'].get(base)
    if bucket and len(bucket) == 1:
        return _r(bucket[0], 'ambiguous')
    # 4) directory-package fallback: Go / Java / PHP import a package DIRECTORY
    #    (e.g. "github.com/gin-gonic/gin/render" → render/). Exact dir path, then
    #    unique dir tail. Only reached when no file resolved — strictly additive.
    dirs = idx.get('dirs')
    if dirs:
        for c in cands:
            hit = dirs.get(c.strip('/'))
            if hit and hit != importer_rel:
                return _r(hit, 'extracted')
        dirtails = idx.get('dirtails') or {}
        for c in cands:
            segs = c.strip('/').split('/')
            for n in (2, 1):
                if len(segs) < n:
                    continue
                tail = '/'.join(segs[-n:])
                if n == 1 and len(tail) < 4:
                    continue
                ds = dirtails.get(n, {}).get(tail)
                if ds and len(ds) == 1:
                    hit = dirs[ds[0]]
                    if hit != importer_rel:
                        return _r(hit, 'ambiguous')
    return _r(None, None)


def resolve_with_hint(importer_rel, target, idx, lessons=None, want_why=False):
    """resolve_import, then — only if that failed AND a lesson supplies a matching
    resolver_hint — apply the hint. Importer-aware (scoped hints resolve the same
    relative import to different files per base dir) and value-flexible (the hint
    value is re-resolved for this importer, so it need not be the exact stored
    path). Centralized so build_graph and health.self_check agree on what counts
    as 'resolved' after a teach — otherwise a taught hint heals the edge but the
    self-check keeps nagging about the same import (the audit's complaint)."""
    rel, why = resolve_import(importer_rel, target, idx, want_why=True)
    if rel is None and lessons:
        from . import lessons as _l
        hint = _l.resolve_hint(target, lessons, importer_rel=importer_rel)
        if hint:
            if hint in idx.get('rels', ()):          # exact stored project path
                rel, why = hint, 'inferred'
            else:                                    # re-resolve the hint for this importer
                rr = resolve_import(importer_rel, hint, idx)
                if rr is not None:
                    rel, why = rr, 'inferred'
    return (rel, why) if want_why else rel


def build_graph(root, ignore_dirs=None, respect_gitignore=True, max_file_kb=512,
                reference_segments=None, cache=None, diagnostics=None, jobs=1):
    """Scan `root` and return (nodes, edges, file_texts).

    nodes:      list of {id, label, rel, category, loc, tier, kind}
    edges:      list of {source, target, kind}  (file-id -> file-id)
    file_texts: {rel: text}  (cached so detectors don't re-read)

    cache:       optional {rel: {mtime,size,ast,imports}} parse cache, mutated in
                 place — unchanged files skip re-parsing (results are identical).
    diagnostics: optional dict; records skipped/oversized/read-error files and
                 cache hit/parse counts so nothing is dropped silently.
    jobs:        thread count for the file-READ pass (I/O-bound). >1 speeds up large
                 trees; output is byte-identical to jobs=1 (all downstream work
                 iterates files in deterministic order). Default 1.
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

    # Phase 1 — read contents (optionally parallel; reads are I/O-bound). Stored by
    # rel; every step after this iterates `files` in order, so jobs never changes
    # the output, only the wall-clock of the read.
    def _read(item):
        abs_path, rel = item
        try:
            return rel, Path(abs_path).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return rel, None
    texts_by_rel = {}
    if jobs and jobs > 1 and len(files) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            for rel, text in ex.map(_read, files):
                texts_by_rel[rel] = text
    else:
        for item in files:
            rel, text = _read(item)
            texts_by_rel[rel] = text

    file_texts = {}
    raw_imports = {}
    for abs_path, rel in files:
        text = texts_by_rel.get(rel)
        if text is None:
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
        cat = categorize(rel)
        if cat == 'other' and _LESSONS:          # let a lesson name a taught language's category
            from . import lessons as _l
            cat = _l.category_for(rel, _LESSONS) or cat
        nodes.append({
            'id': id_of[rel],
            'label': rel.split('/')[-1],
            'rel': rel,
            'category': cat,
            'loc': loc,
            'tier': tier_of(rel, reference_segments),
            'kind': 'code',
        })

    seen = set()
    edges = []
    for rel in rel_paths:
        src_id = id_of[rel]
        for target in raw_imports[rel]:
            resolved, why = resolve_with_hint(rel, target, idx, lessons=_LESSONS, want_why=True)
            if resolved and resolved != rel:
                key = (src_id, id_of[resolved])
                if key not in seen:
                    seen.add(key)
                    edges.append({'source': src_id, 'target': id_of[resolved],
                                  'kind': 'import', 'prov': why or 'extracted'})

    return nodes, edges, file_texts
