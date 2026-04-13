# claude-transcripts

A tool for viewing and annotating Claude Code sessions. The viewer is a small Python package with a bundled HTML UI; annotation is a Claude Code subagent that lives in the cloned repo's `.claude/agents/` directory and is picked up automatically when Claude Code runs inside the clone.

## Quick try (clone and run)

```bash
git clone https://github.com/davidcpage/claude-transcripts
cd claude-transcripts

# Launch the viewer
uv run transcripts                    # default: ~/.claude/annotated-sessions/
uv run transcripts --raw              # all raw sessions from ~/.claude/projects/
uv run transcripts --dir ./some/dir   # a specific directory of .jsonl files
```

`uv run` detects `pyproject.toml`, creates `.venv/`, installs the project in editable mode, and runs the `transcripts` console script. There are no runtime dependencies, so the first sync is nearly instant. Subsequent runs reuse the venv.

To annotate a session, open Claude Code **in this cloned directory** and ask it to annotate a session — by full path to a `.jsonl` file or by what-three-words slug (e.g. `nifty-snacking-harbor`). The `session-annotator` subagent is discovered from `.claude/agents/session-annotator.md` and runs the full pipeline: slug resolution, compression, analysis, and writing an annotated copy to `~/.claude/annotated-sessions/`. You can annotate several sessions in one request and they will run in parallel.

Annotation only works when Claude Code is running inside the clone, because that's where the agent files live.

## Viewer-only without cloning

```bash
uvx --from git+https://github.com/davidcpage/claude-transcripts transcripts --raw
```

This runs the viewer ephemerally without cloning, against your raw sessions under `~/.claude/projects/`. It does **not** give you the annotation subagent — for that, use the clone-and-run path above.

## Credits and related tools

- The viewer's HTML display format was initially inspired by [hansonw's Codex session gist](https://gist.github.com/hansonw/db53a79e266310585024ab774f6a3845). The design has iterated since then, but the original shape and many specific details are still visible.
- See also [simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) (Apache 2.0) — a related tool that renders Claude Code sessions as static HTML with built-in GitHub Gist publishing.
