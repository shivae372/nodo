"""
Claude Code integration: auto-feed the codebase map to the agent.

`--hook` installs a SessionStart hook into the project's .claude/settings.json so
that every time an agent (Claude Code) starts a session in this project, it
silently receives Nodo's token-cheap architecture summary — no grepping, no
re-reading files to rebuild context. A silent agent-integration hook, done in
pure stdlib.

`--emit-context` is what the hook actually runs: it prints a JSON envelope with
`additionalContext` containing nodo-context.md, which Claude Code injects into
the session. If the map is stale or missing, it degrades gracefully.
"""
import json
from pathlib import Path


def emit_context(out_dir):
    """Print the SessionStart JSON envelope that Claude Code injects.

    Wraps nodo-context.md (the token-cheap summary). Always prints valid JSON so
    the hook never breaks a session, even if the map is missing.
    """
    md_path = Path(out_dir) / 'nodo-context.md'
    if md_path.exists():
        summary = md_path.read_text(encoding='utf-8', errors='ignore')
        note = (
            "\n\n---\n"
            "The above is a Nodo architecture map of this project. Use it before "
            "grepping. To check what a specific file depends on or what breaks if "
            "you change it, run:\n"
            "    python <nodo>/nodo.py . --query <path/to/file>\n"
            "Full machine-readable graph + every issue with line numbers + snippets "
            "is in .nodo/nodo-context.json.\n"
        )
        context = summary + note
    else:
        context = (
            "Nodo architecture map not generated yet. Run `python <nodo>/nodo.py .` "
            "in this project to produce .nodo/nodo-context.md (a token-cheap "
            "architecture + issues summary) and .nodo/nodo-context.json."
        )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


def emit_nudge(out_dir):
    """PreToolUse hook body: before the agent's first broad Grep/Glob of the session,
    inject a ONE-TIME, ~60-token nudge to consult the nodo map instead of scanning
    raw files (the moment tokens are about to burn — the map answers structure and
    impact questions for a fraction of the cost). Graphify-style steering, but
    deduped per session so the hook itself never becomes a token tax.

    Reads the hook JSON from stdin (session_id), emits additionalContext only once
    per session, and always prints valid JSON — on any error it prints {} so the
    hook can never break or block a session (it never sets permissionDecision).
    """
    import sys
    try:
        try:
            payload = json.loads(sys.stdin.read() or '{}')
        except Exception:
            payload = {}
        session = str(payload.get('session_id') or 'default')[:64]
        out = Path(out_dir)
        md_path = out / 'nodo-context.md'
        if not md_path.exists():                      # no map → stay silent
            print('{}')
            return
        marker_dir = out / '.nudge'
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker = marker_dir / f'{"".join(c for c in session if c.isalnum() or c in "-_") or "default"}'
        if marker.exists():                           # already nudged this session
            print('{}')
            return
        # prune stale markers so the dir never grows unbounded
        try:
            import time as _t
            cutoff = _t.time() - 7 * 86400
            for m in marker_dir.iterdir():
                if m.stat().st_mtime < cutoff:
                    m.unlink()
        except Exception:
            pass
        marker.write_text('1', encoding='utf-8')
        # ground the nudge in THIS repo: name its real load-bearing file
        hub = ''
        try:
            ctx = json.loads((out / 'nodo-context.json').read_text(encoding='utf-8',
                                                                   errors='ignore'))
            hubs = ctx.get('hubs') or []
            if hubs:
                hub = hubs[0].get('file', '')
        except Exception:
            pass
        example = f' (e.g. --query {hub})' if hub else ''
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": (
                    "This repo has a nodo codebase map — cheaper than broad grep/reads "
                    "for structure and impact questions. Try `nodo . --ask \"<question>\"` "
                    f"or `--query <file>` for blast radius{example}. "
                    "Summary: .nodo/nodo-context.md"
                ),
            }
        }))
    except Exception:
        print('{}')


def _hook_command(nodo_launcher):
    """The command the hook runs. Uses an absolute path to this repo's launcher."""
    launcher = str(Path(nodo_launcher).resolve()).replace('\\', '/')
    return f'python "{launcher}" . --emit-context'


def _nudge_command(nodo_launcher):
    launcher = str(Path(nodo_launcher).resolve()).replace('\\', '/')
    return f'python "{launcher}" . --emit-nudge'


def install_hook(project_root, nodo_launcher):
    """Add a SessionStart hook to <project_root>/.claude/settings.json.

    Idempotent: if an equivalent Nodo hook already exists, it is updated in place
    rather than duplicated. Returns a human-readable status string.
    """
    project_root = Path(project_root)
    settings_dir = project_root / '.claude'
    settings_dir.mkdir(exist_ok=True)
    settings_path = settings_dir / 'settings.json'

    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding='utf-8', errors='ignore'))
        except Exception:
            return (f'error: {settings_path} exists but is not valid JSON. '
                    'Fix or remove it, then re-run --hook.')

    command = _hook_command(nodo_launcher)
    hook_entry = {
        'matcher': 'startup',
        'hooks': [{
            'type': 'command',
            'command': command,
            'timeout': 15,
            'statusMessage': 'Loading Nodo architecture map...',
        }],
    }

    hooks = settings.setdefault('hooks', {})
    session_start = hooks.setdefault('SessionStart', [])

    # remove any prior Nodo hook (identified by --emit-context in the command)
    before = len(session_start)
    session_start[:] = [
        h for h in session_start
        if not any('--emit-context' in (sub.get('command', ''))
                   for sub in h.get('hooks', []))
    ]
    replaced = len(session_start) < before
    session_start.append(hook_entry)

    # PreToolUse steering (Graphify-style): before the session's FIRST broad
    # Grep/Glob, nudge the agent toward the map instead of raw file scans.
    # --emit-nudge is once-per-session and never blocks, so the hook itself
    # stays token-cheap.
    nudge_entry = {
        'matcher': 'Grep|Glob',
        'hooks': [{
            'type': 'command',
            'command': _nudge_command(nodo_launcher),
            'timeout': 10,
        }],
    }
    pre_tool = hooks.setdefault('PreToolUse', [])
    pre_tool[:] = [
        h for h in pre_tool
        if not any('--emit-nudge' in (sub.get('command', ''))
                   for sub in h.get('hooks', []))
    ]
    pre_tool.append(nudge_entry)

    settings_path.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    verb = 'Updated' if replaced else 'Installed'
    return (f'{verb} Nodo hooks in {settings_path}\n'
            f'  SessionStart: {command}\n'
            f'  PreToolUse (Grep|Glob): {_nudge_command(nodo_launcher)} '
            '(one-time per session map nudge)\n'
            '  Claude Code will now load the architecture map automatically at session start\n'
            '  and be steered to the map before its first broad file scan.\n'
            '  (Run a normal scan first so .nodo/nodo-context.md exists.)')


_NODO_START = '<!-- nodo:start -->'
_NODO_END = '<!-- nodo:end -->'


def _agent_instructions(nodo_launcher):
    launcher = str(Path(nodo_launcher).resolve()).replace('\\', '/')
    return (
        "## Codebase map (Nodo)\n\n"
        "This repo has a Nodo map in `.nodo/`. Before grepping or reading files "
        "to understand the code:\n\n"
        "1. Read `.nodo/nodo-context.md` — token-cheap architecture + issues "
        "(each issue has a confidence: act on `high` first).\n"
        f"2. Blast radius / change-impact for a file or symbol: "
        f"`python \"{launcher}\" . --query <path-or-symbol>`\n"
        f"3. How does A reach B: `python \"{launcher}\" . --path <a> <b>`\n"
        f"4. Where a concept lives (code + docs + PDFs): "
        f"`python \"{launcher}\" . --explain \"<concept>\"`\n"
        f"5. Doc/PDF knowledge topics: `python \"{launcher}\" . --topics`\n\n"
        "Converted (token-cheap) Markdown for PDFs/Office files is in "
        "`.nodo/converted/`. Full graph + issues: `.nodo/nodo-context.json`.\n"
    )


def _upsert_block(path, block):
    """Idempotently insert/replace a sentinel-wrapped block in a file."""
    section = f'{_NODO_START}\n{block}{_NODO_END}\n'
    if path.exists():
        text = path.read_text(encoding='utf-8', errors='ignore')
        if _NODO_START in text and _NODO_END in text:
            pre = text.split(_NODO_START)[0]
            post = text.split(_NODO_END, 1)[1]
            text = pre + section + post
        else:
            text = text.rstrip() + '\n\n' + section
    else:
        text = '# Agent guide\n\n' + section
    path.write_text(text, encoding='utf-8')


def install_agents(project_root, nodo_launcher):
    """Wire the Nodo map into multiple AI assistants (not just Claude):
    AGENTS.md (Codex / Windsurf / Amp / OpenCode / others) and a Cursor rule.
    Idempotent. Returns a status string."""
    project_root = Path(project_root)
    block = _agent_instructions(nodo_launcher)
    written = []

    _upsert_block(project_root / 'AGENTS.md', block)
    written.append('AGENTS.md (Codex/Windsurf/Amp/OpenCode/…)')

    cursor_dir = project_root / '.cursor' / 'rules'
    cursor_dir.mkdir(parents=True, exist_ok=True)
    (cursor_dir / 'nodo.mdc').write_text(
        '---\ndescription: Use the Nodo codebase map before exploring\n'
        'alwaysApply: true\n---\n\n' + block, encoding='utf-8')
    written.append('.cursor/rules/nodo.mdc (Cursor)')

    return 'Installed Nodo agent instructions:\n  - ' + '\n  - '.join(written)


def install_mcp(project_root, nodo_launcher):
    """Register the nodo MCP server in `.mcp.json` (read by Claude Code, Cursor, and
    other MCP clients), merging into any existing config. Idempotent. Returns status."""
    import json
    project_root = Path(project_root)
    launcher = str(Path(nodo_launcher).resolve()).replace('\\', '/')
    p = project_root / '.mcp.json'
    cfg = {}
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
        except Exception:
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    servers = cfg.get('mcpServers')
    if not isinstance(servers, dict):
        servers = {}
    servers['nodo'] = {'command': 'python', 'args': [launcher, '--mcp', '.']}
    cfg['mcpServers'] = servers
    try:
        p.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
        return ('.mcp.json — registered the "nodo" MCP server for Claude Code / Cursor. '
                'Zero-dep built-in server; `pip install mcp` only for the official SDK. '
                'Default tool surface is lite (token-cheap) — add --mcp-tools full for all tools.')
    except Exception as e:
        return f'could not write .mcp.json: {e}'
