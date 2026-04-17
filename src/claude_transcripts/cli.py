import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="transcripts",
        description="View Claude Code sessions",
    )
    parser.add_argument("--port", type=int, default=None,
                        help="port to listen on (default: 8080, or an OS-assigned free port if 8080 is busy)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--raw", action="store_true",
                        help="serve all raw sessions from ~/.claude/projects/")
    source.add_argument("--dir", type=Path, default=None,
                        help="serve sessions from a specific directory")
    parser.add_argument("--export", type=Path, metavar="SESSION", default=None,
                        help="export a single session as a standalone HTML file instead of serving")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="output path for --export (default: <session-stem>.html in cwd)")
    args = parser.parse_args()

    if args.export is not None:
        from .export import export_session
        session_path = args.export
        output_path = args.output or Path.cwd() / f"{session_path.stem}.html"
        result = export_session(session_path, output_path)
        print(f"Wrote {result}")
        return

    from .serve import run_server
    run_server(port=args.port, raw=args.raw, directory=args.dir)


if __name__ == "__main__":
    main()
