"""CLI tests: dry run writes nothing, a real run writes one file."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_clip
from wyze_joiner.cli import main
from wyze_joiner.core import probe_clip


def test_cli_dry_run_writes_nothing(clips_dir: Path) -> None:
    make_clip(clips_dir, "a.mp4")
    make_clip(clips_dir, "b.mp4")
    output = clips_dir / "joined.mp4"
    assert main([str(clips_dir), "--output", str(output), "--dry-run"]) == 0
    assert not output.exists()


def test_cli_join_and_missing_folder(clips_dir: Path) -> None:
    make_clip(clips_dir, "a.mp4")
    make_clip(clips_dir, "b.mp4")
    output = clips_dir / "joined.mp4"
    assert main([str(clips_dir), "--output", str(output)]) == 0
    assert 1.8 <= probe_clip(output).duration <= 2.2
    assert main([str(clips_dir / "does-not-exist")]) == 1
