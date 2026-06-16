"""
Optional MCP (Model Context Protocol) stdio server.

Exposes nodo's query engine as LIVE TOOLS an agent (Claude Code, Cursor, …) can
call mid-session — `nodo_ask`, `nodo_blast_radius`, `nodo_who_uses`, `nodo_path`,
`nodo_explain`, `nodo_list_issues`, `nodo_hubs`, `nodo_topics`, `nodo_overview`,
`nodo_refresh` — instead of only reading the static context files at session start.

Opt-in and zero-dep-safe: needs `pip install mcp` (Python 3.10+); if it's absent
nodo prints the install command and the core is completely unaffected. The tools
are thin wrappers over the existing, tested logic (ask / query / symbols / docs) —
no new analysis, read-only, local, no network.

    python -m nodo.serve [PATH]        # run the stdio server
    python /path/to/nodo/nodo.py --mcp [PATH]
"""
import argparse
import sys
from pathlib import Path

from . import scanner
from . import ask as _ask
from . import query as _query
from . import symbols as _symbols
from .config import load_config


class _State:
    """Holds the scanned graph for the server's lifetime; refreshes on demand."""

    def __init__(self, root, out_dir=None):
        self.root = Path(root).resolve()
        self.out_dir = Path(out_dir) if out_dir else (self.root / '.nodo')
        self.nodes = None
        self.edges = None
        self.file_texts = None
        self.docs = {}

    def refresh(self):
        from . import __main__ as cli
        cfg = load_config(self.root)
        args = argparse.Namespace(
            no_gitignore=False, no_cache=False, ignore=[], include_vendor=False,
            multimodal=False, full=False, docs_only=True, ast=False, no_ast=False,
            name=None, out=str(self.out_dir))
        try:
            from . import ast_index
            if ast_index.available():
                scanner.enable_ast()
        except Exception:
            pass
        # apply any lessons Claude has taught (learned languages / corrections)
        try:
            from . import lessons as _lz
            L = _lz.load_lessons(self.out_dir)
            if _lz.has_content(L):
                scanner.enable_lessons(L)
        except Exception:
            pass
        cli._run_scan(self.root, self.out_dir, self.root.name, cfg, args, quiet=True)
        ignore = cli._ignore_dirs(cfg, args, self.out_dir, self.root)
        self.nodes, self.edges, self.file_texts = scanner.build_graph(
            self.root, ignore_dirs=ignore, respect_gitignore=True,
            max_file_kb=cfg.get('max_file_kb', 512))
        self.docs = scanner.discover_docs(self.root, ignore)

    def _ready(self):
        if self.nodes is None:
            self.refresh()

    # ── tool handlers (pure: reuse existing logic, return text) ──
    def ask(self, question):
        self._ready()
        return _ask.answer(question or '', self.nodes, self.edges, self.file_texts,
                           str(self.out_dir), self.docs)

    def blast_radius(self, file):
        self._ready()
        return _query.query_file(str(self.out_dir), file or '')

    def who_uses(self, symbol):
        self._ready()
        return _symbols.query_symbol(self.nodes, self.file_texts, symbol or '') \
            or f"No symbol matching '{symbol}'."

    def path(self, a, b):
        self._ready()
        return _query.path_between(str(self.out_dir), a or '', b or '')

    def explain(self, concept):
        self._ready()
        return _query.explain_concept(str(self.out_dir), concept or '',
                                      file_texts=self.file_texts, docs=self.docs)

    def list_issues(self, file=None):
        self._ready()
        return _ask._issues_answer(_ask._ctx(self.out_dir), file or None)

    def hubs(self):
        self._ready()
        return _ask._hubs_answer(_ask._ctx(self.out_dir))

    def topics(self):
        self._ready()
        return _ask._topics_answer(_ask._ctx(self.out_dir))

    def overview(self):
        self._ready()
        return _ask._overview_answer(_ask._ctx(self.out_dir), self.nodes)

    def self_check(self):
        self._ready()
        from . import health, lessons as _lz
        from . import __main__ as cli
        cfg = load_config(self.root)
        args = argparse.Namespace(ignore=[], no_gitignore=False)
        ignore = cli._ignore_dirs(cfg, args, self.out_dir, self.root)
        hc = health.self_check(str(self.root), self.nodes, self.edges, self.file_texts,
                               _lz.load_lessons(self.out_dir), ignore)
        out = hc['report']
        if hc['teach_template']:
            import json
            induced = (hc.get('draft_stats') or {}).get('induced')
            label = ('Auto-drafted lesson (induced from your files — review, then call '
                     'nodo_teach with it):' if induced else
                     'Starter lesson (fill the regexes, then call nodo_teach with it):')
            out += '\n\n' + label + '\n' + json.dumps(hc['teach_template'], indent=2)
        return out

    def fix_context(self, file):
        """Emit the structured <context> prompt for a file's issues — EVIDENCE for
        the agent to act on. nodo gathers; Claude writes the fix (never nodo)."""
        self._ready()
        ctx = _ask._ctx(self.out_dir)
        if not file:
            return "Pass a file path."
        issues = [i for i in ctx.get('issues', []) if i.get('file') == file]
        if not issues:
            cand = [i for i in ctx.get('issues', []) if i.get('file', '').endswith(file)]
            if cand:
                file = cand[0]['file']
                issues = [i for i in ctx.get('issues', []) if i.get('file') == file]
        if not issues:
            return f"No issues recorded for {file}."
        rel_to_id = {n['rel']: n['id'] for n in self.nodes}
        id_to_rel = {n['id']: n['rel'] for n in self.nodes}
        deps, fid = [], rel_to_id.get(file)
        if fid is not None:
            deps = sorted(id_to_rel[e['target']] for e in self.edges
                          if e.get('source') == fid and e.get('kind', 'import') == 'import')
        proj = ctx.get('project') or ctx.get('name') or self.root.name
        out = [f'<context project="{proj}">', f'  <file path="{file}" />', '  <issues>']
        for i in issues[:12]:
            ln = f':L{i["line"]}' if i.get('line') else ''
            out.append(f'    [{i.get("confidence", "?")}/{i["severity"]}] {i["type"]}{ln} — '
                       f'{i.get("detail", "")}')
        out.append('  </issues>')
        if deps:
            out.append('  <dependencies>' + ', '.join(d.split('/')[-1] for d in deps[:12])
                       + '</dependencies>')
        out.append('</context>')
        out.append("<task>Fix these issues. Match the existing patterns and don't break "
                   "unrelated code.</task>")
        return '\n'.join(out)

    def changed(self):
        """Files changed since the last scan + their combined transitive blast
        radius — 'what did my recent edits put at risk?'"""
        self._ready()
        ctx = _ask._ctx(self.out_dir)
        diag = ctx.get('diagnostics', {})
        changed = sorted(set(diag.get('changed', [])) | set(diag.get('added', [])))
        if not changed:
            return "No files changed since the last scan."
        rel_to_id = {n['rel']: n['id'] for n in self.nodes}
        id_to_rel = {n['id']: n['rel'] for n in self.nodes}
        rev = {}
        for e in self.edges:
            if e.get('kind', 'import') == 'import':
                rev.setdefault(e['target'], []).append(e['source'])
        seen, stack = set(), [rel_to_id[r] for r in changed if r in rel_to_id]
        while stack:
            cur = stack.pop()
            for dep in rev.get(cur, []):
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        impacted = sorted(id_to_rel[i] for i in seen
                          if id_to_rel.get(i) and id_to_rel[i] not in changed)
        lines = [f"{len(changed)} file(s) changed since last scan: "
                 + ', '.join(changed[:8]) + (f" (+{len(changed) - 8})" if len(changed) > 8 else '')]
        if impacted:
            lines.append(f"Blast radius: {len(impacted)} file(s) transitively import the "
                         f"changed set: " + ', '.join(impacted[:10])
                         + (f" (+{len(impacted) - 10})" if len(impacted) > 10 else ''))
        else:
            lines.append("Blast radius: nothing else imports the changed set.")
        return '\n'.join(lines)

    def surprises(self):
        self._ready()
        from . import clustering, graphmerge, surprises as _sp
        comm = clustering.detect_communities(len(self.nodes), self.edges)
        un, ue, uc = graphmerge.integrate(self.nodes, self.edges, comm, self.docs, [], str(self.root))
        sur = _sp.build_surprises(un, ue, uc)
        if not sur:
            return "No surprising connections found (small or highly-modular graph)."
        return ("Surprising connections (cross-module / cross-modal — ask why each matters):\n"
                + '\n'.join(f"  • {s['from']} -> {s['to']}  [{s['from_file']} <-> {s['to_file']}]"
                            f" — {s['reason']}" for s in sur[:12]))

    def vibe(self):
        self._ready()
        from . import vibe as _vibe
        return _vibe.vibe_check(_ask._ctx(self.out_dir))

    def what_if(self, target):
        self._ready()
        from collections import defaultdict
        rels = [n['rel'] for n in self.nodes]
        fmatch = ([r for r in rels if r == target] or [r for r in rels if r.endswith('/' + target)]
                  or [r for r in rels if r.endswith(target)])
        if fmatch:
            f = fmatch[0]
            id_of = {n['rel']: n['id'] for n in self.nodes}
            id2 = {n['id']: n['rel'] for n in self.nodes}
            rev = defaultdict(list)
            for e in self.edges:
                if e.get('kind', 'import') == 'import':
                    rev[e['target']].append(e['source'])
            seen, stack = set(), [id_of[f]]
            while stack:
                c = stack.pop()
                for d in rev.get(c, []):
                    if d not in seen:
                        seen.add(d)
                        stack.append(d)
            imp = sorted(id2[i] for i in seen)
            return (f"Changing {f}: {len(imp)} file(s) transitively import it"
                    + ((' — ' + ', '.join(imp[:20])) if imp else ' (leaf / entry point)'))
        from . import callgraph as _cg
        cg = _cg.build_call_graph(self.nodes, self.file_texts)
        if not cg.get('available'):
            return "Function impact needs tree-sitter; pass a file path instead."
        callers = cg.get('callers', {})
        if target not in callers and target not in cg.get('callees', {}):
            return f"'{target}' isn't a known file or function."
        seen, stack = set(), [target]
        while stack:
            c = stack.pop()
            for u in callers.get(c, []):
                if u not in seen:
                    seen.add(u)
                    stack.append(u)
        return (f"Changing {target}(): {len(seen)} function(s) transitively call it"
                + ((' — ' + ', '.join(s + '()' for s in sorted(seen)[:20])) if seen
                   else ' (entry point or reached dynamically)'))

    def symbols(self):
        self._ready()
        from . import symgraph as _sg
        sg = _sg.build_symbol_graph(self.nodes, self.file_texts)
        if not sg.get('available'):
            return "Symbol graph needs tree-sitter (it's on by default when installed)."
        c = sg['counts']
        return (f"Symbol graph: {c['symbols']} symbols ({c['classes']} classes) / {c['calls']} call "
                f"edge(s) / {c['inherits']} inheritance edge(s) across {c['files']} files. "
                f"Full graph (functions/classes/methods + defines/calls/inherits) in "
                f".nodo/nodo-symbols.json.")

    def calls(self, symbol):
        self._ready()
        from . import callgraph as _cg
        out = _cg.query_symbol_calls(_cg.build_call_graph(self.nodes, self.file_texts), symbol or '')
        return out or (f"'{symbol}' isn't a calling/called function in the graph "
                       f"(call graph needs tree-sitter; it's on by default when installed).")

    def teach(self, lesson):
        from . import lessons as _lz
        if isinstance(lesson, str):
            import json
            try:
                lesson = json.loads(lesson)
            except Exception as e:
                return f"Lesson must be a JSON object (parse error: {e})."
        ok, errors, summary = _lz.merge_lessons(self.out_dir, lesson)
        if not ok:
            return "Lesson rejected:\n" + "\n".join(f"  - {e}" for e in errors)
        self.refresh()   # apply immediately so subsequent tool calls reflect the lesson
        bits = []
        if summary.get('languages_added'):
            bits.append("learned " + ", ".join(summary['languages_added']))
        if summary.get('languages_updated'):
            bits.append("updated " + ", ".join(summary['languages_updated']))
        if summary.get('keep_alive_added'):
            bits.append("keep-alive " + ", ".join(summary['keep_alive_added']))
        if summary.get('resolver_hints_added'):
            bits.append("resolver hints " + ", ".join(summary['resolver_hints_added']))
        now = summary.get('extensions_now_understood') or []
        tail = (f" nodo now understands: {', '.join(now)}." if now else "")
        return "Taught nodo (" + "; ".join(bits) + ")." + tail + " Map refreshed — healed."


def tool_specs():
    """MCP tool definitions (name, description, JSON input schema)."""
    S = lambda **p: {"type": "object", "properties": p, "required": [k for k in p]}
    opt = lambda **p: {"type": "object", "properties": p, "required": []}
    return [
        {"name": "nodo_ask", "description": "Ask a natural-language question about the codebase; "
         "nodo routes it to blast-radius, import-path, symbol, issues, hubs, concept, or overview.",
         "schema": S(question={"type": "string", "description": "the question"})},
        {"name": "nodo_blast_radius", "description": "A file's dependents + transitive change "
         "impact — what could break if you change it.",
         "schema": S(file={"type": "string", "description": "path to a source file"})},
        {"name": "nodo_who_uses", "description": "Where a symbol is defined and every file that "
         "references it (or confirms it's unused).",
         "schema": S(symbol={"type": "string", "description": "function/class/symbol name"})},
        {"name": "nodo_path", "description": "The import chain connecting two files (how A reaches B).",
         "schema": S(a={"type": "string"}, b={"type": "string"})},
        {"name": "nodo_explain", "description": "Find the files, docs and PDFs most related to a "
         "concept (BM25 + synonyms).",
         "schema": S(concept={"type": "string", "description": "concept, e.g. 'authentication'"})},
        {"name": "nodo_list_issues", "description": "Detected issues, highest-confidence first; "
         "optionally scoped to one file.",
         "schema": opt(file={"type": "string", "description": "optional file to scope to"})},
        {"name": "nodo_hubs", "description": "Load-bearing files (highest blast radius).", "schema": opt()},
        {"name": "nodo_topics", "description": "Knowledge-graph topics + god-nodes from docs/PDFs.", "schema": opt()},
        {"name": "nodo_overview", "description": "A synthesized project overview (files, hubs, "
         "concepts, topics, issue counts).", "schema": opt()},
        {"name": "nodo_refresh", "description": "Rescan the project so answers reflect the latest code.", "schema": opt()},
        {"name": "nodo_self_check", "description": "What nodo does NOT understand yet — unknown "
         "languages, files it parsed but pulled nothing from, unresolved local imports — with a "
         "ready-to-fill lesson template. Use this, then nodo_teach, to heal a blind spot.",
         "schema": opt()},
        {"name": "nodo_teach", "description": "Tutor nodo: persist a lesson so it sticks across "
         "scans. Pass a lesson object — languages (extensions + def/import regex), keep_alive "
         "(suppress a confirmed false 'dead code' finding), or resolver_hints. nodo applies it "
         "immediately and on every future scan. Offline; no LLM call.",
         "schema": S(lesson={"type": "object", "description": "a lesson per the lessons.json schema"})},
        {"name": "nodo_fix_context", "description": "Emit the structured <context> prompt for a "
         "file's issues (evidence to act on — nodo gathers, you write the fix).",
         "schema": S(file={"type": "string", "description": "path to a source file"})},
        {"name": "nodo_changed", "description": "Files changed since the last scan and their "
         "combined transitive blast radius — what your recent edits put at risk.",
         "schema": opt()},
        {"name": "nodo_calls", "description": "A function's call graph: who calls it and what "
         "it calls (function-level, deterministic from the parse tree).",
         "schema": S(symbol={"type": "string", "description": "function/method name"})},
        {"name": "nodo_surprises", "description": "Surprising connections — ranked cross-module / "
         "cross-modal (code↔docs↔assets) bridge edges that grep and similarity search miss. "
         "nodo gives the edge + evidence; you explain why it matters.", "schema": opt()},
        {"name": "nodo_what_if", "description": "Impact simulation: what a change to a file "
         "(transitive importers) or function (transitive callers) could affect.",
         "schema": S(target={"type": "string", "description": "a file path or function name"})},
        {"name": "nodo_symbols", "description": "Symbol-graph summary — functions/classes/methods "
         "as nodes with defines/calls/inherits edges (full graph in .nodo/nodo-symbols.json).",
         "schema": opt()},
        {"name": "nodo_vibe_summary", "description": "A concise architectural 'vibe check' "
         "(deterministic): shape (god module / modular / layered), coupling, health, themes.",
         "schema": opt()},
    ]


def dispatch(state, name, args):
    """Route an MCP tool call to a handler. Pure + testable (no MCP needed)."""
    a = args or {}
    try:
        if name == "nodo_ask":
            return state.ask(a.get("question", ""))
        if name == "nodo_blast_radius":
            return state.blast_radius(a.get("file", ""))
        if name == "nodo_who_uses":
            return state.who_uses(a.get("symbol", ""))
        if name == "nodo_path":
            return state.path(a.get("a", ""), a.get("b", ""))
        if name == "nodo_explain":
            return state.explain(a.get("concept", ""))
        if name == "nodo_list_issues":
            return state.list_issues(a.get("file"))
        if name == "nodo_hubs":
            return state.hubs()
        if name == "nodo_topics":
            return state.topics()
        if name == "nodo_overview":
            return state.overview()
        if name == "nodo_refresh":
            state.refresh()
            return "Rescanned the project; the map is fresh."
        if name == "nodo_self_check":
            return state.self_check()
        if name == "nodo_teach":
            return state.teach(a.get("lesson", {}))
        if name == "nodo_fix_context":
            return state.fix_context(a.get("file", ""))
        if name == "nodo_changed":
            return state.changed()
        if name == "nodo_calls":
            return state.calls(a.get("symbol", ""))
        if name == "nodo_surprises":
            return state.surprises()
        if name == "nodo_what_if":
            return state.what_if(a.get("target", ""))
        if name == "nodo_symbols":
            return state.symbols()
        if name == "nodo_vibe_summary":
            return state.vibe()
        return f"Unknown tool: {name}"
    except Exception as e:
        return f"nodo error handling {name}: {e}"


def serve(root='.', out_dir=None):
    """Run the MCP stdio server. Returns an exit code. Needs the `mcp` package."""
    try:
        import asyncio
        from mcp.server import Server
        from mcp import types
        from mcp.server.stdio import stdio_server
    except Exception:
        sys.stderr.write(
            "nodo MCP server needs the 'mcp' package (Python 3.10+).\n"
            "  Install:  pip install mcp        (or: pip install \"nodo-map[mcp]\")\n"
            "The zero-dependency core and all CLI commands work without it.\n")
        return 1

    state = _State(root, out_dir)
    server = Server("nodo")

    @server.list_tools()
    async def _list_tools():
        return [types.Tool(name=s["name"], description=s["description"], inputSchema=s["schema"])
                for s in tool_specs()]

    @server.call_tool()
    async def _call_tool(name, arguments):
        text = dispatch(state, name, arguments or {})
        return [types.TextContent(type="text", text=text)]

    async def _main():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_main())
    return 0


def _cli(argv=None):
    p = argparse.ArgumentParser(prog="nodo.serve",
                                description="Run nodo as an MCP server (stdio).")
    p.add_argument("path", nargs="?", default=".", help="project root (default: .)")
    p.add_argument("-o", "--out", default=None, help="output dir (default: <path>/.nodo)")
    a = p.parse_args(argv)
    return serve(a.path, a.out)


if __name__ == "__main__":
    sys.exit(_cli())
