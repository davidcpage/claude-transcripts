# claude-transcripts

A small Python package for viewing Claude Code session JSONL files in a browser.

## Layout

- `src/claude_transcripts/cli.py` — `transcripts` entry point. Dispatches to either the server or the standalone exporter based on flags.
- `src/claude_transcripts/serve.py` — local HTTP server. Three source modes: default `~/.claude/annotated-sessions/`, `--raw` for `~/.claude/projects/`, or `--dir` for an arbitrary directory. Serves `index.html` plus JSON endpoints for listing and streaming sessions.
- `src/claude_transcripts/export.py` — bundles a single session into a self-contained HTML file by reading `index.html` and injecting `window.INLINE_SESSION = {...}` ahead of the existing viewer script.
- `src/claude_transcripts/index.html` — the entire viewer (HTML + CSS + inline JS, ~1500 lines). All rendering, scrollspy, the token rail, and section collapse logic lives here.

## Key thing to know

The viewer is a single-file inline-JS app. `serve.py` and `export.py` both deliver the same `index.html`; the exporter just prepends an inline `<script>` that sets `window.INLINE_SESSION` so the page hydrates from a literal instead of fetching. **Edits to `index.html` automatically affect both the served viewer and exported standalone files** — no template duplication.
