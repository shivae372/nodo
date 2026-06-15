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
from .scanner import build_graph, discover_docs, discover_assets
from .clustering import detect_communities, community_summaries
from .detectors import detect_all
from .config import load_config, write_sample_config
from .render import render
from .query import query_file, path_between, explain_concept
from .symbols import query_symbol
from .assets import link_assets, attach_pdf_text
from .hookinstall import emit_context, install_hook
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
    parser.add_argument('--hook', action='store_true',
                        help="Install a Claude Code SessionStart hook so agents auto-load "
                             "the architecture map at session start. Then exit.")
    parser.add_argument('--emit-context', action='store_true',
                        help="Print the SessionStart JSON context envelope and exit "
                             "(this is what the installed hook runs).")
    parser.add_argument('--include-vendor', action='store_true',
                        help='Also analyse reference/vendored/example dirs (off by default '
                             'so third-party noise never drowns your own code).')
    parser.add_argument('--ast', action='store_true',
                        help='EXPERIMENTAL: use tree-sitter for import/symbol extraction when '
                             'installed (pip install tree-sitter tree-sitter-languages). '
                             'Falls back to the zero-dep regex extractor if absent.')
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

    if args.ast:
        from . import ast_index
        scanner.enable_ast()
        if not ast_index.available():
            print('[nodo] --ast: tree-sitter not installed; using the zero-dep regex '
                  'extractor. Install with: pip install tree-sitter tree-sitter-languages',
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

    if args.emit_context:
        # invoked by the Claude Code hook — print JSON envelope, nothing else.
        emit_context(out_dir)
        return 0

    if args.hook:
        # launcher path = the nodo.py at the repo root (parent of this package)
        launcher = Path(__file__).resolve().parent.parent / 'nodo.py'
        print(install_hook(root, launcher))
        return 0

    if args.query:
        needle = args.query
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


def _run_scan(root, out_dir, project_name, cfg, args, quiet=False):
    """Scan, detect, render. Returns the render result dict, or None if no files."""
    ignore_dirs = _ignore_dirs(cfg, args, out_dir, root)

    t0 = time.time()
    if not quiet:
        print(f'nodo {__version__} — scanning {root} ...')
    nodes, edges, file_texts = build_graph(
        root,
        ignore_dirs=ignore_dirs,
        respect_gitignore=not args.no_gitignore,
        max_file_kb=cfg.get('max_file_kb', 512),
    )
    if not nodes:
        print('No source files found. Is this the right directory?', file=sys.stderr)
        return None
    if not quiet:
        print(f'  {len(nodes)} files, {len(edges)} dependencies')

    communities = detect_communities(len(nodes), edges)
    comm_sum = community_summaries(communities, nodes)

    issues = detect_all(nodes, edges, file_texts, custom_rules=cfg.get('custom_rules'),
                        include_reference=getattr(args, 'include_vendor', False))
    n_e = sum(1 for i in issues if i['severity'] == 'error')
    n_w = sum(1 for i in issues if i['severity'] == 'warn')
    n_i = sum(1 for i in issues if i['severity'] == 'info')
    if not quiet:
        print(f'  {len(issues)} issues ({n_e} errors, {n_w} warnings, {n_i} info)')

    # derived insights — auto-generated flows + sensitive surfaces + API ref
    flows = entry_flows(nodes, edges)
    sensitive = sensitive_map(nodes, file_texts)
    apis = api_routes(nodes, file_texts)

    # design docs (always indexed — cheap) + optional multimodal asset linking
    docs = discover_docs(root, ignore_dirs)
    assets = []
    if not quiet and _resolve_multimodal(args):
        raw = discover_assets(root, ignore_dirs)
        assets = link_assets(root, raw, nodes, docs,
                             include_reference=getattr(args, 'include_vendor', False))
        assets, n_pdf = attach_pdf_text(root, assets)
        extra = f', {n_pdf} PDF(s) text-extracted' if n_pdf else ''
        print(f'  {len(docs)} docs indexed, {len(assets)} assets linked to nodes{extra}')
    elif not quiet and docs:
        print(f'  {len(docs)} docs indexed')

    # Unify the graph: add doc + asset nodes and reference edges so everything is
    # connected in the rendered graph / context.json. Detectors above already ran
    # on the code-only graph, so this never affects structural analysis.
    from . import graphmerge
    u_nodes, u_edges, u_comm = graphmerge.integrate(
        nodes, edges, communities, docs, assets, str(root))

    result = render(
        out_dir=out_dir,
        project_name=project_name,
        abs_root=str(root).replace('\\', '/'),
        nodes=u_nodes, edges=u_edges, communities=u_comm,
        comm_summaries=comm_sum, issues=issues,
        community_names=cfg.get('community_names'),
        flows=flows, sensitive=sensitive, apis=apis,
        docs=docs, assets=assets,
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
