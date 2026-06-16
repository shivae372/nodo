# Community lessons

Curated, ready-to-`--teach` lessons for languages nodo doesn't parse out of the
box. **Local-only — nodo never fetches these over the network.** You copy the
file (or this folder) into reach and teach it:

```bash
# teach one language
python /path/to/nodo/nodo.py . --teach examples/lessons/zig.json

# teach a whole folder at once (every *.json in it)
python /path/to/nodo/nodo.py . --teach examples/lessons/
```

Each lesson is plain JSON validated on `--teach` (bad regex → rejected), then
saved to your project's `.nodo/lessons.json` and applied on every scan. Edit them
freely — they're a starting point, not gospel.

## What's here

| File | Language | Extracts |
|---|---|---|
| `zig.json` | Zig (`.zig`) | `fn` / `pub fn`, `const`, `@import("...")` |
| `nim.json` | Nim (`.nim`) | `proc`/`func`/`template`/`macro`/`type`, `import` / `from..import` |

## Contributing a lesson

A lesson maps an extension to how its language declares **definitions** and
**imports** (regex, one capture group each), and may include corrections:

```json
{
  "languages": {
    "mylang": {
      "extensions": [".ml2"],
      "category": "lib",
      "def_patterns": ["(?m)^\\s*def\\s+([A-Za-z_]\\w*)"],
      "import_patterns": ["(?m)^\\s*use\\s+\"([^\"]+)\""],
      "grammar": "optional-tree-sitter-grammar-name"
    }
  }
}
```

Tip: run `nodo . --self-check` inside a project using the language — nodo
**auto-drafts** most of this for you from the real files; you just confirm it.
If `tree-sitter-language-pack` ships a grammar for the language, add
`"grammar": "<name>"` and nodo uses the real parse tree (no regex needed).
