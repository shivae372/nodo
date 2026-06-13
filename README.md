# Nodo

**Map any codebase in seconds.** Nodo scans your project, draws an interactive
dependency graph, flags code smells, and — crucially — emits clean, structured
context your AI coding agent can actually use.

Zero dependencies. Pure Python standard library. One self-contained HTML file
that works offline. No build step, no `npm install`, no account.

> Built for solo "vibe coders" who lean on Claude Code / Cursor / Windsurf, and
> for senior engineers who want a fast architectural read on an unfamiliar repo.

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

No install needed — just clone and run:

```bash
git clone https://github.com/shivae372/nodo
cd nodo
python -m nodo /path/to/your/project --open
```

Or install the `nodo` command:

```bash
pip install -e .
nodo /path/to/your/project --open
```

Run it inside a project with no path to scan the current directory:

```bash
cd my-app
python -m /path/to/nodo .       # or: nodo .
```

That's it. Open `.nodo/nodo.html` in any browser.

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
- **Hubs & Modules** — your highest-blast-radius files and detected clusters.
- **AI Context** — one-click copies: project summary, full issue backlog, or a
  scoped prompt per issue.
- **Hot Paths** (`h`) — highlight the architectural hubs and what they touch.

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

### Custom rules

Add a `.nodo.json` at your project root (`nodo --init` writes a starter):

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
  --ignore DIR         extra directory to skip (repeatable)
  --no-gitignore       don't read .gitignore for ignore dirs
  --version
```

---

## How it works

1. **Scan** — walk the tree (honoring `.gitignore` + sane defaults), read every
   source file, extract imports per language.
2. **Resolve** — turn import strings into real file edges (relative paths,
   tsconfig-style `@/` aliases, `src/` roots, Python dotted modules).
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

## License

MIT. Bundles [vis-network](https://github.com/visjs/vis-network) (MIT/Apache-2.0).

Contributions welcome — new detectors and language resolvers especially.
