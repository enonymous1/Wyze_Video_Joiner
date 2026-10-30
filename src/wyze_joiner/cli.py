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
    plan_record_join,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wyze-join",
        description="Join short Wyze camera clips into longer videos.",
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        default=None,
        help="Folder containing the clips to join (omit with --record-dir)",
    )
    parser.add_argument(
        "--record-dir",
        type=Path,
        default=None,
        help=(
            "Wyze SD-card record folder (record/<YYYYMMDD>/<HH>/<numeric>.mp4): "
            "plan one join per hour instead of one join for a single folder"
        ),
    )
    parser.add_argument(
        "--record-output-dir",
        type=Path,
        default=None,
        help=(
            "Output folder for --record-dir (default: each hour's <HH>.mp4 inside its date folder)"
        ),
    )
    parser.add_argument(
        "--max-size-gb",
        type=float,
        default=None,
        help="With --record-dir, skip hours larger than this size in GB",
    )
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


def _record_progress(fraction: float, message: str) -> None:
    # Carriage return keeps a TTY on one line; on a pipe it is just text.
    print(f"\r{message} {fraction:.0f}%", end="", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.record_dir is not None:
        if args.input is not None:
            print(
                "wyze-join: give either an input folder or --record-dir, not both",
                file=sys.stderr,
            )
            return 2
        if args.output is not None:
            print(
                "wyze-join: with --record-dir, use --record-output-dir instead of --output",
                file=sys.stderr,
            )
            return 2
        try:
            batch = plan_record_join(
                args.record_dir,
                output_dir=args.record_output_dir,
                strategy=args.strategy,
                force=args.force,
                max_size_gb=args.max_size_gb,
            )
        except (WyzeJoinerError, OutputExistsError) as exc:
            print(f"wyze-join: {exc}", file=sys.stderr)
            return 1
        _report_plan(batch.jobs)
        for skip in batch.skipped:
            print(f"Skipped {skip.hour_dir}: {skip.reason}")
        if args.dry_run:
            print("Dry run: nothing was written.")
            return 0
        if not batch.jobs:
            print("Nothing to join.")
            return 0
        try:
            outputs = join_jobs(batch.jobs, on_progress=_record_progress)
        except WyzeJoinerError as exc:
            print(f"\nwyze-join: {exc}", file=sys.stderr)
            return 1
        print()
        for output in outputs:
            print(f"Wrote {output}")
        return 0
    if args.input is None:
        print(
            "wyze-join: an input folder is required (or use --record-dir)",
            file=sys.stderr,
        )
        return 2
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
