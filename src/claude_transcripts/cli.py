import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="transcripts",
        description="View Claude Code sessions",
    )
    parser.add_argument("--port", type=int, default=8080,
                        help="port to listen on (default: 8080)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--raw", action="store_true",
                        help="serve all raw sessions from ~/.claude/projects/")
    source.add_argument("--dir", type=Path, default=None,
                        help="serve sessions from a specific directory")
    args = parser.parse_args()

    from .serve import run_server
    run_server(port=args.port, raw=args.raw, directory=args.dir)


if __name__ == "__main__":
    main()
