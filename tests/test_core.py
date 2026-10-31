"""Core tests, run against real ffmpeg/ffprobe output."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import make_clip
from wyze_joiner.core import (
    OutputExistsError,
    WyzeJoinerError,
    discover_clips,
    join_jobs,
    plan_join,
    probe_clip,
)


def test_discover_sorts_and_ignores_non_clips_and_previous_outputs(clips_dir: Path) -> None:
    make_clip(clips_dir, "b.mp4")
    make_clip(clips_dir, "a.mp4")
    make_clip(clips_dir, "joined.mp4")
    (clips_dir / "notes.txt").write_text("not a clip")
    found = discover_clips(clips_dir)
    assert [path.name for path in found] == ["a.mp4", "b.mp4"]


def test_join_handles_quote_in_filename(clips_dir: Path) -> None:
    # The concat demuxer quoting must survive a literal apostrophe in a
    # clip name; this is the regression test for that escaping.
    make_clip(clips_dir, "it's.mp4", color="red")
    make_clip(clips_dir, "plain.mp4", color="blue")
    jobs = plan_join(clips_dir, output=clips_dir / "joined.mp4")
    assert [clip.path.name for clip in jobs[0].clips] == ["it's.mp4", "plain.mp4"]
    outputs = join_jobs(jobs)
    assert 1.8 <= probe_clip(outputs[0]).duration <= 2.2


def test_discover_recursive(clips_dir: Path) -> None:
    nested = clips_dir / "2024-01-01"
    nested.mkdir()
    make_clip(clips_dir, "a.mp4")
    make_clip(nested, "b.mp4")
    assert [path.name for path in discover_clips(clips_dir)] == ["a.mp4"]
    # Recursive order is by relative path, so the dated folder sorts first.
    assert [path.name for path in discover_clips(clips_dir, recursive=True)] == ["b.mp4", "a.mp4"]


def test_join_stream_copy_preserves_order_and_duration(clips_dir: Path) -> None:
    for name, color in (("a.mp4", "red"), ("b.mp4", "green"), ("c.mp4", "blue")):
        make_clip(clips_dir, name, color=color)
    output = clips_dir / "joined.mp4"
    jobs = plan_join(clips_dir, output=output)
    assert len(jobs) == 1
    assert jobs[0].strategy == "copy"
    assert [clip.path.name for clip in jobs[0].clips] == ["a.mp4", "b.mp4", "c.mp4"]
    outputs = join_jobs(jobs)
    assert outputs == [output]
    info = probe_clip(output)
    assert 2.8 <= info.duration <= 3.2
    # Joining exactly once: a second run without --force must refuse.
    with pytest.raises(OutputExistsError):
        plan_join(clips_dir, output=output)
    jobs_forced = plan_join(clips_dir, output=output, force=True)
    assert join_jobs(jobs_forced) == [output]


def test_output_is_never_an_input(clips_dir: Path) -> None:
    make_clip(clips_dir, "a.mp4")
    make_clip(clips_dir, "b.mp4")
    output = clips_dir / "joined.mp4"
    join_jobs(plan_join(clips_dir, output=output))
    # The joined file now sits in the source folder; a fresh plan must
    # still see only the two source clips, not joined.mp4 itself.
    jobs = plan_join(clips_dir, output=clips_dir / "joined2.mp4")
    assert [clip.path.name for clip in jobs[0].clips] == ["a.mp4", "b.mp4"]


def test_mismatched_clips_reencode_to_first_clip_size(clips_dir: Path) -> None:
    make_clip(clips_dir, "a.mp4", size="128x72")
    make_clip(clips_dir, "b.mp4", size="64x64")
    output = clips_dir / "joined.mp4"
    jobs = plan_join(clips_dir, output=output)
    assert jobs[0].strategy == "reencode"
    join_jobs(jobs)
    info = probe_clip(output)
    assert (info.width, info.height) == (128, 72)


def test_copy_strategy_refuses_mismatched_clips(clips_dir: Path) -> None:
    make_clip(clips_dir, "a.mp4", size="128x72")
    make_clip(clips_dir, "b.mp4", size="64x64")
    with pytest.raises(WyzeJoinerError, match="stream copy"):
        plan_join(clips_dir, output=clips_dir / "joined.mp4", strategy="copy")


def test_no_clips_raises(clips_dir: Path) -> None:
    with pytest.raises(WyzeJoinerError, match="No video clips"):
        plan_join(clips_dir, output=clips_dir / "joined.mp4")


def test_split_by_day(clips_dir: Path) -> None:
    make_clip(clips_dir, "20240101_120000.mp4")
    make_clip(clips_dir, "20240101_120100.mp4")
    make_clip(clips_dir, "20240102_120000.mp4")
    jobs = plan_join(clips_dir, split_by_day=True)
    assert [job.output_path.name for job in jobs] == [
        "joined-2024-01-01.mp4",
        "joined-2024-01-02.mp4",
    ]
    outputs = join_jobs(jobs)
    assert len(outputs) == 2
    assert 1.8 <= probe_clip(outputs[0]).duration <= 2.2
    assert 0.8 <= probe_clip(outputs[1]).duration <= 1.2


def test_split_by_day_refuses_undated_clips(clips_dir: Path) -> None:
    make_clip(clips_dir, "clip.mp4")
    with pytest.raises(WyzeJoinerError, match="no YYYYMMDD"):
        plan_join(clips_dir, split_by_day=True)


def test_gap_detection_from_timestamps(clips_dir: Path) -> None:
    # 1s clips at 12:00:00, 12:00:01, then 12:00:03: the 12:00:02 minute
    # (second, in this synthetic case) is uncovered.
    make_clip(clips_dir, "20240101_120000.mp4")
    make_clip(clips_dir, "20240101_120001.mp4")
    make_clip(clips_dir, "20240101_120003.mp4")
    jobs = plan_join(clips_dir, output=clips_dir / "joined.mp4")
    assert len(jobs[0].gaps) == 1
    assert jobs[0].gaps[0].gap_seconds == pytest.approx(1.0, abs=0.2)
