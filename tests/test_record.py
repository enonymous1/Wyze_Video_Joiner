"""Record-folder batch tests: one join per record/<date>/<hour>."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_clip
from wyze_joiner.cli import main
from wyze_joiner.core import join_jobs, plan_record_join, probe_clip


def _record(tmp_path: Path) -> Path:
    record = tmp_path / "record"
    hour = record / "20240101" / "07"
    hour.mkdir(parents=True)
    return record


def test_record_numeric_order_not_string_order(tmp_path: Path) -> None:
    record = _record(tmp_path)
    hour = record / "20240101" / "07"
    # String sort would put 10.mp4 before 2.mp4; numeric order must not.
    make_clip(hour, "10.mp4", color="blue")
    make_clip(hour, "2.mp4", color="red")
    batch = plan_record_join(record)
    assert [clip.path.name for clip in batch.jobs[0].clips] == ["2.mp4", "10.mp4"]
    assert batch.jobs[0].output_path == record / "20240101" / "07.mp4"
    assert not batch.skipped
    outputs = join_jobs(batch.jobs)
    assert 1.8 <= probe_clip(outputs[0]).duration <= 2.2


def test_record_ignores_non_date_and_non_hour_and_skips_empty(tmp_path: Path) -> None:
    record = _record(tmp_path)
    hour = record / "20240101" / "07"
    make_clip(hour, "1.mp4")
    (record / "notes").mkdir()
    (record / "20240101" / "99").mkdir()  # not a valid hour
    (record / "20240101" / "08").mkdir()  # valid hour, no clips
    batch = plan_record_join(record)
    assert len(batch.jobs) == 1
    assert len(batch.skipped) == 1
    assert batch.skipped[0].reason == "no numeric .mp4 clips"


def test_record_existing_output_skipped_and_force_rebuilds(tmp_path: Path) -> None:
    record = _record(tmp_path)
    hour = record / "20240101" / "07"
    make_clip(hour, "1.mp4")
    output = record / "20240101" / "07.mp4"
    join_jobs(plan_record_join(record).jobs)
    assert output.exists()
    batch = plan_record_join(record)
    assert not batch.jobs
    assert batch.skipped[0].reason == "output already exists (use --force)"
    forced = plan_record_join(record, force=True)
    assert len(forced.jobs) == 1
    assert join_jobs(forced.jobs) == [output]


def test_record_max_size_skips_hour(tmp_path: Path) -> None:
    record = _record(tmp_path)
    hour = record / "20240101" / "07"
    make_clip(hour, "1.mp4")
    batch = plan_record_join(record, max_size_gb=0.0000001)
    assert not batch.jobs
    assert "exceeds limit" in batch.skipped[0].reason


def test_record_output_dir_avoids_date_collision(tmp_path: Path) -> None:
    record = tmp_path / "record"
    for date in ("20240101", "20240102"):
        hour = record / date / "07"
        hour.mkdir(parents=True)
        make_clip(hour, "1.mp4")
    out_dir = tmp_path / "out"
    batch = plan_record_join(record, output_dir=out_dir)
    assert [job.output_path.name for job in batch.jobs] == [
        "2024-01-01-07.mp4",
        "2024-01-02-07.mp4",
    ]
    assert join_jobs(batch.jobs) == [
        out_dir / "2024-01-01-07.mp4",
        out_dir / "2024-01-02-07.mp4",
    ]


def test_record_cli_dry_run_writes_nothing(tmp_path: Path) -> None:
    record = _record(tmp_path)
    hour = record / "20240101" / "07"
    make_clip(hour, "1.mp4")
    assert main(["--record-dir", str(record), "--dry-run"]) == 0
    assert not (record / "20240101" / "07.mp4").exists()
    assert main(["--record-dir", str(record), str(tmp_path)]) == 2
