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
ISSUES (1):
  [warn] fetch() without timeout:L40 — external call can hang the function
```

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

Built-in, language-aware where it matters, noise-suppressed in test files:

**Security** — `Math.random()` for IDs/tokens, `dangerouslySetInnerHTML`,
`eval`/`new Function`, possible hardcoded secrets.
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

### Signal over noise: corpus tiering

Vendored, reference, and example code (`reference/`, `vendor/`, `third_party/`,
`examples/`, `fixtures/`, …) is **tiered out of issue counts by default**, so a
third-party folder full of `console.log`s never buries findings in your own
code. The files still appear in the graph — they're just not counted against
you. Pass `--include-vendor` to analyse them too.

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

---

## Languages

Best support for **JavaScript / TypeScript** (incl. JSX, TSX, Vue, Svelte) and
**Python** — imports, routes, components, hooks, models are all understood. A
generic resolver covers Go, Rust, Java, Ruby, PHP, C#, and more at the
file-dependency level. Categorization is heuristic and framework-agnostic.

---

## CLI

```
nodo [PATH] [options]

  PATH                 project root to scan (default: current dir)
  -o, --out DIR        output directory (default: <path>/.nodo)
  --name NAME          project name in the viewer (default: folder name)
  --open               open the HTML in your browser when done
  --init               write a sample .nodo.json and exit
  --query FILE|SYMBOL  blast radius for a file, or definition+references for a symbol
  --path A B           show the import chain connecting two files
  --explain CONCEPT    find the files & design docs related to a concept (BM25)
  --hook               install a Claude Code SessionStart hook, then exit
  --include-vendor     also analyse reference/vendored/example dirs
  --multimodal         link images/PDFs/video to the nodes near them
  --docs-only          index doc text but skip the multimodal asset pass
  --ast                EXPERIMENTAL: use tree-sitter when installed (regex fallback)
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
the Claude skill reads the image/PDF with Claude's own vision when you ask. Local
PDF *text* extraction kicks in automatically when `pypdf` is installed (still
fully offline); without it, PDFs are still indexed and linked.

## Optional: tree-sitter accuracy (`--ast`, experimental)

Nodo defaults to a fast, dependency-free regex extractor. For deeper accuracy on
large TypeScript/Python codebases you can opt into real parse trees:

```bash
pip install tree-sitter tree-sitter-language-pack
python nodo.py . --ast
```

The AST extractor uses real parse trees, so it ignores import-like strings in
comments and distinguishes a genuine `require(...)` from an identically-named
function call. If tree-sitter isn't installed, `--ast` prints a one-line note and
silently uses the regex path — the zero-dependency promise is never broken.

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

## Use with Claude Code

Nodo ships a Claude Code skill in [`.claude/skills/nodo/`](.claude/skills/nodo/).
Copy it into your project's `.claude/skills/` and type `/nodo` to regenerate the
map after a refactor. See the skill's `SKILL.md` for details.

---

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
