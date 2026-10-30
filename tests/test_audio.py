"""Audio analysis tests, run against real ffmpeg output."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import make_clip
from wyze_joiner.audio import AudioAnalysisError, analyze_audio, main


def _clip_with_audio(directory: Path, name: str = "clip.mp4") -> Path:
    return make_clip(directory, name, with_audio=True)


def test_audio_analysis_saves_plots_and_audio(clips_dir: Path) -> None:
    clip = _clip_with_audio(clips_dir)
    result = analyze_audio(clip)
    assert result.audio_path is not None and result.audio_path.is_file()
    assert result.audio_path.stat().st_size > 0
    for path in (result.waveform_path, result.spectrogram_path, result.rms_path):
        assert path is not None and path.is_file() and path.stat().st_size > 0
    assert result.audio_path.name == "clip_audio.wav"
    assert result.waveform_path.name == "clip_waveform.png"


def test_audio_wav_input_does_not_crash_on_missing_target(clips_dir: Path) -> None:
    clip = _clip_with_audio(clips_dir)
    extracted = analyze_audio(clip).audio_path
    assert extracted is not None
    # The WAV input's target is a *different* file (clip_audio_audio.wav),
    # which does not exist yet: the original samefile check crashed here.
    result = analyze_audio(extracted)
    assert result.audio_path is not None and result.audio_path.is_file()
    assert result.audio_path.name == "clip_audio_audio.wav"


def test_audio_no_audio_track_raises(clips_dir: Path) -> None:
    clip = make_clip(clips_dir, "silent.mp4")
    with pytest.raises(AudioAnalysisError, match="No audio stream"):
        analyze_audio(clip)


def test_audio_missing_file_raises(clips_dir: Path) -> None:
    with pytest.raises(AudioAnalysisError, match="File not found"):
        analyze_audio(clips_dir / "does-not-exist.mp4")


def test_audio_cli(clips_dir: Path) -> None:
    clip = _clip_with_audio(clips_dir)
    assert main([str(clip)]) == 0
    assert (clips_dir / "clip_audio.wav").exists()
    assert main([str(clips_dir / "does-not-exist.mp4")]) == 1
