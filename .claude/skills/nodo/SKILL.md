---
name: nodo
description: Map the current codebase with Nodo — regenerate the dependency graph, issue list, and AI-context artifacts. Use when the user types /nodo or asks to (re)generate/update the architecture map, code graph, or issue report after code changes.
---

Regenerate the Nodo architecture map for the current project.

Nodo is a zero-dependency Python tool that scans the codebase and writes an
interactive HTML viewer plus machine-readable artifacts for AI agents. It works
on any project and any language — there is nothing project-specific to configure.

## Outputs (written to `./.nodo/`)

- `nodo.html` — interactive viewer (topology graph + Issues + Hubs + AI Context). For humans.
- `nodo-context.json` — full graph, hubs, modules, every issue with line numbers + snippets. **Read this** to understand the architecture before making changes.
- `nodo-context.md` — token-cheap summary.
- `nodo-issues.txt` — plain-text issue list.

## Steps

### Step 1 — Run Nodo on the project root (pick a mode)

Nodo has two modes. Pick based on what the user wants; **default to vibe-coder**
unless they ask for depth.

**Vibe-coder mode (default — fast, zero-dependency, stay in flow):**
```bash
python /path/to/nodo/nodo.py .
```
Pure standard library, regex extraction, instant. Gives the dependency graph,
high-signal issues, hubs, data flow, and the AI-context files. This is the right
mode for day-to-day iteration and issue-spotting.

**Advanced mode (`--deep` — the heaviest, most semantic scan):**
```bash
python /path/to/nodo/nodo.py . --deep
```
Turns on **tree-sitter AST** (symbol-level, ~19 languages), **multimodal**
(docs/PDFs/images → the knowledge graph: concepts, topic communities, god-nodes),
a **function-level call graph** (`.nodo/nodo-callgraph.json` — who calls whom), and
**surprising connections** (ranked cross-module / cross-modal bridge edges written
to `nodo-report.md` + `nodo-context.json → surprises`). When the user asks "what's
surprising here?" read those and explain *why* each link matters — nodo finds the
edge + evidence; you supply the rationale. Edges also carry **provenance**
(`extracted` / `inferred` / `ambiguous`). Use advanced for big-picture/architectural
understanding, onboarding, or when the user wants depth comparable to a semantic-
graph tool. Heavier;
best to install the extras first: `pip install tree-sitter tree-sitter-language-pack markitdown`.

**Semantic vs syntactic — the division of labour.** Vibe mode is *syntactic*
(structure: files, imports, definitions). Advanced mode adds the *semantic*
scaffold (concepts, communities, call relationships) — but the deep semantic
*reasoning* ("how does auth actually work?", "what's the real data flow?") is
**yours**: you read Nodo's deterministic evidence (graph, knowledge topics, call
graph) and reason over it. Nodo never calls an LLM; you are the intelligence on
top, which is why answers stay grounded and private.

The launcher works from any directory and needs no install. If the Nodo repo is
not on this machine, tell the user to clone it first:
`git clone https://github.com/shivae372/nodo`, then run the command above with
the clone path. (If you happen to be inside the Nodo repo root, `python -m nodo .`
also works.)

After an advanced scan, you can trace functions directly:
```bash
python /path/to/nodo/nodo.py . --calls <function>   # who calls it + what it calls
```

### Step 2 — Report

Tell the user:
- File count, dependency count, and module count from the output
- Issue count (errors / warnings / info)
- That `.nodo/nodo.html` is ready to open in a browser
- That `.nodo/nodo-context.json` is available for AI agents to read

## When debugging with Nodo's output

- **Ask anything (start here) — but YOU are the brain on top.** `--ask` is
  offline and deterministic: it returns structured *evidence* fast (blast radius,
  who-uses, issues, hubs, concept matches, or a project overview) and labels how it
  interpreted the question with a `[nodo · mode: target]` header. It is heuristic,
  not authoritative.

  ```bash
  python /path/to/nodo/nodo.py . --ask "what breaks if I change lib/auth.ts?"
  python /path/to/nodo/nodo.py . --ask "what should I fix in checkout.ts?"
  python /path/to/nodo/nodo.py . --ask "how does the router reach the database?"
  python /path/to/nodo/nodo.py . --ask "what does this project do?"
  ```

  **Your job:** run `--ask` first (it saves you the grepping/reading), then READ
  the result and tell the user only the **correct, relevant** parts — apply your
  judgment, don't blindly echo it. Use the `[nodo · …]` header to check the
  interpretation: if nodo guessed the wrong lens (e.g. did a concept search when
  the user meant a specific symbol), re-ask more specifically or use the exact flag
  below. When correctness matters, confirm against the actual code — nodo finds the
  spot fast; you guarantee the answer is right. That division (nodo = instant
  offline evidence, you = the judgment) is what saves time and prevents wrong
  answers.
- **Cheapest impact check (preferred over reading files):** to learn what a file
  depends on and what breaks if you change it, run a query instead of opening
  files — it answers in ~200 tokens:

  ```bash
  python /path/to/nodo/nodo.py . --query path/to/file.ts
  ```

  It prints the file's dependents (who imports it), dependencies, hub rank, and
  any issues — no rescan, reads the existing map.
- **Is a symbol used, or dead?** `--query` also takes a symbol name, not just a
  file. Use it to confirm whether a function/class/feature is actually wired in
  before you touch it (self-verifying — no grep needed):

  ```bash
  python /path/to/nodo/nodo.py . --query AudioEngine
  ```

  Prints where the symbol is defined and every file that references it, or
  "0 files — nothing references this symbol" for a confirmed orphan.
- **How does A connect to B:** trace the import chain between two files:

  ```bash
  python /path/to/nodo/nodo.py . --path path/to/a.ts path/to/b.ts
  ```

- **Where does a concept live (code + design docs):** BM25 search (handles
  synonyms, e.g. `auth` also matches login/jwt/session) — use this instead of
  grepping to locate a feature. It also searches the project's Markdown/spec
  docs, so you can find the doc that *defines* a feature and judge the code
  against intent:

  ```bash
  python /path/to/nodo/nodo.py . --explain "authentication"
  ```

- **Broken-feature findings to act on:** the issue list flags two high-value
  structural smells an AI agent commonly introduces — `Disconnected feature
  (implemented but unreferenced)` (a file with real surface area that nothing
  imports) and `Platform-gated dead UI` (handlers that no-op outside their
  platform). When you see these, verify with `--query <symbol>` before assuming
  code is live.
- **Noise control:** reference/vendored/example dirs are excluded from issue
  counts by default. Pass `--include-vendor` only if you specifically need to
  audit third-party code.

- For a readable narrative overview, read `.nodo/nodo-report.md` (corpus,
  load-bearing files, security posture, primary flows, code health).
- To understand an unfamiliar area in full, read `.nodo/nodo-context.json` — it
  has the dependency graph (`files` + `edges`), hubs, modules, auto-derived
  flows + sensitive surfaces, and every issue with line number + snippet.
- The issues are already deduped and severity-sorted; treat `error` first.

## Multimodal knowledge graph & AI queries

Nodo turns the project's docs and PDFs into a **knowledge graph**: it mines
concepts, clusters them into **topics (communities)**, and links docs/PDFs ↔
concepts ↔ code in one graph. Nodo builds this deterministically and offline;
**you (Claude) are the AI query layer** on top.

To answer a semantic question about the project ("how does auth work?", "what's
the payment flow?", "what does this diagram show?"):

1. **Orient with topics** — read `.nodo/nodo-knowledge.md` (topics + their
   concepts + source docs/PDFs) or run:
   ```bash
   python /path/to/nodo/nodo.py . --topics --full
   ```
2. **Locate** the relevant sources with `--explain "<concept>"` (searches code +
   docs + PDF text).
3. **Read and reason** over those sources to answer. PDFs, Word/PowerPoint/Excel,
   HTML and the like are **pre-converted to Markdown** at `.nodo/converted/…` —
   **read that token-cheap `.md` instead of the raw document** (a PDF can cost
   10–100× the tokens of its plain text). Each asset's `converted` path is in
   `nodo-context.json` → `assets`. For **images** (and diagrams a converter can't
   read), open the file and use your own **vision**, then SAVE a 2–3 sentence
   description to `.nodo/converted/<rel-with-/-as-__>.md` (e.g. `docs/arch.png` →
   `.nodo/converted/docs__arch.png.md`). Nodo preserves that file and folds it into
   the knowledge graph on the next scan — your visual understanding becomes a
   pinned, queryable node while the core stays offline. Install Microsoft
   `markitdown` (`pip install markitdown`, Python 3.10+) for the broadest text
   conversion; PDFs convert via `pypdf` otherwise.
4. Build the graph with PDFs/images included via `--full` (or `--multimodal`).

`nodo-context.json` carries the full picture: `knowledge.topics`,
`knowledge.concepts`, the `concept`/`doc`/`asset` nodes, and `kind:"reference"`
edges connecting them.

## Optional: auto-load the map every session

Run once per project to install a Claude Code SessionStart hook so the map is
injected into context automatically at the start of every session (no manual
reading, saves tokens):

```bash
python /path/to/nodo/nodo.py . --hook
```

After that, regenerate the map with the skill whenever the code changes; the
hook always serves the latest `.nodo/nodo-context.md`.

## Live tools via MCP (optional)

Nodo can also run as an MCP server so you can call it as **tools mid-session**
(not just read the context file at the start): `python /path/to/nodo/nodo.py --mcp .`
(needs `pip install mcp`). It exposes `nodo_ask`, `nodo_blast_radius`,
`nodo_who_uses`, `nodo_path`, `nodo_explain`, `nodo_list_issues`, `nodo_hubs`,
`nodo_topics`, `nodo_overview`, `nodo_refresh`, `nodo_fix_context` (the structured
`<context>` prompt for a file's issues — evidence to act on), `nodo_changed`
(diff-aware blast radius of recent edits), `nodo_calls` (a function's call graph),
`nodo_surprises` (cross-module / cross-modal bridge edges — see Advanced mode),
plus `nodo_self_check` and `nodo_teach` (see Self-healing below). `nodo.py .
--install` registers it in `.mcp.json`. Same rule applies: these are fast offline
*evidence* — you read the result and tell the user the correct part.

## Self-healing: teach nodo when it's blind (you are the tutor)

Nodo is deterministic and offline, so it has hard edges: a language it has no
grammar for, a file it parsed but pulled nothing from, an aliased import it
couldn't resolve, or a dynamically-loaded file it wrongly calls "dead". **You
(Claude) are the intelligence that heals those edges** — and the fix persists, so
nodo gets smarter about this codebase every time.

**When to teach (watch for these):**
- A scan prints a `self-check:` nudge, or `--self-check` / `nodo_self_check`
  reports gaps.
- You notice nodo missed a language, a symbol, or an import you can see in the code.
- You verify that a file nodo flagged as `Disconnected feature` / dead is actually
  reached (dynamically, via reflection, a plugin registry, a framework entrypoint).

**The loop:**
1. **Diagnose** — run `python /path/to/nodo/nodo.py . --self-check` (or call
   `nodo_self_check`). It prints unknown languages, silent files, and unresolved
   local imports — and for an unknown language it **auto-drafts a lesson**: the
   def/import regexes are induced from the real files (deterministic), and it
   reports how many symbols/imports the draft would extract so you can judge it.
2. **Tutor — confirm or refine the draft** — READ a couple of the flagged files
   and check the auto-drafted regexes against what the code actually looks like.
   Often you just confirm it; refine when the draft missed a declaration shape.
   You're still the brain — verify, don't rubber-stamp. Derive each regex from
   the real code, one capture group each (symbol name / import target).
   - `languages.<name>`: `extensions`, `def_patterns` (capture the symbol name),
     `import_patterns` (capture the import target), optional `category`.
   - `keep_alive`: a file path or symbol to stop flagging as dead (use only after
     you've confirmed it IS reached — this never hides a real bug like a secret).
   - `resolver_hints`: `{ "<import string>": "real/rel/path.ext" }` for an alias
     nodo couldn't resolve.
3. **Heal** — `python /path/to/nodo/nodo.py . --teach lesson.json` (or call
   `nodo_teach` with the lesson object). It validates (bad regexes are rejected),
   saves to `.nodo/lessons.json`, and applies on every future scan. Re-scan and the
   language is first-class: real nodes, resolved import edges, working
   `--query <symbol>`.

Example lesson (taught after looking at the project's `.zig` files):

```json
{
  "languages": {
    "zig": {
      "extensions": [".zig"],
      "def_patterns": ["\\bfn\\s+([A-Za-z_]\\w*)", "\\bconst\\s+(\\w+)\\s*=\\s*struct"],
      "import_patterns": ["@import\\(\"([^\"]+)\"\\)"]
    }
  },
  "keep_alive": ["src/plugins/registry.ts"],
  "resolver_hints": { "@generated/api": "src/api/gen.ts" }
}
```

This is the durable version of the division of labour: nodo finds where it's
blind; you supply the understanding; nodo remembers it forever. Don't fabricate
patterns — read the files first, then teach what's actually there.

Shortcuts: if `tree-sitter-language-pack` ships a grammar for the language, add
`"grammar": "<name>"` to the language instead of regexes and nodo uses the real
parse tree. `--teach <dir>` ingests every `*.json` lesson in a folder (curated
starters live in `examples/lessons/`). A lesson may also carry `keep_alive` (stop
flagging a confirmed-live file as dead) and `resolver_hints` (resolve an alias
nodo missed).

## Notes

- Nodo is pure standard-library Python (3.8+). No pip install or npm needed.
- It never makes network calls; code stays local.
- **Multimodal + unified graph:** docs and assets are first-class graph nodes,
  not a side list. Doc nodes link to the code they describe (by markdown link AND
  by module name — a spec naming `AudioEngine` connects to `AudioEngine.js`);
  asset nodes (images/PDFs/video) link from whatever references them. These
  `kind:"reference"` edges are separate from code `kind:"import"` edges, so
  `--query`/`--path` still trace imports only. `--multimodal` includes binary
  assets; *you* (Claude) read their contents with your own vision when asked.
  `--docs-only` skips binary assets. Bare TTY runs prompt; default is docs-only.
- **Optional accuracy:** `--ast` uses tree-sitter when installed
  (`pip install tree-sitter tree-sitter-language-pack`) and silently falls back
  to the zero-dep regex extractor otherwise — never required.
- Custom project rules live in `.nodo.json` at the project root (`nodo --init`
  writes a starter). Add regex rules there to flag project-specific smells.
- The HTML inlines vis-network from `nodo/vendor/vis-network.min.js`, so the
  viewer works fully offline.
- To verify the canvas renders, screenshot it headless (use an **absolute**
  `--screenshot` path on Windows):
  `chrome --headless=new --disable-gpu --window-size=1600,1000 --virtual-time-budget=10000 --screenshot="<abs>/_test.png" "file://<abs>/.nodo/nodo.html"`
