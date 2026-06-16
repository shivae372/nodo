"""
Nodo CLI.

    python -m nodo [PATH] [options]

Scans a project, builds the dependency graph, detects issues, and writes the
interactive viewer + AI artifacts.
"""
import argparse
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from . import scanner
from . import cache as _cache
from .scanner import build_graph, discover_docs, discover_assets
from .clustering import detect_communities, community_summaries
from .detectors import detect_all
from .config import load_config, write_sample_config
from .render import render
from .query import query_file, path_between, explain_concept
from .symbols import query_symbol
from .assets import link_assets, convert_assets
from .hookinstall import emit_context, install_hook, install_agents, install_mcp
from .insights import entry_flows, sensitive_map, api_routes


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='nodo',
        description='Map any codebase: dependency graph + issue detection + AI-agent artifacts. Zero dependencies.',
    )
    parser.add_argument('path', nargs='?', default='.',
                        help='Project root to scan (default: current directory)')
    parser.add_argument('-o', '--out', default=None,
                        help='Output directory (default: <path>/.nodo)')
    parser.add_argument('--name', default=None,
                        help='Project name shown in the viewer (default: folder name)')
    parser.add_argument('--open', action='store_true',
                        help='Open the generated HTML in your browser when done')
    parser.add_argument('--init', action='store_true',
                        help='Write a sample .nodo.json config file and exit')
    parser.add_argument('--self-check', '--doctor', dest='self_check', action='store_true',
                        help="Report what nodo does NOT understand — unknown languages, files it "
                             "parsed but pulled nothing from, unresolved local imports — so you "
                             "(Claude) know exactly what to teach it. Then exit.")
    parser.add_argument('--teach', metavar='LESSON_JSON', default=None,
                        help="Ingest a lesson (a JSON file: languages with def/import regex, "
                             "keep_alive corrections, resolver_hints), persist it to "
                             ".nodo/lessons.json, and exit. How Claude tutors nodo on a new "
                             "language or a confirmed false positive — it sticks across scans.")
    parser.add_argument('--query', metavar='FILE', default=None,
                        help="Print one file's blast radius (dependents, dependencies, "
                             "issues) from the existing map and exit. Token-cheap; for AI agents.")
    parser.add_argument('--path', nargs=2, metavar=('FILE_A', 'FILE_B'), default=None,
                        dest='path_pair',
                        help="Show the import chain connecting two files (how does A reach B). "
                             "Token-cheap; for AI agents.")
    parser.add_argument('--explain', metavar='CONCEPT', default=None,
                        help="Find the files most related to a concept (e.g. 'authentication', "
                             "'billing'). Lexical search over paths + content. For AI agents.")
    parser.add_argument('--topics', action='store_true',
                        help="Print the knowledge-graph topics (communities of docs/PDFs) "
                             "and exit. Add --full to include PDFs.")
    parser.add_argument('--ask', metavar='QUESTION', default=None,
                        help="Ask a natural-language question — nodo routes it to blast-radius, "
                             "import-path, symbol, concept search, or topics. One command for "
                             "every query.")
    parser.add_argument('--hook', action='store_true',
                        help="Install a Claude Code SessionStart hook so agents auto-load "
                             "the architecture map at session start. Then exit.")
    parser.add_argument('--install', action='store_true',
                        help="Wire the map into multiple AI assistants: Claude Code hook + "
                             "Cursor rule + AGENTS.md (Codex/Windsurf/…). Then exit.")
    parser.add_argument('--emit-context', action='store_true',
                        help="Print the SessionStart JSON context envelope and exit "
                             "(this is what the installed hook runs).")
    parser.add_argument('--include-vendor', action='store_true',
                        help='Also analyse reference/vendored/example dirs (off by default '
                             'so third-party noise never drowns your own code).')
    parser.add_argument('--ast', action='store_true',
                        help='Force tree-sitter parsing (prints a note + uses regex if the '
                             'grammar is not installed). AST is used automatically when '
                             'available; this flag just makes the requirement explicit.')
    parser.add_argument('--no-ast', action='store_true',
                        help='Force the zero-dependency regex extractor even if tree-sitter '
                             'is installed.')
    parser.add_argument('--no-cache', action='store_true',
                        help='Disable the incremental parse cache (.nodo/cache.json).')
    parser.add_argument('--full', action='store_true',
                        help='Deepest scan: shortcut for --ast --multimodal.')
    parser.add_argument('--benchmark', action='store_true',
                        help='Compare regex vs tree-sitter parsing (timing + edges) and exit.')
    parser.add_argument('--mcp', action='store_true',
                        help='Run nodo as an MCP server (stdio) — exposes its query tools to '
                             'agents mid-session. Needs: pip install mcp')
    parser.add_argument('--multimodal', action='store_true',
                        help='Include images / PDFs / video as assets linked to the nodes near '
                             'them (contents are read by the Claude skill, not nodo).')
    parser.add_argument('--docs-only', action='store_true',
                        help='Index documentation text but skip the multimodal asset pass.')
    parser.add_argument('--no-gitignore', action='store_true',
                        help='Do not read .gitignore for extra ignore dirs')
    parser.add_argument('--ignore', action='append', default=[],
                        help='Extra directory name to ignore (repeatable)')
    parser.add_argument('--version', action='version', version=f'nodo {__version__}')
    args = parser.parse_args(argv)

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f'error: {root} is not a directory', file=sys.stderr)
        return 2

    if args.full:                 # --full == --ast --multimodal
        args.ast = True
        args.multimodal = True

    # Parser selection: use tree-sitter automatically when importable (best
    # accuracy), else the zero-dep regex extractor. --no-ast forces regex;
    # --ast makes the tree-sitter requirement explicit.
    if not args.no_ast:
        from . import ast_index
        if ast_index.available():
            scanner.enable_ast()
        elif args.ast:
            print('[nodo] --ast: tree-sitter not installed; using the zero-dep regex '
                  'extractor. Install with: pip install tree-sitter tree-sitter-language-pack',
                  file=sys.stderr)

    if args.init:
        if write_sample_config(root):
            print(f'Wrote sample config: {root / ".nodo.json"}')
        else:
            print(f'Config already exists: {root / ".nodo.json"}')
        return 0

    cfg = load_config(root)
    project_name = args.name or cfg.get('project_name') or root.name
    out_dir = Path(args.out) if args.out else (root / '.nodo')

    # Self-learning: load any lessons Claude has taught and apply them to every
    # path below (scan / query / ask / self-check). nodo stays offline — lessons
    # are local data, never an LLM call.
    from . import lessons as _lessons_mod
    learned = _lessons_mod.load_lessons(out_dir)
    if _lessons_mod.has_content(learned):
        scanner.enable_lessons(learned)

    if args.teach:
        teach_path = Path(args.teach)
        if not teach_path.exists():
            print(f'error: lesson file not found: {teach_path}', file=sys.stderr)
            return 2
        import json as _json
        try:
            obj = _json.loads(teach_path.read_text(encoding='utf-8', errors='ignore'))
        except Exception as e:
            print(f'error: {teach_path} is not valid JSON: {e}', file=sys.stderr)
            return 2
        ok, errors, summary = _lessons_mod.merge_lessons(out_dir, obj)
        if not ok:
            print('Lesson rejected — fix these and re-teach:', file=sys.stderr)
            for er in errors:
                print(f'  - {er}', file=sys.stderr)
            return 1
        print(f'Taught nodo. Saved to {out_dir / _lessons_mod.LESSONS_NAME}')
        if summary.get('languages_added'):
            print(f"  + new language(s): {', '.join(summary['languages_added'])}")
        if summary.get('languages_updated'):
            print(f"  ~ updated language(s): {', '.join(summary['languages_updated'])}")
        if summary.get('keep_alive_added'):
            print(f"  + keep-alive: {', '.join(summary['keep_alive_added'])}")
        if summary.get('resolver_hints_added'):
            print(f"  + resolver hint(s): {', '.join(summary['resolver_hints_added'])}")
        if summary.get('extensions_now_understood'):
            print(f"  nodo now understands: {', '.join(summary['extensions_now_understood'])}")
        print('  Re-scan (nodo .) and the lesson is applied — nodo healed.')
        return 0

    if args.benchmark:
        return _run_benchmark(root, out_dir, cfg, args)

    if args.mcp:
        from . import serve as _serve
        return _serve.serve(str(root), str(out_dir))

    if args.emit_context:
        # invoked by the Claude Code hook — print JSON envelope, nothing else.
        emit_context(out_dir)
        return 0

    if args.hook:
        # launcher path = the nodo.py at the repo root (parent of this package)
        launcher = Path(__file__).resolve().parent.parent / 'nodo.py'
        print(install_hook(root, launcher))
        return 0

    if args.install:
        launcher = Path(__file__).resolve().parent.parent / 'nodo.py'
        print(install_hook(root, launcher))
        print(install_agents(root, launcher))
        print(install_mcp(root, launcher))
        return 0

    if args.query:
        needle = args.query
        try:
            from . import querylog
            querylog.record(out_dir, 'query', needle)
        except Exception:
            pass
        # cheap path: existing map + the needle names a file → blast radius
        if (out_dir / 'nodo-context.json').exists():
            file_res = query_file(out_dir, needle)
            if file_res and not file_res.startswith('No file matching'):
                print(file_res)
                return 0
        # otherwise treat it as a SYMBOL (definition + references) — needs a scan
        nodes, edges, file_texts = build_graph(
            root, ignore_dirs=_ignore_dirs(cfg, args, out_dir, root),
            respect_gitignore=not args.no_gitignore,
            max_file_kb=cfg.get('max_file_kb', 512))
        sym = query_symbol(nodes, file_texts, needle)
        if sym is not None:
            print(sym)
            return 0
        # neither a file nor a known symbol: build the map and answer as a file query
        if not (out_dir / 'nodo-context.json').exists():
            if _run_scan(root, out_dir, project_name, cfg, args, quiet=True) is None:
                return 1
        print(query_file(out_dir, needle))
        return 0

    if args.path_pair:
        if not (out_dir / 'nodo-context.json').exists():
            print(f'No map yet — scanning {root} once ...', file=sys.stderr)
            if _run_scan(root, out_dir, project_name, cfg, args, quiet=True) is None:
                return 1
        print(path_between(out_dir, args.path_pair[0], args.path_pair[1]))
        return 0

    if args.explain:
        # needs file content for BM25 body ranking — do a quiet scan to get it,
        # which also refreshes the map. Design docs are folded in so the concept's
        # spec surfaces alongside the code that implements it.
        ignore_dirs = _ignore_dirs(cfg, args, out_dir, root)
        _nodes, _edges, file_texts = build_graph(
            root, ignore_dirs=ignore_dirs,
            respect_gitignore=not args.no_gitignore,
            max_file_kb=cfg.get('max_file_kb', 512))
        docs = discover_docs(root, ignore_dirs)
        if not (out_dir / 'nodo-context.json').exists():
            if _run_scan(root, out_dir, project_name, cfg, args, quiet=True) is None:
                return 1
        print(explain_concept(out_dir, args.explain, file_texts=file_texts, docs=docs))
        return 0

    if args.ask:
        ignore_dirs = _ignore_dirs(cfg, args, out_dir, root)
        nodes, edges, file_texts = build_graph(
            root, ignore_dirs=ignore_dirs, respect_gitignore=not args.no_gitignore,
            max_file_kb=cfg.get('max_file_kb', 512))
        docs = discover_docs(root, ignore_dirs)
        if not (out_dir / 'nodo-context.json').exists():
            if _run_scan(root, out_dir, project_name, cfg, args, quiet=True) is None:
                return 1
        from . import ask as _ask
        print(_ask.answer(args.ask, nodes, edges, file_texts, out_dir, docs=docs))
        try:
            from . import querylog
            querylog.record(out_dir, 'ask', args.ask)
        except Exception:
            pass
        return 0

    if args.topics:
        # (re)build the map so the knowledge graph is fresh, then print topics
        if _run_scan(root, out_dir, project_name, cfg, args, quiet=True) is None:
            return 1
        import json as _json
        try:
            ctx = _json.loads((out_dir / 'nodo-context.json').read_text(encoding='utf-8', errors='ignore'))
        except Exception:
            ctx = {}
        topics = ctx.get('knowledge', {}).get('topics', [])
        if not topics:
            print('No doc/PDF topics found. Add docs, or run with --full to include PDFs.')
            return 0
        print(f'Knowledge topics ({len(topics)}) — communities of docs/PDFs:')
        for t in topics:
            cs = ', '.join(t['concepts'][:6])
            ds = ', '.join(d.split('/')[-1] for d in t['docs'][:4])
            print(f'  • {t["name"]}: {cs}' + (f'   [{ds}]' if ds else ''))
        print('\nAsk the Claude skill to answer questions semantically over these topics.')
        return 0

    if args.self_check:
        ignore_dirs = _ignore_dirs(cfg, args, out_dir, root)
        nodes, edges, file_texts = build_graph(
            root, ignore_dirs=ignore_dirs, respect_gitignore=not args.no_gitignore,
            max_file_kb=cfg.get('max_file_kb', 512))
        from . import health
        hc = health.self_check(str(root), nodes, edges, file_texts, scanner._LESSONS, ignore_dirs)
        print(hc['report'])
        if hc['teach_template']:
            import json as _json
            print('\nStarter lesson (fill the regexes, save as lesson.json, then '
                  '`nodo . --teach lesson.json`):')
            print(_json.dumps(hc['teach_template'], indent=2))
        return 0

    result = _run_scan(root, out_dir, project_name, cfg, args)
    if result is None:
        return 1

    if args.open:
        webbrowser.open(Path(result['html']).resolve().as_uri())

    return 0


def _ignore_dirs(cfg, args, out_dir, root=None):
    """The full set of directory names to skip — defaults + config + CLI + the
    output dir + .gitignore — so discover_files/docs/assets all prune identically."""
    ig = set(scanner.DEFAULT_IGNORE_DIRS) | set(cfg.get('ignore_dirs', [])) | set(args.ignore)
    ig.add(out_dir.name)
    if root is not None and not args.no_gitignore:
        ig |= scanner.load_gitignore(Path(root))
    return ig


def _resolve_multimodal(args):
    """Decide whether to run the multimodal asset pass: explicit flags win; if
    neither is given and we're attached to a terminal, ask; otherwise default to
    docs-only (no heavy work, no prompt) so scripted/agent runs stay predictable."""
    if getattr(args, 'multimodal', False):
        return True
    if getattr(args, 'docs_only', False):
        return False
    try:
        if sys.stdin.isatty() and sys.stdout.isatty():
            ans = input('Include images / PDFs / video in the map (multimodal)? [y/N] ').strip().lower()
            return ans in ('y', 'yes')
    except Exception:
        pass
    return False


def _run_benchmark(root, out_dir, cfg, args):
    """Scan with regex and (if installed) tree-sitter; report timing + edge counts."""
    from . import ast_index
    ig = _ignore_dirs(cfg, args, out_dir, root)
    mfk = cfg.get('max_file_kb', 512)
    print(f'nodo {__version__} — parser benchmark on {root}')
    print(f'  {"parser":<12}{"files":>8}{"edges":>8}{"time":>9}')
    scanner._USE_AST = False
    t0 = time.time()
    n, e, _ = build_graph(root, ignore_dirs=ig, respect_gitignore=not args.no_gitignore, max_file_kb=mfk)
    dt = time.time() - t0
    print(f'  {"regex":<12}{len(n):>8}{len(e):>8}{dt:>8.2f}s')
    if ast_index.available():
        scanner.enable_ast()
        t0 = time.time()
        n2, e2, _ = build_graph(root, ignore_dirs=ig, respect_gitignore=not args.no_gitignore, max_file_kb=mfk)
        dt2 = time.time() - t0
        print(f'  {"tree-sitter":<12}{len(n2):>8}{len(e2):>8}{dt2:>8.2f}s')
        ratio = f', tree-sitter took {dt2 / dt:.1f}x the regex time' if dt > 0 else ''
        print(f'  delta: {len(e2) - len(e):+d} edges{ratio}')
        scanner._USE_AST = False
    else:
        print('  tree-sitter not installed — '
              'pip install tree-sitter tree-sitter-language-pack')
    return 0


def _run_scan(root, out_dir, project_name, cfg, args, quiet=False):
    """Scan, detect, render. Returns the render result dict, or None if no files."""
    ignore_dirs = _ignore_dirs(cfg, args, out_dir, root)

    t0 = time.time()
    if not quiet:
        print(f'nodo {__version__} — scanning {root} ...')
    cache_data = None if args.no_cache else _cache.load(out_dir)
    old_hashes = {rel: e.get('hash') for rel, e in (cache_data or {}).items()}
    diag = {}
    nodes, edges, file_texts = build_graph(
        root,
        ignore_dirs=ignore_dirs,
        respect_gitignore=not args.no_gitignore,
        max_file_kb=cfg.get('max_file_kb', 512),
        cache=cache_data,
        diagnostics=diag,
    )
    # personalization: what changed since your last scan (uses the content-hash cache)
    if cache_data is not None and old_hashes:
        cur = set(file_texts)
        diag['changed'] = sorted(r for r in cur if r in old_hashes
                                 and cache_data.get(r, {}).get('hash') != old_hashes[r])
        diag['added'] = sorted(r for r in cur if r not in old_hashes)
    if not args.no_cache and cache_data is not None:
        _cache.save(out_dir, cache_data)
    if not nodes:
        print('No source files found. Is this the right directory?', file=sys.stderr)
        return None
    if not quiet:
        parser_label = 'tree-sitter' if scanner._USE_AST else 'regex'
        print(f'  {len(nodes)} files, {len(edges)} dependencies  [parser: {parser_label}]')
        if len(nodes) > 5000:
            print(f'  note: large repo ({len(nodes)} files) — the first scan does the work; '
                  f'rescans reuse the cache. Use --no-ast for a faster (less precise) pass.')
        ch, pa = diag.get('cache_hits', 0), diag.get('parsed', 0)
        if ch:
            print(f'  cache: {ch} reused, {pa} parsed')
        nch, nad = len(diag.get('changed', [])), len(diag.get('added', []))
        if nch or nad:
            print(f'  since last scan: {nch} changed, {nad} new')
        nsl = len(diag.get('skipped_large', []))
        nre = len(diag.get('read_errors', []))
        if nsl or nre:
            bits = []
            if nsl:
                bits.append(f'{nsl} skipped (>{cfg.get("max_file_kb", 512)}KB)')
            if nre:
                bits.append(f'{nre} unreadable')
            print('  note: ' + ', '.join(bits))

    communities = detect_communities(len(nodes), edges)
    comm_sum = community_summaries(communities, nodes)

    keep_alive = None
    if scanner._LESSONS:
        from . import lessons as _lz
        keep_alive = _lz.keep_alive_set(scanner._LESSONS) or None
    issues = detect_all(nodes, edges, file_texts, custom_rules=cfg.get('custom_rules'),
                        include_reference=getattr(args, 'include_vendor', False),
                        keep_alive=keep_alive)
    n_e = sum(1 for i in issues if i['severity'] == 'error')
    n_w = sum(1 for i in issues if i['severity'] == 'warn')
    n_i = sum(1 for i in issues if i['severity'] == 'info')
    if not quiet:
        print(f'  {len(issues)} issues ({n_e} errors, {n_w} warnings, {n_i} info)')

    # self-healing nudge: surface what nodo can't parse so Claude can teach it.
    # Recorded in diagnostics (→ context.json) and summarized once on the console.
    try:
        if len(nodes) <= 4000:
            from . import health
            hc = health.self_check(str(root), nodes, edges, file_texts, scanner._LESSONS, ignore_dirs)
            if hc['gaps']:
                diag['learning_gaps'] = [{k: v for k, v in g.items() if k != 'detail'}
                                         for g in hc['gaps']]
                if not quiet:
                    kinds = {}
                    for g in hc['gaps']:
                        kinds[g['kind']] = kinds.get(g['kind'], 0) + 1
                    label = {'unknown_language': 'unknown language(s)',
                             'silent_extraction': 'silent file(s)',
                             'unresolved_local': 'unresolved-import file(s)'}
                    bits = [f'{n} {label[k]}' for k, n in sorted(kinds.items()) if k in label]
                    if bits:
                        print(f"  self-check: {', '.join(bits)} — run "
                              f"`nodo . --self-check` to see what to teach nodo")
    except Exception:
        pass

    # derived insights — auto-generated flows + sensitive surfaces + API ref
    flows = entry_flows(nodes, edges)
    sensitive = sensitive_map(nodes, file_texts)
    apis = api_routes(nodes, file_texts)

    # design docs (always indexed — cheap) + optional multimodal asset linking
    docs = discover_docs(root, ignore_dirs)
    assets = []
    doc_texts = dict(docs)                       # corpus for the knowledge graph
    do_multimodal = args.multimodal or args.full or (not quiet and _resolve_multimodal(args))
    if do_multimodal:
        raw = discover_assets(root, ignore_dirs)
        assets = link_assets(root, raw, nodes, docs,
                             include_reference=getattr(args, 'include_vendor', False))
        # Convert PDFs/Office/HTML/images → Markdown (saved under .nodo/converted/),
        # pin the converted path onto each asset, and fold the text into the
        # knowledge corpus. Claude reads the cheap .md instead of the raw file.
        n_conv = convert_assets(root, out_dir, assets, doc_texts)
        if not quiet:
            extra = f', {n_conv} converted → Markdown (token-cheap)' if n_conv else ''
            print(f'  {len(docs)} docs indexed, {len(assets)} assets linked{extra}')
    elif not quiet and docs:
        print(f'  {len(docs)} docs indexed')

    # Knowledge graph: mine concepts + topic communities from doc/PDF text.
    from . import knowledge as _knowledge
    know = _knowledge.build_knowledge(doc_texts)
    if not quiet and know.get('topics'):
        print(f'  knowledge: {len(know["topics"])} topic(s), '
              f'{len(know["concepts"])} concept(s) from {len(doc_texts)} doc(s)/PDF(s)')

    # Unify the graph: add doc + asset + concept nodes and reference edges so
    # everything is connected in the rendered graph / context.json. Detectors above
    # already ran on the code-only graph, so this never affects structural analysis.
    from . import graphmerge
    u_nodes, u_edges, u_comm = graphmerge.integrate(
        nodes, edges, communities, docs, assets, str(root), knowledge=know)

    result = render(
        out_dir=out_dir,
        project_name=project_name,
        abs_root=str(root).replace('\\', '/'),
        nodes=u_nodes, edges=u_edges, communities=u_comm,
        comm_summaries=comm_sum, issues=issues,
        community_names=cfg.get('community_names'),
        flows=flows, sensitive=sensitive, apis=apis,
        docs=docs, assets=assets, diagnostics=diag,
        parser=('tree-sitter' if scanner._USE_AST else 'regex'),
        knowledge=know,
    )

    if not quiet:
        dt = time.time() - t0
        print(f'\nDone in {dt:.1f}s. Output in {out_dir}/')
        print(f'  - {Path(result["html"]).name:22} interactive viewer (open in a browser)')
        print(f'  - {Path(result["json"]).name:22} machine-readable graph + issues (for AI agents)')
        print(f'  - {Path(result["md"]).name:22} token-cheap summary')
        print(f'  - {Path(result["txt"]).name:22} plain-text issue list')
        print(f'  - {"nodo-report.md":22} readable architecture report')
    return result


if __name__ == '__main__':
    sys.exit(main())
