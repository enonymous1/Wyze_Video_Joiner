"""Command line interface: ``wyze-join``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from wyze_joiner import __version__
from wyze_joiner.core import (
    JoinJob,
    OutputExistsError,
    WyzeJoinerError,
    join_jobs,
    plan_join,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wyze-join",
        description="Join short Wyze camera clips into longer videos.",
    )
    parser.add_argument("input", type=Path, help="Folder containing the clips to join")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output file (default: joined.mp4 in the input folder). "
            "With --split-by-day this is the output folder instead."
        ),
    )
    parser.add_argument("--recursive", action="store_true", help="Search subfolders too")
    parser.add_argument(
        "--split-by-day",
        action="store_true",
        help="Produce one joined-YYYY-MM-DD.mp4 per recording date",
    )
    parser.add_argument(
        "--strategy",
        choices=("auto", "copy", "reencode"),
        default="auto",
        help="auto: stream copy when clips match, re-encode otherwise (default)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output")
    parser.add_argument(
        "--extensions",
        default=".mp4",
        help="Comma-separated clip extensions to join (default: .mp4)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be joined without running ffmpeg",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _report_plan(jobs: Sequence[JoinJob]) -> None:
    for job in jobs:
        print(f"{len(job.clips)} clip(s) ({job.strategy}) -> {job.output_path}")
        for clip in job.clips:
            print(f"  {clip.path.name}  ({clip.duration:.1f}s, {clip.width}x{clip.height})")
        for gap in job.gaps:
            print(
                f"  Warning: {gap.gap_seconds:.1f}s gap between "
                f"{gap.previous.name} and {gap.next.name}"
            )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    extensions = tuple(ext.strip() for ext in args.extensions.split(",") if ext.strip())
    try:
        jobs = plan_join(
            args.input,
            output=args.output,
            recursive=args.recursive,
            extensions=extensions,
            strategy=args.strategy,
            force=args.force,
            split_by_day=args.split_by_day,
        )
    except (WyzeJoinerError, OutputExistsError) as exc:
        print(f"wyze-join: {exc}", file=sys.stderr)
        return 1
    _report_plan(jobs)
    if args.dry_run:
        print("Dry run: nothing was written.")
        return 0

    def _progress(fraction: float, message: str) -> None:
        # Carriage return keeps a TTY on one line; on a pipe it is just text.
        print(f"\r{message} {fraction:.0f}%", end="", flush=True)

    try:
        outputs = join_jobs(jobs, on_progress=_progress)
    except WyzeJoinerError as exc:
        print(f"\nwyze-join: {exc}", file=sys.stderr)
        return 1
    print()
    for output in outputs:
        print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
