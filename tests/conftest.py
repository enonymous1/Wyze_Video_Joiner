"""Synthetic clip helpers. Clips are generated with the real ffmpeg."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def make_clip(
    directory: Path,
    name: str,
    *,
    color: str = "red",
    size: str = "128x72",
    fps: int = 30,
    duration: int = 1,
    with_audio: bool = False,
) -> Path:
    """Generate a short, valid MP4 clip with ffmpeg."""
    path = directory / name
    video = f"color=c={color}:s={size}:r={fps}:d={duration}"
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        video,
    ]
    if with_audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:d={duration}"]
        command += ["-shortest", "-c:a", "aac"]
    else:
        command += ["-an"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True)
    return path


@pytest.fixture
def clips_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "clips"
    directory.mkdir()
    return directory
