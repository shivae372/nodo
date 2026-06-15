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

### Step 1 — Run Nodo on the project root

Run the launcher from the cloned Nodo repo, pointing it at the current project
(`.`). Replace `/path/to/nodo` with wherever the repo lives on this machine:

```bash
python /path/to/nodo/nodo.py .
```

The launcher works from any directory and needs no install. If the Nodo repo is
not on this machine, tell the user to clone it first:
`git clone https://github.com/shivae372/nodo`, then run the command above with
the clone path. (If you happen to be inside the Nodo repo root, `python -m nodo .`
also works.)

### Step 2 — Report

Tell the user:
- File count, dependency count, and module count from the output
- Issue count (errors / warnings / info)
- That `.nodo/nodo.html` is ready to open in a browser
- That `.nodo/nodo-context.json` is available for AI agents to read

## When debugging with Nodo's output

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

## Optional: auto-load the map every session

Run once per project to install a Claude Code SessionStart hook so the map is
injected into context automatically at the start of every session (no manual
reading, saves tokens):

```bash
python /path/to/nodo/nodo.py . --hook
```

After that, regenerate the map with the skill whenever the code changes; the
hook always serves the latest `.nodo/nodo-context.md`.

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
