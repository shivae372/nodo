# Nodo

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](pyproject.toml)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](pyproject.toml)
[![Claude Code skill](https://img.shields.io/badge/Claude_Code-%2Fnodo_skill-8A2BE2.svg)](.claude/skills/nodo/SKILL.md)

**Map any codebase in seconds.** Nodo scans your project, draws an interactive
dependency graph, flags code smells, and — crucially — emits clean, structured
context your AI coding agent can actually use.

Zero dependencies. Pure Python standard library. One self-contained HTML file
that works offline. No build step, no `npm install`, no account.

> Built for solo "vibe coders" who lean on Claude Code / Cursor / Windsurf, and
> for senior engineers who want a fast architectural read on an unfamiliar repo.

### Install as a Claude Code plugin

```text
/plugin marketplace add shivae372/nodo
/plugin install nodo@nodo
```

> Don't see `/plugin` (or a "path does not exist" error)? Your Claude Code is out of date — run **`claude update`**, then retry. Prefer no plugins? Nodo is **clone-and-run** — see [Quick start](#quick-start).

Part of the **[claude-bootstrap](https://github.com/shivae372/claude-bootstrap) ecosystem**: claude-bootstrap (the capability/self-improvement layer) reads Nodo's architecture map automatically at session start.

![Nodo topology graph](docs/screenshot-graph.png)

---

## Why

When you let an AI write whole features, two things get hard:

1. **You lose the map.** Where does this function reach? What breaks if I touch it?
2. **The agent lacks context.** It re-reads files, guesses at architecture, and
   sometimes breaks unrelated flows.

Nodo fixes both. The **HTML viewer** is for your eyes. The **`nodo-context.json`
/ `.md`** files are for your agent — point Claude Code at them and it understands
your architecture and open issues without you explaining a thing.

---

## Quick start

Nodo is **clone-and-run** — pure Python standard library, no install, no dependencies.

```bash
git clone https://github.com/shivae372/nodo
cd nodo
python -m nodo /path/to/your/project --open
```

To map a project from anywhere, point Python at the launcher in the cloned repo:

```bash
python /path/to/nodo/nodo.py /path/to/your/project
```

That's it. Open `.nodo/nodo.html` in any browser.

### Recommended: the Claude Code skill

The repo ships a [`/nodo` skill](.claude/skills/nodo/SKILL.md) for [Claude Code](https://claude.com/claude-code).
Copy `.claude/skills/nodo/` into any project's `.claude/skills/`, then just type **`/nodo`** —
the skill rebuilds the map and the AI-agent context files in one step, and Claude reads them
back automatically. This is the blessed, highest-trust way to run it.

---

## Two modes: vibe-coder (default) vs advanced

Nodo runs at two depths — pick one:

| | **Vibe-coder** (default) | **Advanced** (`--deep`) |
|---|---|---|
| Command | `python nodo.py .` | `python nodo.py . --deep` |
| Speed / deps | Instant, **zero dependencies** (pure stdlib, regex) | Heavier; install `tree-sitter tree-sitter-language-pack markitdown` |
| Graph | File + import dependency graph | + **tree-sitter symbols** (~19 langs) + a **function-level call graph** |
| Semantics | Structure + issues + hubs + data flow | + **knowledge graph** (concepts, topic communities, god-nodes) + **multimodal** (docs/PDFs/images) |
| Best for | Daily iteration, issue-spotting, blast radius — staying in flow | Onboarding, architecture review, big-picture understanding |

**What "semantic" means here.** *Syntactic* is structure — files, imports,
definitions (vibe mode). *Semantic* is meaning — concepts, how things relate,
which functions call which (advanced mode). Nodo builds the semantic **scaffold
deterministically** (no embeddings, no LLM, nothing leaves your machine) and lets
**your AI assistant be the reasoning layer** on top. So you get semantic depth
*grounded in real evidence* — never a hallucinated graph — plus everything vibe
mode already gives you. Advanced mode also surfaces:

- a **symbol-level graph** (`.nodo/nodo-symbols.json`) — functions, classes and
  methods as first-class nodes with `defines` / `calls` / `inherits` / `contains`
  (class→method) edges, plus **rationale** (each symbol's docstring/leading
  comment) — hierarchical (files → classes → methods) *and* flat. Works across
  **every installed tree-sitter grammar** (C/C++/Go/Rust/Java/… not just JS/TS/Py).
  Battle-tested on OpenClaw (776-file C++): **6,937 symbols, 3,481 calls, 1,297
  containment, 99 inheritance edges**,
- a **function call graph** (`.nodo/nodo-callgraph.json`; trace any function with
  `--calls <fn>`, or ask *"what calls X / what does X call?"*),
- **impact simulation** — `--what-if <file|fn>` shows the transitive importers /
  callers a change could affect,
- **architecture insights** in `nodo-report.md` — god objects, import cycles,
  load-bearing functions, dead surface (deterministic, pattern-based),
- **surprising connections** — ranked cross-module / cross-modal *bridge* edges
  (e.g. a design doc that links straight to a core class), the hidden links grep
  and similarity search miss; nodo gives the edge + evidence, your assistant
  explains *why* (`--ask "surprising connections"`),
- **edge provenance** on every dependency (`extracted` = exact resolve, `ambiguous`
  = suffix/basename fallback, `inferred` = doc-mention / resolver-hint),
- **suggested questions** the map is best at answering.

All of it lands in `nodo-report.md` + `nodo-context.json`, deterministic and
offline. Surprises are scored with **betweenness centrality** (pure-stdlib Brandes,
size-gated) so true bridges rank highest. (Community detection is pure-stdlib label
propagation — no `igraph`/Leiden dependency — so "advanced" still needs nothing
heavier than optional tree-sitter.)

Not sure which mode? **`--smart`** picks for you — a cheap pre-scan reads project
size, language mix, and docs/PDFs and runs vibe or `--deep` accordingly (it prints
which and why). And the call graph exports to **Mermaid** or **Graphviz DOT**
(`--export mermaid|dot`) to drop into a PR or render a diagram.

**Both modes** also answer **`--vibe`** — a deterministic architectural read
("*hub-and-spoke around `app.ts`, layered, loosely coupled, carrying some debt*"):
shape, god module, coupling, health, and themes, in one paragraph grounded in the
graph. It's fast and zero-dep, so vibe coders get it too (`--ask "what's the vibe?"`,
or MCP `nodo_vibe_summary`).

---

## What you get

Everything lands in `<project>/.nodo/`:

| File | For | Contents |
|---|---|---|
| `nodo.html` | **you** | Interactive graph + Issues + Hubs + AI Context tabs. Self-contained, offline. |
| `nodo-context.json` | **AI agents** | Full graph, hubs, modules, and every issue with line numbers + code snippets. |
| `nodo-context.md` | **AI agents** | Token-cheap summary — drop it straight into a chat. |
| `nodo-issues.txt` | grep / CI | Plain-text issue list. |

### The viewer

- **Topology graph** — every file a node, every import an edge. Force-directed
  layout, click to inspect, drag to explore. Node labels are readable (black on
  light). Sliders for node size / edge width / label size.
- **Issues tab** — code smells grouped by category, each with the exact line, a
  3-line code snippet, a **Copy AI Context** button, and an IDE deep-link.
- **Data Flow tab** — auto-derived from the graph: each entry point (API route,
  page, `main`) rendered as a numbered step-by-step sequence — step 1 the entry,
  then the files imported at each depth. Read left to right to see how a request
  moves through the code. Zero configuration.
- **API Reference tab** — every HTTP route grouped by domain, with the methods
  each handles (read from the actual handler exports, not guessed) as colour-coded
  badges. Click a path to open it in your editor.
- **Security tab** — files auto-classified by what they touch (auth, crypto,
  secrets, payments, database, network, user input) with the matched terms — the
  surfaces to review first in any audit.
- **Hubs & Modules** — your highest-blast-radius files and detected clusters.
- **AI Context** — one-click copies: project summary, full issue backlog, or a
  scoped prompt per issue.
- **Hot Paths** (`h`) — highlight the architectural hubs and what they touch.
- **Find & export** — a search box filters/zooms the graph to files matching a
  name, and **Export PNG** saves the current canvas as an image.

![Nodo issues tab with code snippets and Copy AI Context](docs/screenshot-issues.png)

### AI-native features

Every issue card copies a ready-to-paste prompt:

```xml
<context project="my-app">
  <file path="src/auth/token.ts" line="42" />
  <issue severity="warn" type="Math.random() for value">
    Math.random() is not cryptographically secure...
  </issue>
  <dependencies>session.ts, crypto.ts, login.ts</dependencies>
  <code>
    41: export function makeToken() {
    42:   const id = Math.random().toString(36).slice(2)
    43:   return id
  </code>
</context>
<task>Fix this issue. Match the existing patterns and don't break unrelated code.</task>
```

Or just tell your terminal agent:

> Read `.nodo/nodo-context.json` before changing anything.

---

## Ask nodo anything (`--ask`)

One command for every question — nodo figures out what you mean and routes it to
the right answer. No need to remember flags:

```bash
python nodo.py . --ask "what breaks if I change lib/auth.ts?"   # → blast radius + change impact
python nodo.py . --ask "how does the router connect to the db?" # → import-path trace
python nodo.py . --ask "who uses verifyToken?"                  # → definition + references
python nodo.py . --ask "what should I fix in checkout.ts?"      # → issues, high-confidence first
python nodo.py . --ask "what are the key files?"                # → load-bearing hubs
python nodo.py . --ask "where is authentication?"               # → code + docs + PDFs
python nodo.py . --ask "what are the main topics?"              # → knowledge-graph communities
```

Every answer is prefixed with how it was interpreted (e.g. `[nodo · blast radius:
lib/auth.ts]`), so it's never a black box — and an unrecognized question returns a
short menu of what nodo can answer.

**nodo finds, your agent judges.** `--ask` is offline and deterministic — it hands
back fast, structured *evidence* (blast radius, who-uses, issues, hubs, overview).
The AI assistant sitting on top (Claude Code) reads that evidence and tells you the
*correct, relevant* part. That split is the whole point: nodo does the instant,
private search; the model does the reasoning — so you get answers without the
grep-and-guess, and without the model hallucinating structure it didn't verify.

## MCP server — live tools, mid-session (optional)

Static context files are read once at session start. The MCP server lets your
agent call nodo **as tools any time** — "what's the blast radius of the edit I
just made?" — which is the stickier integration pattern.

```bash
pip install mcp                 # optional (Python 3.10+); core stays zero-dep
python nodo.py . --mcp          # run the stdio server
python nodo.py . --install      # also registers it in .mcp.json for Claude Code / Cursor
```

It exposes nineteen tools (incl. `nodo_vibe_summary`) — `nodo_ask`, `nodo_blast_radius`, `nodo_who_uses`,
`nodo_path`, `nodo_explain`, `nodo_list_issues`, `nodo_hubs`, `nodo_topics`,
`nodo_overview`, `nodo_refresh`, `nodo_fix_context` (the structured prompt for a
file's issues), `nodo_changed` (diff-aware blast radius of recent edits),
`nodo_calls` (a function's call graph), `nodo_surprises` (cross-module / cross-modal
bridge edges), `nodo_what_if` (impact simulation), `nodo_symbols` (symbol-graph
summary), plus `nodo_self_check` and `nodo_teach` (the self-healing loop — see
below) — all thin wrappers over the same engine the CLI uses (local, no network;
only `nodo_teach` writes, and only to the local `.nodo/lessons.json`). If `mcp`
isn't installed, nodo prints the install command and every other command keeps
working — the zero-dependency core is never affected.

## It remembers (personalization)

nodo learns from how you use it, all **local** (nothing leaves your machine):

- **What changed since your last scan** — every scan reports changed/new files
  (via the content-hash cache), surfaced in the output and `nodo-context.md` →
  "Since your last scan".
- **The files you work with most** — `--query`/`--ask` are logged locally
  (`.nodo/queries.log`) and the files you touch most show up in `nodo-context.md`,
  so the map foregrounds *your* hot paths. Keep `.nodo/` out of git (it's local).

## It heals (self-healing & self-learning)

nodo knows where it's blind, and it lets Claude tutor it — so the more you use
them together, the smarter nodo gets about *your* stack. The division of labour
again: **nodo is the deterministic, offline scaffold; Claude is the intelligence.**
nodo never calls an LLM — lessons are local data it applies on every scan.

**1. nodo diagnoses itself** — `--self-check` (alias `--doctor`) reports exactly
where it came up empty:

```bash
python nodo.py . --self-check
```
```
[nodo · self-check]

Languages nodo does NOT understand yet (no parser / no lesson):
  • .zig  — 14 file(s)
  → nodo auto-drafted a lesson for '.zig' from your files (37 definition(s),
    9 import(s) across 6 sample(s)). It's a DRAFT — review the regexes below,
    save as lesson.json, then `nodo . --teach lesson.json`.

Local imports nodo could not resolve to a file:
  • src/app.ts  (2: @generated/api, ~/legacy/shim)
  → Add `resolver_hints` mapping the import string → real path …
```

It flags three blind-spot classes: **unknown languages** (a code extension it has
no parser for), **silent extraction** (a file it parsed but pulled no symbols/imports
from — a grammar gap), and **unresolved local imports** (relative paths that didn't
resolve to an edge). Every normal scan prints a one-line nudge when gaps exist, and
records them in `nodo-context.json → diagnostics.learning_gaps`.

**2. Claude tutors it** — and nodo does most of the writing. `--self-check` prints
an **auto-drafted lesson**: it induces the def/import regexes from your actual
files (a deterministic keyword library, every pattern validated against the
samples — no LLM) and reports how many symbols/imports the draft would extract, so
it's self-evidencing. Claude reads a couple of the files, **confirms or refines**
the draft (or adds a `keep_alive` / `resolver_hint` correction), and `--teach`
validates and persists it:

```jsonc
// lesson.json  — nodo's auto-draft, confirmed by Claude against the .zig files
{
  "languages": {
    "zig": {
      "extensions": [".zig"],
      "def_patterns":    ["\\bfn\\s+([A-Za-z_]\\w*)", "\\bconst\\s+(\\w+)\\s*=\\s*struct"],
      "import_patterns": ["@import\\(\"([^\"]+)\"\\)"]
    }
  },
  "keep_alive":     ["src/plugins/registry.ts"],         // stop calling a dynamically-loaded file "dead"
  "resolver_hints": { "@generated/api": "src/api/gen.ts" } // resolve an alias nodo couldn't
}
```
```bash
python nodo.py . --teach lesson.json          # validated, saved to .nodo/lessons.json
python nodo.py . --teach examples/lessons/    # a directory teaches every *.json in it
```

If `tree-sitter-language-pack` already ships a grammar for the language, skip the
regexes entirely — add `"grammar": "<name>"` to the language and nodo extracts via
the real parse tree (e.g. `{"extensions": [".pyx"], "grammar": "python"}`). Curated
starter lessons live in [`examples/lessons/`](examples/lessons/) (Zig, Nim, …) —
all local, nothing is fetched over the network.

**3. nodo heals — permanently.** On the next scan the taught language is
**first-class**: its files are real nodes, its imports become edges, and
`--query <symbol>` works across it. A `keep_alive` correction suppresses a
confirmed-false "dead code"/"disconnected" finding (without ever hiding a real bug
like a hardcoded secret in the same file); a `resolver_hint` fixes a missed import.
The lesson persists in `.nodo/lessons.json` and applies to every future scan — nodo
**learned**. Bad lessons (invalid regex, missing extensions) are rejected, so the
heal is always safe.

Same loop works live over MCP — `nodo_self_check` then `nodo_teach` — so Claude
can heal a blind spot the moment it hits one, mid-session.

> Invalid regexes are rejected; lessons are plain local JSON you can read, edit,
> and commit per-project (or keep out of git with the rest of `.nodo/`).

## Save your agent's tokens

Two features turn Nodo from a viewer into an agent's memory — both cut tokens by
answering questions the agent would otherwise spend reads on.

### Blast-radius queries

Instead of letting an agent open ten files to figure out "what breaks if I touch
this", ask Nodo. One command, ~200 tokens, no rescan:

```bash
python nodo.py . --query lib/auth.ts
```

```
FILE  lib/auth.ts
      category=lib  loc=88  edges=17
      hub rank #6 (high blast radius)

DEPENDENTS (13) — these import it; changing its exports may break them:
  <- app/api/login/route.ts
  <- app/api/admin/users/route.ts
  ...
DEPENDENCIES (4) — this file imports:
  -> lib/crypto.ts
  ...
CHANGE IMPACT: 31 file(s) transitively depend on this (up to 4 hop(s); 18 indirect).
ISSUES (1):
  [warn] fetch() without timeout:L40 — external call can hang the function
```

The **CHANGE IMPACT** line is the full transitive blast radius — every file that
directly *or indirectly* imports this one — so an agent can gauge "what could
break if I change this" before touching it.

### Symbol queries (definition + references, or "confirmed dead")

`--query` also takes a **symbol**, not just a file. Ask where `AudioEngine` is
defined and who references it — or get a confirmation that nothing does:

```bash
python nodo.py . --query AudioEngine
```

```
SYMBOL  AudioEngine
DEFINED IN (1):
  src/features/AudioEngine.js:L12
REFERENCED IN: 0 files — nothing references this symbol.
  Confirmed unreferenced: likely dead code or an unwired feature
```

This makes the disconnected-feature and dead-code findings **self-verifying** —
no grep needed to confirm an orphan.

### Trace & explain (no file reads)

```bash
python nodo.py . --path lib/auth.ts lib/db.ts   # how does A reach B through imports
python nodo.py . --explain "authentication"     # which files implement a concept (BM25 + synonyms)
```

`--path` BFS-traces the import chain between two files. `--explain` is a
zero-dependency BM25 keyword search with a code-aware tokenizer (splits
`camelCase`/`snake_case`) and a concept-synonym map, so `auth` also finds
`login`/`jwt`/`session`. It also folds in your **design docs** (Markdown / specs),
so `--explain "audio features"` surfaces the spec that *defines* a feature next
to the code that implements it — letting you judge code against intent. Both
answer in a few hundred tokens.

### Auto-load the map at session start

Install a Claude Code hook once, and every session silently receives the
architecture summary — no grepping to rebuild context:

```bash
python nodo.py . --hook
```

This writes a `SessionStart` hook into `.claude/settings.json` that runs
`nodo.py . --emit-context`, injecting `nodo-context.md` into the agent's context
automatically. It's idempotent and preserves any existing settings.

---

## What it detects

Every issue carries a **confidence** (`high` / `medium` / `low`) so an agent can
triage — act on high (structural facts / AST-verified: cycles, broken contracts,
arg-count drift, platform-gated UI), weigh medium, treat low as hints
(duplication, complexity, `console.log`, markers). It's in `nodo-context.json`,
`nodo-context.md`, and `nodo-issues.txt`.

Built-in, language-aware where it matters, noise-suppressed in test files:

**Security** — `Math.random()` for IDs/tokens, `dangerouslySetInnerHTML`,
`eval`/`new Function`, possible hardcoded secrets, SQL built by string
concatenation (injection), unsafe deserialization (`pickle`/`yaml.load`).
**Reliability** — empty `catch` blocks, bare `except:`, `fetch()` without a
timeout.
**Type safety** — `@ts-ignore`/`@ts-nocheck`, `as any` escapes.
**Tech debt** — `TODO`/`FIXME`/`HACK`/`XXX`, `eslint-disable`.
**Topology** — high-coupling hubs (god objects), possibly-unused files.
**Hygiene** — stray `console.log`/`print`, unchecked `process.env.X!`.

### Cross-file bugs an LLM editing one file can't see

These are the findings that need the *whole repo* in view — exactly what an AI
assistant editing a single file lacks. All deterministic, tuned for near-zero
false positives:

**Broken contracts** — a symbol imported by one file that its source no longer
exports (a rename/removal that breaks the importer at build time). Now follows
`export * from` barrels, so re-exported symbols are no longer falsely flagged.
**Argument-count drift** *(tree-sitter mode only)* — a call that passes more
arguments than the imported function declares. Both the signature and the call's
argument count come from the parse tree (rest params, optional params, and spread
calls are handled), so it's false-positive-safe — verified to flag zero on
click/express/hono. In regex mode it stays **off** by design (regex can't count
args safely).
**Import cycles** — real runtime circular imports (type-only imports excluded).
**Disconnected features** — a file with real surface area (many exports / lots
of code) that *nothing* in the project imports and whose name appears in no
import. The "implemented but never wired in" smell — exactly what an AI agent
introduces when it builds a feature and forgets to connect it. Graphify can't
see this.
**Platform-gated dead UI** — a component whose handlers all call an injected
bridge with optional chaining (`window.electronAPI?.x()`) and no fallback, so it
silently no-ops outside that platform (a browser/SSR build).
**Orphaned exports** — exported symbols nothing imports; dead surface area.
**Duplication drift** — identical blocks copied across files that can silently
diverge when one copy is fixed and the others aren't.
**Reassignment of an imported binding** *(data-flow)* — assigning to an ESM named
import, which is read-only and throws a `TypeError` at runtime. Statement-anchored
and skipped when the name is locally declared, destructured, or a parameter, so
it's high-confidence with near-zero false positives.
**Shared mutable export** *(ownership)* — `export let`/`var` that another module
imports by name: shared mutable module state. An info-level hint to prefer `const`
+ an explicit setter so ownership stays clear.

### Signal over noise: corpus tiering

Reference and example code (`reference/`, `third_party/`, `examples/`,
`fixtures/`, `vendored/`, `benchmarks/`, …) is **tiered out of issue counts by
default** — the files still appear in the graph, they're just not counted
against you, so a third-party folder full of `console.log`s never buries
findings in your own code. Pass `--include-vendor` to count them too.

Heavy build / dependency directories (`node_modules/`, `vendor/`, `dist/`,
`build/`, `.git/`, …) are **skipped entirely** — not scanned and not in the
graph.

### Custom rules

Add a `.nodo.json` at your project root (`python /path/to/nodo/nodo.py . --init` writes a starter):

```json
{
  "ignore_dirs": ["generated", "legacy"],
  "community_names": { "0": "API layer", "1": "UI" },
  "custom_rules": [
    {
      "name": "Inlined plan limit",
      "pattern": "\\b(50|500|2000)\\b",
      "include": "api/",
      "severity": "warn",
      "category": "Billing",
      "detail": "Plan limit not imported from the single source of truth."
    }
  ]
}
```

`pattern` is any Python regex. `include`/`exclude` filter by file path. Your
rules show up in the viewer and artifacts alongside the built-ins.

Two more knobs tune the built-ins to your project:

```json
{
  "suppress": [
    "TODO marker",                              // drop a finding type everywhere
    { "type": "console.log", "path": "scripts/" } // …or only under a path
  ],
  "severity_overrides": { "console.log left in code": "warn" }
}
```

`suppress` removes findings you've decided are noise (string = match the issue
type; object = `type` and/or `path`, both substring). `severity_overrides`
re-weights a finding type (`error` / `warn` / `info`) — exact type match first,
else substring. Suppressed issues don't count toward the per-type cap.

---

## Languages

**Import graph + categorization** work on every language via the resolver
(relative paths, aliases, dir-index): JS/TS/JSX/TSX/Vue/Svelte and Python are
deepest (routes, components, hooks, models understood); Go, Rust, Java, C/C++,
C#, Ruby, PHP and others resolve at the file-dependency level.

**Tree-sitter AST symbol extraction** (auto when tree-sitter is installed, or
`--ast`) covers the mainstream set — Python, JS, TS, TSX, Go, Rust, Java, C, C++,
C#, Kotlin, Swift, Scala, Dart, Ruby, PHP, Lua, Solidity, Bash — via a
*grammar-agnostic definition matcher* that works across **every grammar
`tree-sitter-language-pack` ships (100+)** — any grammar that follows
tree-sitter's definition-node convention works automatically, no per-language
code. This powers `--query <symbol>` across languages,
disconnected-feature/orphan detection, and (for JS/TS) the AST-accurate
argument-count contract check.

Honest limits: local import *edges* are path-based — strongest for relative-import
languages (JS/TS/Py/C/C++); module-path languages (Go/Rust/Java/C#) extract symbols
and imports but resolve at the package level. Exotic/functional grammars (Haskell,
OCaml, Erlang, …) fall back to the regex extractor.

---

## CLI

```
nodo [PATH] [options]

  PATH                 project root to scan (default: current dir)
  -o, --out DIR        output directory (default: <path>/.nodo)
  --name NAME          project name in the viewer (default: folder name)
  --open               open the HTML in your browser when done
  --init               write a sample .nodo.json and exit
  --self-check         report what nodo doesn't understand (unknown languages,
  (--doctor)           silent extraction, unresolved local imports), then exit
  --teach LESSON.json  ingest a lesson (teach nodo a language or a correction);
                       persists to .nodo/lessons.json and applies on every scan
  --ask "QUESTION"     natural-language: routes to blast-radius/path/symbol/issues/
                       hubs/concept/topics — one command for every query
  --query FILE|SYMBOL  blast radius for a file, or definition+references for a symbol
  --path A B           show the import chain connecting two files
  --explain CONCEPT    find the files & design docs related to a concept (BM25)
  --topics             print knowledge-graph topics (doc/PDF communities), then exit
  --vibe               print a deterministic architectural "vibe check", then exit
  --smart              auto-pick vibe vs --deep from project size / languages / docs
  --export FORMAT      export the call graph as `mermaid` or `dot`, then exit
  --hook               install a Claude Code SessionStart hook, then exit
  --install            wire the map into Claude + Cursor + AGENTS.md (+ MCP), then exit
  --mcp                run as an MCP server (stdio) — agents call nodo's tools live
  --include-vendor     also analyse reference/vendored/example dirs
  --multimodal         link images/PDFs/video to the nodes near them
  --docs-only          index doc text but skip the multimodal asset pass
  --ast                require tree-sitter parsing (note + regex fallback if absent)
  --no-ast             force the regex extractor even if tree-sitter is installed
  --no-cache           disable the incremental parse + detection caches
  --jobs N             threads for the file-read pass (default 1; output identical)
  --full               deepest scan: shortcut for --ast --multimodal
  --deep               advanced mode: --full + a function-level call graph
  --calls SYMBOL       a function's call graph (who calls it + what it calls)
  --what-if FILE|SYMBOL  impact simulation: transitive importers / callers affected
  --benchmark          compare regex vs tree-sitter (timing + edges), then exit
  --ignore DIR         extra directory to skip (repeatable)
  --no-gitignore       don't read .gitignore for ignore dirs
  --version
```

---

## Multimodal (images, PDFs, docs)

A codebase is more than code. Run the multimodal pass to fold docs and visual
assets into the map:

```bash
python nodo.py . --multimodal      # include images / PDFs / video
python nodo.py . --docs-only       # docs text only, skip binary assets
```

Run bare (`python nodo.py .`) on a terminal and Nodo **asks** whether to include
images/PDFs; in scripts and agent runs it defaults to docs-only (no prompt, no
heavy work).

**Everything connects in one graph.** Docs and assets aren't a side list — they
become real nodes:

- **doc nodes** link to the code they describe — by markdown link *and* by module
  name, so a spec that mentions `AudioEngine` connects to `AudioEngine.js` (great
  for spotting "spec exists, implementation is disconnected").
- **asset nodes** (images/PDFs/video) link from every code/doc file that
  references them (`<img src="diagram.png">`, `![](arch.pdf)`).

These **reference edges** (`kind: "reference"`, drawn dashed) are distinct from
code **import edges** (`kind: "import"`), so the viewer shows the whole connected
picture while `--query`/`--path` keep tracing imports only. The full node + edge
set (with `kind`) is in `nodo-context.json`.

Nodo's core never sends anything over the network, so it does not *interpret*
image pixels itself — it locates each asset and wires it to the right nodes, and
the Claude skill reads the image/PDF with Claude's own vision when you ask.

### Convert-to-Markdown (save your agent's tokens)

PDFs, Word/PowerPoint/Excel, HTML, and more are **converted to Markdown once** and
saved under `.nodo/converted/` — so your agent reads the cheap `.md` instead of
the raw document (a PDF can cost 10–100× the tokens of its text). Each asset's
`converted` path is recorded in `nodo-context.json` → `assets`, and the converted
text is folded into the knowledge graph (so PDFs/Office docs contribute concepts
and topics, not just a filename link).

```bash
pip install markitdown          # broad conversion: PDF/Word/PPT/Excel/HTML/images (Python 3.10+)
python nodo.py . --full
```

Conversion uses Microsoft **markitdown** when installed (widest format support),
and falls back to **pypdf** for PDFs (and direct reads for HTML/CSV) otherwise —
all fully offline. Images stay linked for Claude's vision to interpret.

### Images & diagrams — the vision loop

nodo can't read pixels (it's offline), but Claude Code can — so they team up:

1. nodo links each image/diagram as a node and flags it as undescribed.
2. You ask Claude to look at it; Claude writes a 2–3 sentence description to
   `.nodo/converted/<file>.md` (the skill spells this out).
3. On the next scan nodo **pins that description to the node and folds it into the
   knowledge graph** (so the diagram's content drives concepts/topics), with a
   quality gate that ignores too-short/vague descriptions.
4. Anyone can then ask `--ask "describe the architecture diagram"` and get the
   pinned vision text instantly — no re-reading the image, no tokens burned.

That's the division of labour end-to-end: **nodo does the deterministic, offline
plumbing (extract, link, cluster, convert, pin); Claude supplies the vision and
the judgment.** A richer multimodal map than static extraction, at a fraction of
the weight — and nothing leaves your machine unless you ask Claude to look.

### Knowledge graph (docs + PDFs → topics, queryable by AI)

Beyond linking, Nodo mines the **content** of your docs and PDFs into a knowledge
graph: it extracts **concepts** (the shared, salient vocabulary), clusters
docs+concepts into **topics** (the same community detection used for code
modules), and adds `concept` nodes connected to the docs that cover them.

```bash
python nodo.py . --topics --full      # list the topics (communities)
```
```
Knowledge topics (2) — communities of docs/PDFs:
  • token: token, login, session, auth, jwt, logout   [auth.md, auth-design.md]
  • stripe: stripe, charge, invoices, monthly, payments   [payments-spec.pdf, payments.md]
```

It also surfaces **god-nodes** — the most-connected concepts that the most
documents flow through (`knowledge.god_nodes`). The full graph is in
`nodo-context.json` → `knowledge` (`concepts` + `topics` + `god_nodes`), mirrored
in `nodo-knowledge.md`. Nodo builds this **deterministically and
offline**; the *semantic* layer — "how does auth actually work?", reading a
diagram — is answered by the **Claude skill** on top (vision + reasoning over the
graph). That's the division of labour: Nodo is the fast, private scaffold; Claude
is the intelligence. `--explain "<concept>"` searches code + docs + PDF text to
locate sources for those answers.

## Parsing: regex by default, tree-sitter automatically

Out of the box Nodo uses a fast, **zero-dependency regex** extractor — clone and
run, nothing to install. If you install tree-sitter, Nodo **uses it automatically**
for higher-accuracy parsing (real parse trees ignore import-like strings in
comments and tell a genuine `require(...)` from a function that's merely named
`require`):

```bash
pip install tree-sitter tree-sitter-language-pack    # opt-in accuracy
```

The scan prints which parser ran (`[parser: tree-sitter]` or `[parser: regex]`).
Force either with `--ast` (require tree-sitter; note + regex fallback if absent)
or `--no-ast` (always regex). The regex path stays fully supported — both are
covered by the test suite.

## Performance & caching

- **Incremental parse cache** (`.nodo/cache.json`): each file's imports are cached
  by content hash + parser mode, so a rescan only re-parses files that actually
  changed. A cached run produces an **identical map** (graph + issues + knowledge)
  to a clean one — only the run's `generated` timestamp and the cache-hit counters
  in `diagnostics` differ; disable with `--no-cache`.
- **Incremental detection cache** (`.nodo/detect-cache.json`): the issue list is
  reused when nothing that affects detection changed (every file's content hash +
  parser + lessons + detection config), so a no-op rescan skips the cross-file
  passes entirely. Byte-identical inputs → byte-identical issues, so reuse is
  always correct.
- **Parallel reads** (`--jobs N`): the file-read pass can run on N threads for
  large trees. The resulting **map is identical** to a single-threaded run (all
  downstream work is order-deterministic — only the `generated` timestamp varies,
  as it does between any two runs); default is 1.
- **Bounded output**: no single detector emits more than 25 findings before
  collapsing to a summary line, so the report stays readable on large repos.
- **Nothing fails silently**: oversized/unreadable files are reported in the scan
  output and in `nodo-context.json` → `diagnostics`.

Indicative timings (regex, cold): ~63-file repo in ~0.7s, ~390-file repo in ~0.5s.

---

## How it works

1. **Scan** — walk the tree (honoring `.gitignore` + sane defaults), read every
   source file, extract imports per language.
2. **Resolve** — turn import strings into real file edges, bundler-style: exact
   path first, then a *unique* suffix match (handles off-by-one relative depths,
   `baseUrl`, aliased roots), then a *unique* basename — uniqueness is required,
   so it fixes false orphans without inventing phantom edges. Handles relative
   paths, tsconfig-style `@/`/`~/` aliases, `src/`/`app/` roots, dir `index`
   files, and Python dotted modules.
3. **Cluster** — label-propagation community detection (no external libs).
4. **Detect** — run built-in + custom rules, capturing line numbers and snippets.
5. **Render** — emit the self-contained HTML (vis-network inlined) plus the JSON
   / Markdown / text artifacts.

No network calls. No telemetry. Your code never leaves your machine.

---

## Use in CI (GitHub Action)

Nodo ships a zero-dependency GitHub Action that maps every pull request and posts
a blast-radius + issues summary as a PR comment (created once, then updated in
place). Add `.github/workflows/nodo.yml`:

```yaml
name: nodo
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  map:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.x' }
      - uses: shivae372/nodo@v1.2
        with:
          path: '.'        # project root to scan
          args: '--no-ast' # fast regex pass; drop for tree-sitter accuracy
          comment: 'true'  # set 'false' to skip the PR comment
```

It runs the same engine as the CLI (pure Python standard library — no install
step), so the comment is deterministic and the action never phones home. . (For `shivae372/nodo@v1.2` to resolve, tag a `v1.2`
release; `@main` works in the meantime.)

---

## Use with Claude Code

Nodo ships a Claude Code skill in [`.claude/skills/nodo/`](.claude/skills/nodo/).
Copy it into your project's `.claude/skills/` and type `/nodo` to regenerate the
map after a refactor. See the skill's `SKILL.md` for details.

### Other AI assistants

Claude Code is the primary target, but one command wires the map into others too:

```bash
python nodo.py . --install
```

This installs the Claude Code SessionStart hook **and** writes an `AGENTS.md`
section (read by Codex, Windsurf, Amp, OpenCode, and other agents) plus a Cursor
rule (`.cursor/rules/nodo.mdc`, `alwaysApply`) — each telling the assistant to read
`.nodo/nodo-context.md` and use `--query`/`--path`/`--explain` instead of grepping.
Idempotent; re-run after upgrades.

---

## Known limitations & accuracy

Honest about where the heuristics stop:

- **Extraction is regex by default** (tree-sitter when installed). Both resolve
  relative/alias/dir-index imports and dynamic `import()`; deeply dynamic patterns
  (fully computed string paths, framework auto-registration) can still miss an
  edge. When unsure, the resolver stays silent rather than invent a phantom edge,
  so you may occasionally see a real-but-unresolved import — not a wrong one.
- **Detectors are heuristic**, deliberately tuned for *few false positives* over
  completeness: cross-file checks suppress when confidence is low (e.g. `export *`
  barrels, plugin/dynamic architectures), and every detector is capped so it can't
  flood. That means some real issues won't be flagged — treat findings as
  high-signal leads, not a complete audit.
- **Deep language understanding is JS/TS + Python**; other languages resolve at
  the file-dependency level only.
- **Multimodal links, it doesn't *understand*.** Nodo connects assets/docs to the
  code that references them; interpreting an image/PDF's contents is left to your
  AI agent's own vision (PDF *text* extraction is available via optional `pypdf`).
- **The viewer is force-directed**; very large graphs read better via
  `nodo-context.json` + `--query`/`--explain` than the HTML canvas.
- **Safety**: Nodo only reads files and makes no network calls, but it runs on
  whatever you point it at — treat scanning untrusted code like opening it.

## Roadmap

- Symbol-level (not just file-level) cross-file contracts, AST-backed.
- Viewer UX: search, module collapse, degree filtering for large graphs.
- Incremental detection cache (today the parse step is cached; detection re-runs).
- More cross-file detectors (data-flow-aware checks).

## Tests

Zero-dependency, like the tool itself:

```bash
python -m unittest discover -s tests    # or: pytest tests/
```

The suite covers import resolution (including the false-orphan cases), corpus
tiering, every cross-file detector and its anti-false-positive guards, symbol
queries, doc recall, robust test-file detection, and adversarial inputs
(symlink loops, unicode names, oversized/binary files, malformed source) — so
"it works" is checked, not asserted. Detectors are also bounded: no single check
can emit more than 25 findings before collapsing to a summary line, so noise
never buries signal on a large codebase.

---

## License

MIT — see [LICENSE](LICENSE). Bundles [vis-network](https://github.com/visjs/vis-network)
(MIT/Apache-2.0); see [NOTICE](NOTICE) for third-party attribution.

Contributions welcome — new detectors and language resolvers especially.
