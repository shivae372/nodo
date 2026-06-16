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
            out += ('\n\nStarter lesson (fill the regexes, then call nodo_teach or '
                    '`nodo . --teach`):\n' + json.dumps(hc['teach_template'], indent=2))
        return out

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
