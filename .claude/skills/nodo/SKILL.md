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

If Nodo is installed (`nodo` command available):

```bash
nodo . 
```

Otherwise run it as a module from wherever the repo is cloned (adjust the path):

```bash
python -m nodo .
```

If neither works, the Nodo repo may not be on this machine — tell the user to
`git clone https://github.com/shivae372/nodo` and run `python -m /path/to/nodo .`.

### Step 2 — Report

Tell the user:
- File count, dependency count, and module count from the output
- Issue count (errors / warnings / info)
- That `.nodo/nodo.html` is ready to open in a browser
- That `.nodo/nodo-context.json` is available for AI agents to read

## When debugging with Nodo's output

- To understand an unfamiliar area, read `.nodo/nodo-context.json` — it has the
  dependency graph, the highest-coupling "hub" files, and every detected issue
  with file path + line number + a code snippet.
- To find what a change might break, look at the target file's entry in the
  graph: its `hubs` ranking and neighbour list show the blast radius.
- The issues are already deduped and severity-sorted; treat `error` first.

## Notes

- Nodo is pure standard-library Python (3.8+). No pip install or npm needed.
- It never makes network calls; code stays local.
- Custom project rules live in `.nodo.json` at the project root (`nodo --init`
  writes a starter). Add regex rules there to flag project-specific smells.
- The HTML inlines vis-network from `nodo/vendor/vis-network.min.js`, so the
  viewer works fully offline.
- To verify the canvas renders, screenshot it headless (use an **absolute**
  `--screenshot` path on Windows):
  `chrome --headless=new --disable-gpu --window-size=1600,1000 --virtual-time-budget=10000 --screenshot="<abs>/_test.png" "file://<abs>/.nodo/nodo.html"`
