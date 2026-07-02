# Changelog

## v1.3.0 — 2026-07-02

Performance, multi-language quality, and token-efficiency release. All changes
verified output-identical where they touch existing analysis (equivalence-tested
on a 2,970-file corpus); full suite 128 tests.

### Performance — thousands of files, seconds not minutes
- **Import resolution is index-backed** (tail-segment indexes) instead of scanning
  every path per import — the old path was quadratic-ish in project size.
- **Doc-mention linking inverted** (tokenize each doc once + exact-confirm
  candidates) instead of running every unique basename/stem regex over every doc:
  440.7s → 2.6s on a 2,970-file / 728-doc repo, byte-identical edges.
- **Insights cache** (entry flows, sensitive map, API routes) keyed by the same
  content signature as the detection cache — no-change rescans skip the full-text
  regex battery.
- **`--ask` / `--query <symbol>` / MCP `nodo_ask` / `nodo_who_uses` no longer hang
  on large repos** — symbol references resolve per-symbol (token-scan) instead of
  building an all-symbols alternation regex per line (was minutes / a hung MCP
  call on 3k files; now ~2–4s).
- Measured: django (2,970 files) full scan 480.7s → **18.4s**; 12,208-file
  4-language corpus **33.4s cold / 8.8s warm**.

### Languages — compiled languages get a real graph
- **Rust**: `use crate::/self::/super::` + `mod x;` extraction, `foo/mod.rs`
  convention, std filtered. cargo: 1 → **1,963 edges** (real cycle surfaced).
- **Go**: single + block imports, **stdlib filtered** (`import "errors"` can no
  longer forge an edge to a local `errors.go`). gin: 1 → **32 edges**, hubs are
  the real architecture (`binding/binding.go`, `gin.go`, `render/render.go`).
- **Java/Kotlin/Scala, C#, PHP**: dotted / backslashed module-path extraction.
- **Directory-package fallback**: imports naming a package directory (Go/Java/PHP,
  Python packages, JS barrel dirs) resolve to the package's representative file.
  Strictly additive — file-based resolution is unchanged.

### Token efficiency (MCP + hooks)
- **Lite MCP tool surface by default**: 5 tools (~0.4k tokens resident per turn)
  instead of 19 (~1.3k) — `nodo_ask` routes everything else. `--mcp-tools full`
  (or `NODO_MCP_TOOLS=full`) exposes all 19; `dispatch()` accepts all 19 in
  either mode. Trimmed tool descriptions in full mode.
- **PreToolUse nudge hook** (`--hook` / `--install`): before the session's first
  broad Grep/Glob, a once-per-session, never-blocking, ~60-token steer toward the
  map (grounded in the repo's real top hub).
- `nodo-context.md` opens with **copy-pasteable suggested queries** and caps the
  issues section with a global row budget (full detail stays in the JSON).
- **Mechanical-hub exclusion**: barrel files (`__init__.py`, `index.ts`, `mod.rs`)
  no longer crown the hubs list or "start here".

### Fixes & housekeeping
- Version consistency test: `nodo/__init__.py`, `pyproject.toml`,
  `.claude-plugin/plugin.json`, and the README Action ref must agree or the
  suite fails. (The repo workflow's own pin is a release-checklist item —
  GitHub Apps can't push workflow files.)
- `--install` MCP registration message no longer claims `pip install mcp` is
  needed (the built-in zero-dependency stdio server has shipped since 1.2.1).
- Token claims in README/skill updated to measured figures (~150–400 per query on
  a 3k-file repo).

## v1.2.2 — 2026-06-18
Importer-aware resolver hints; heal-loop completion (`--teach` clears the
self-check nag). TestResolverHints; 123 tests.

## v1.2.1 — 2026-06-17
Zero-dependency built-in MCP stdio server (no `pip install mcp` needed); plugin
MCP registration; install doc fixes.

## v1.2.0 — 2026-06-17
Advanced mode (`--deep`): symbol graph, function call graph, impact simulation,
surprising connections, knowledge graph. Self-healing (`--self-check` /
`--teach`). MCP server. Two-mode UX (`--smart`).

## v0.2.0 — 2026-06-15
Accuracy release: bundler-like resolver, corpus tiering, barrel-aware contracts,
disconnected-feature + platform-gated-dead-UI detectors, symbol-level `--query`,
doc recall, multimodal assets, opt-in tree-sitter.

## v0.1.0 — 2026-06-13
Initial release: zero-dependency codebase mapper — dependency graph, issue
detection, self-contained HTML viewer, AI-agent context files, `/nodo` skill.
