"""Qt-free core: discover, probe, plan, and join video clips.

Everything in this module uses only the standard library plus the
``ffmpeg`` / ``ffprobe`` binaries on PATH. It deliberately does not use
ffmpeg-python: the concat demuxer plus stream copy is a short, explicit
subprocess call, and keeping it explicit makes the code testable without
a Qt install and without a wrapper dependency.

Joining rules, in one place:

* Clips are sorted by relative path, so timestamped Wyze filenames come
  out in chronological order instead of filesystem order.
* A previous output (``joined.mp4`` or ``joined-YYYY-MM-DD.mp4``) in the
  source folder is never fed back in as an input.
* An existing output is never overwritten unless ``force`` is set.
* Identical clips (same video codec, size, frame rate, and audio
  layout) are joined with the concat demuxer and stream copy: fast and
  lossless. Anything else is re-encoded to the first clip's size and
  frame rate, so a mixed folder still produces one playable file.
* Progress is real: it is parsed from ffmpeg's ``-progress`` output
  against the summed clip duration. It is not a per-file counter, and
  it is not faked by re-running the join once per clip.
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

__all__ = [
    "CancelledError",
    "ClipInfo",
    "Gap",
    "JoinJob",
    "OutputExistsError",
    "RecordBatch",
    "RecordSkip",
    "WyzeJoinerError",
    "clips_compatible",
    "discover_clips",
    "join_jobs",
    "plan_join",
    "plan_record_join",
    "probe_clip",
]

_DATE_RE = re.compile(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})")
_TIMESTAMP_RE = re.compile(
    r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})[-_T ]?(\d{2})[-_]?(\d{2})(?:[-_]?(\d{2}))?"
)
_PREVIOUS_OUTPUT_RE = re.compile(r"^joined(-\d{4}-\d{2}-\d{2})?\.mp4$")

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


class WyzeJoinerError(Exception):
    """Base error for anything this tool refuses or fails to do."""


class OutputExistsError(WyzeJoinerError):
    """The output file already exists and ``force`` was not set."""


class CancelledError(WyzeJoinerError):
    """The join was cancelled; any partial output has been removed."""


@dataclass(frozen=True)
class ClipInfo:
    """Probe results for one clip."""

    path: Path
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None


@dataclass(frozen=True)
class Gap:
    """Uncovered time between two consecutive clips."""

    previous: Path
    next: Path
    gap_seconds: float


@dataclass
class JoinJob:
    """One output file to produce from an ordered list of clips."""

    clips: list[ClipInfo]
    output_path: Path
    strategy: str  # "copy" or "reencode"
    gaps: list[Gap] = field(default_factory=list)
    force: bool = False

    @property
    def total_duration(self) -> float:
        return sum(clip.duration for clip in self.clips)


def _check_tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise WyzeJoinerError(
            f"{name} was not found on PATH. Install FFmpeg and make sure "
            f"both ffmpeg and ffprobe are on PATH, then try again."
        )
    return found


def discover_clips(
    directory: Path,
    *,
    recursive: bool = False,
    extensions: Sequence[str] = (".mp4",),
    exclude: Sequence[Path] = (),
) -> list[Path]:
    """Return sorted clip files in ``directory``.

    Previous outputs are excluded by name, and any path in ``exclude``
    is excluded by resolved path, so a join can never ingest its own
    output on a later run.
    """
    if not directory.is_dir():
        raise WyzeJoinerError(f"Not a directory: {directory}")
    wanted = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
    excluded = {Path(p).resolve() for p in exclude}
    pattern = "**/*" if recursive else "*"
    clips: list[Path] = []
    for path in directory.glob(pattern):
        if not path.is_file():
            continue
        if path.suffix.lower() not in wanted:
            continue
        if _PREVIOUS_OUTPUT_RE.match(path.name):
            continue
        if path.resolve() in excluded:
            continue
        clips.append(path)
    clips.sort(key=lambda p: p.relative_to(directory).as_posix().lower())
    return clips


def _parse_fps(value: str) -> float:
    if "/" in value:
        numerator, _, denominator = value.partition("/")
        try:
            denom = float(denominator)
            return float(numerator) / denom if denom else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe_clip(path: Path) -> ClipInfo:
    """Probe one clip with ffprobe. Raises WyzeJoinerError on failure."""
    ffprobe = _check_tool("ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,r_frame_rate,channels,sample_rate",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise WyzeJoinerError(f"ffprobe timed out on {path}") from exc
    if result.returncode != 0:
        raise WyzeJoinerError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WyzeJoinerError(f"ffprobe returned unreadable output for {path}") from exc
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise WyzeJoinerError(f"No video stream found in {path}")
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    try:
        duration = float(data.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    sample_rate = audio.get("sample_rate") if audio else None
    return ClipInfo(
        path=path,
        duration=duration,
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=_parse_fps(str(video.get("r_frame_rate", "0"))),
        video_codec=str(video.get("codec_name", "")),
        audio_codec=str(audio["codec_name"]) if audio else None,
        audio_channels=int(audio["channels"]) if audio and audio.get("channels") else None,
        audio_sample_rate=int(sample_rate) if sample_rate else None,
    )


def clips_compatible(clips: Sequence[ClipInfo]) -> bool:
    """True when every clip shares video/audio layout with the first."""
    if not clips:
        return False
    first = clips[0]
    return all(
        clip.video_codec == first.video_codec
        and clip.width == first.width
        and clip.height == first.height
        and abs(clip.fps - first.fps) < 0.01
        and clip.audio_codec == first.audio_codec
        and clip.audio_channels == first.audio_channels
        and clip.audio_sample_rate == first.audio_sample_rate
        for clip in clips[1:]
    )


def _clip_date(path: Path) -> datetime.date | None:
    match = _DATE_RE.search(path.stem)
    if match is None:
        return None
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _clip_start(path: Path) -> datetime.datetime | None:
    match = _TIMESTAMP_RE.search(path.stem)
    if match is None:
        return None
    second = int(match.group(6)) if match.group(6) else 0
    try:
        return datetime.datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            second,
        )
    except ValueError:
        return None


def _find_gaps(clips: Sequence[ClipInfo]) -> list[Gap]:
    """Gaps are uncovered seconds between one clip's end and the next start.

    Only clips whose filenames carry a full timestamp take part. A gap
    under half a second is rounding noise, not a missing minute.
    """
    gaps: list[Gap] = []
    for previous, current in zip(clips, clips[1:]):
        start_prev = _clip_start(previous.path)
        start_next = _clip_start(current.path)
        if start_prev is None or start_next is None:
            continue
        uncovered = (start_next - start_prev).total_seconds() - previous.duration
        if uncovered > 0.5:
            gaps.append(Gap(previous=previous.path, next=current.path, gap_seconds=uncovered))
    return gaps


def plan_join(
    directory: Path,
    *,
    output: Path | None = None,
    recursive: bool = False,
    extensions: Sequence[str] = (".mp4",),
    strategy: str = "auto",
    force: bool = False,
    split_by_day: bool = False,
) -> list[JoinJob]:
    """Discover, probe, and plan joins without running ffmpeg.

    ``strategy`` is ``auto`` (stream copy when compatible, re-encode
    otherwise), ``copy`` (refuse incompatible clips), or ``reencode``.
    With ``split_by_day``, one output per recording date is planned and
    ``output`` (when given) is the output directory; without it, one
    output is planned and ``output`` is the output file.
    """
    if strategy not in ("auto", "copy", "reencode"):
        raise WyzeJoinerError(f"Unknown strategy: {strategy!r} (auto, copy, or reencode)")
    paths = discover_clips(directory, recursive=recursive, extensions=extensions)
    if not paths:
        raise WyzeJoinerError(f"No video clips found in {directory}")
    if split_by_day:
        grouped: dict[datetime.date, list[Path]] = {}
        for path in paths:
            date = _clip_date(path)
            if date is None:
                raise WyzeJoinerError(
                    f"Cannot split by day: {path.name} carries no YYYYMMDD date "
                    "in its name. Run without --split-by-day for such folders."
                )
            grouped.setdefault(date, []).append(path)
        out_dir = output if output is not None else directory
        jobs: list[JoinJob] = []
        for date in sorted(grouped):
            job_output = out_dir / f"joined-{date.isoformat()}.mp4"
            jobs.append(_plan_group(grouped[date], job_output, strategy, force))
        return jobs
    job_output = output if output is not None else directory / "joined.mp4"
    return [_plan_group(paths, job_output, strategy, force)]


@dataclass(frozen=True)
class RecordSkip:
    """One record hour that produced no job, and why."""

    hour_dir: Path
    output_path: Path
    reason: str


@dataclass
class RecordBatch:
    """Planned jobs plus skipped hours from a record folder."""

    jobs: list[JoinJob] = field(default_factory=list)
    skipped: list[RecordSkip] = field(default_factory=list)


_DATE_DIR_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_HOUR_DIR_RE = re.compile(r"^(\d{2})$")


def _record_date(name: str) -> datetime.date | None:
    match = _DATE_DIR_RE.match(name)
    if match is None:
        return None
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _record_hour(name: str) -> int | None:
    match = _HOUR_DIR_RE.match(name)
    if match is None:
        return None
    hour = int(match.group(1))
    return hour if 0 <= hour <= 23 else None


def _numeric_clip_paths(hour_dir: Path) -> list[Path]:
    """Numeric .mp4 clips in an hour folder, in numeric (not string) order.

    The original ss-wyze tool sorted full paths as strings, so ``10.mp4``
    came before ``2.mp4``. Wyze record clips are minute files, so the
    stem's number is the order.
    """
    clips = [
        path
        for path in hour_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp4" and path.stem.isdigit()
    ]
    clips.sort(key=lambda p: (int(p.stem), p.name.lower()))
    return clips


def plan_record_join(
    record_dir: Path,
    *,
    output_dir: Path | None = None,
    strategy: str = "auto",
    force: bool = False,
    max_size_gb: float | None = None,
) -> RecordBatch:
    """Plan one join per hour of a Wyze SD-card ``record`` folder.

    Layout: ``record/<YYYYMMDD>/<HH>/<numeric>.mp4``. Non-date folders,
    non-hour folders, and non-numeric clips are ignored, matching the
    original tool. By default each hour's output is ``<HH>.mp4`` inside
    its date folder, exactly as the original produced; with
    ``output_dir`` given, outputs go there as ``<YYYY-MM-DD>-<HH>.mp4``
    so hours from different dates cannot collide.

    Hours are never aborted by one bad hour: an empty hour, an hour
    over ``max_size_gb``, an hour whose output already exists (without
    ``force``), and an hour that fails planning are all reported in
    ``RecordBatch.skipped`` instead of raising.
    """
    if strategy not in ("auto", "copy", "reencode"):
        raise WyzeJoinerError(f"Unknown strategy: {strategy!r} (auto, copy, or reencode)")
    if not record_dir.is_dir():
        raise WyzeJoinerError(f"Not a directory: {record_dir}")
    batch = RecordBatch()
    dated: list[tuple[datetime.date, Path]] = []
    for child in record_dir.iterdir():
        if not child.is_dir():
            continue
        date = _record_date(child.name)
        if date is not None:
            dated.append((date, child))
    for date, date_dir in sorted(dated):
        hours: list[tuple[int, Path]] = []
        for child in date_dir.iterdir():
            if not child.is_dir():
                continue
            hour = _record_hour(child.name)
            if hour is not None:
                hours.append((hour, child))
        for hour, hour_dir in sorted(hours):
            if output_dir is not None:
                output_path = output_dir / f"{date.isoformat()}-{hour:02d}.mp4"
            else:
                output_path = date_dir / f"{hour:02d}.mp4"
            paths = _numeric_clip_paths(hour_dir)
            if not paths:
                batch.skipped.append(RecordSkip(hour_dir, output_path, "no numeric .mp4 clips"))
                continue
            size_mb = sum(path.stat().st_size for path in paths) / (1024 * 1024)
            if max_size_gb is not None and size_mb > max_size_gb * 1024:
                batch.skipped.append(
                    RecordSkip(
                        hour_dir,
                        output_path,
                        f"size {size_mb:.1f} MB exceeds limit {max_size_gb:g} GB",
                    )
                )
                continue
            if output_path.exists() and not force:
                batch.skipped.append(
                    RecordSkip(hour_dir, output_path, "output already exists (use --force)")
                )
                continue
            try:
                batch.jobs.append(_plan_group(paths, output_path, strategy, force))
            except WyzeJoinerError as exc:
                batch.skipped.append(RecordSkip(hour_dir, output_path, str(exc)))
    return batch


def _plan_group(paths: Sequence[Path], output_path: Path, strategy: str, force: bool) -> JoinJob:
    if output_path.exists() and not force:
        raise OutputExistsError(
            f"Output already exists: {output_path} (use --force to overwrite it)"
        )
    clips = [probe_clip(path) for path in paths if path.resolve() != output_path.resolve()]
    if not clips:
        raise WyzeJoinerError(f"No clips left after excluding the output: {output_path}")
    compatible = clips_compatible(clips)
    if strategy == "copy" and not compatible:
        raise WyzeJoinerError(
            "Clips differ in codec, size, frame rate, or audio layout, so "
            "stream copy cannot join them. Use --strategy auto or reencode."
        )
    chosen = "copy" if (strategy == "copy" or (strategy == "auto" and compatible)) else "reencode"
    return JoinJob(
        clips=clips, output_path=output_path, strategy=chosen, gaps=_find_gaps(clips), force=force
    )


def _concat_file_content(clips: Sequence[ClipInfo]) -> str:
    lines = []
    for clip in clips:
        escaped = str(clip.path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def _parse_progress_file(path: Path, total_duration: float) -> float:
    try:
        text = path.read_text()
    except OSError:
        return 0.0
    out_time_ms = 0
    for line in text.splitlines():
        if line.startswith("out_time_ms="):
            try:
                out_time_ms = int(line.split("=", 1)[1])
            except ValueError:
                pass
    if total_duration <= 0:
        return 0.0
    return min((out_time_ms / 1_000_000) / total_duration, 1.0)


def _run_ffmpeg(
    command: list[str],
    output_path: Path,
    total_duration: float,
    on_progress: ProgressCallback | None,
    should_cancel: CancelCheck | None,
) -> None:
    ffmpeg = _check_tool("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="wyze-join-") as tmp:
        progress_path = Path(tmp) / "progress.txt"
        # -progress must come before the output file: anything after it
        # would be parsed as options for a second, missing output.
        full_command = [
            ffmpeg,
            "-hide_banner",
            "-v",
            "error",
            "-nostdin",
            "-n",
            *command,
            "-progress",
            str(progress_path),
            str(output_path),
        ]
        process = subprocess.Popen(
            full_command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        assert process.stderr is not None
        while process.poll() is None:
            if should_cancel is not None and should_cancel():
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise CancelledError("Join cancelled")
            if on_progress is not None:
                fraction = _parse_progress_file(progress_path, total_duration)
                on_progress(fraction * 100.0, "Joining…")
            time.sleep(0.05)
        stderr = process.stderr.read()
        if process.returncode != 0:
            raise WyzeJoinerError(f"ffmpeg failed: {stderr.strip() or 'unknown error'}")


def join_job(
    job: JoinJob,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> Path:
    """Run one planned job. Removes a partial output on failure/cancel."""
    if job.output_path.exists() and not job.force:
        # plan_join already guards this; re-checking closes the race where
        # the file appeared between planning and running.
        raise OutputExistsError(f"Output already exists: {job.output_path}")
    if job.output_path.exists():
        job.output_path.unlink()
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wyze-join-") as tmp:
        list_path = Path(tmp) / "inputs.txt"
        list_path.write_text(_concat_file_content(job.clips))
        if job.strategy == "copy":
            command = [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-map",
                "0",
                "-c",
                "copy",
            ]
        else:
            first = job.clips[0]
            command = ["-f", "concat", "-safe", "0", "-i", str(list_path), "-map", "0"]
            command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
            if first.fps > 0:
                command += ["-r", f"{first.fps:g}"]
            if first.audio_codec is not None:
                command += ["-c:a", "aac", "-b:a", "128k"]
            else:
                command += ["-an"]
            command += ["-movflags", "+faststart"]
        try:
            _run_ffmpeg(command, job.output_path, job.total_duration, on_progress, should_cancel)
        except BaseException:
            if job.output_path.exists():
                job.output_path.unlink()
            raise
    if not job.output_path.is_file() or job.output_path.stat().st_size == 0:
        raise WyzeJoinerError(f"ffmpeg finished but produced no output: {job.output_path}")
    if on_progress is not None:
        on_progress(100.0, "Done")
    return job.output_path


def join_jobs(
    jobs: Sequence[JoinJob],
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> list[Path]:
    """Run planned jobs in order, reporting overall progress."""
    outputs: list[Path] = []
    total = len(jobs)
    for index, job in enumerate(jobs):

        def _scoped(fraction: float, message: str, _index: int = index) -> None:
            if on_progress is not None:
                overall = ((_index + fraction / 100.0) / total) * 100.0 if total else 100.0
                on_progress(overall, message)

        outputs.append(join_job(job, on_progress=_scoped, should_cancel=should_cancel))
    return outputs
