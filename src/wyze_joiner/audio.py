"""Audio analysis for a joined clip or any video/audio file.

This is the folded-in ``ss-wyze`` audio tool, rewritten around the
binaries this package already requires instead of moviepy and librosa:

* ffmpeg extracts the audio (no heavy media library in the base install),
* the standard library reads the extracted PCM WAV,
* numpy + matplotlib draw the waveform, spectrogram, and RMS plots.

The analysis dependencies are therefore an optional extra
(``pip install wyze-video-joiner[audio]``); the joiner itself stays
dependency-free. Audio bugs fixed from the original tool:

* a file with no audio track now raises a clear error instead of
  crashing inside the media library,
* a WAV input no longer crashes on the ``samefile`` check when the
  ``*_audio.wav`` target does not exist yet,
* plots are made from the file's native sample rate, not a resampled
  22,050 Hz copy, and the waveform is downsampled for plotting instead
  of drawing every sample of a long file.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "AudioAnalysis",
    "AudioAnalysisError",
    "analyze_audio",
    "main",
]


class AudioAnalysisError(Exception):
    """Anything audio analysis refuses or fails to do."""


@dataclass(frozen=True)
class AudioAnalysis:
    """Paths produced by :func:`analyze_audio` (``None`` when not saved)."""

    source: Path
    audio_path: Path | None
    waveform_path: Path | None
    spectrogram_path: Path | None
    rms_path: Path | None


def _find_tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise AudioAnalysisError(
            f"{name} was not found on PATH. Install FFmpeg and make sure "
            f"both ffmpeg and ffprobe are on PATH, then try again."
        )
    return found


def _has_audio(path: Path) -> bool:
    ffprobe = _find_tool("ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AudioAnalysisError(f"ffprobe timed out on {path}") from exc
    if result.returncode != 0:
        raise AudioAnalysisError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AudioAnalysisError(f"ffprobe returned unreadable output for {path}") from exc
    return any(stream.get("codec_type") == "audio" for stream in data.get("streams", []))


def _extract_audio(source: Path, dest: Path, *, mono: bool) -> None:
    ffmpeg = _find_tool("ffmpeg")
    command = [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(source),
        "-vn",
    ]
    if mono:
        command += ["-ac", "1"]
    command += ["-c:a", "pcm_s16le", str(dest)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        raise AudioAnalysisError(
            f"ffmpeg failed to extract audio from {source}: {result.stderr.strip()}"
        )


def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """Read a PCM WAV as mono float samples in [-1, 1] plus its rate.

    Returns ``(samples, sample_rate)``; the samples object is a numpy
    array, imported lazily so the joiner stays importable without the
    audio extra installed.
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise AudioAnalysisError(
            "Audio analysis needs the audio extra: pip install wyze-video-joiner[audio]"
        ) from exc
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sampwidth = wav.getsampwidth()
        sample_rate = wav.getframerate()
        raw = wav.readframes(wav.getnframes())
    if sampwidth == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif sampwidth == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif sampwidth == 3:
        triplets = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        values = (
            triplets[:, 0].astype(np.int32)
            | (triplets[:, 1].astype(np.int32) << 8)
            | (triplets[:, 2].astype(np.int32) << 16)
        )
        values = np.where(values >= (1 << 23), values - (1 << 24), values)
        samples = values.astype(np.float64) / float(1 << 23)
    elif sampwidth == 4:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float64) / float(1 << 31)
    else:
        raise AudioAnalysisError(f"Unsupported WAV sample width: {sampwidth} bytes")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


def _plot_waveform(
    samples: np.ndarray, sample_rate: int, output_path: Path, *, close: bool = True
) -> None:
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as exc:
        raise AudioAnalysisError(
            "Audio analysis needs the audio extra: pip install wyze-video-joiner[audio]"
        ) from exc
    # Plotting every sample of a long file is slow and unreadable; 200k
    # points still shows the shape faithfully.
    stride = max(1, len(samples) // 200_000)
    plotted = samples[::stride]
    times = np.arange(len(plotted)) * stride / sample_rate
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(times, plotted)
    ax.set_title("Audio Waveform")
    ax.set_xlabel("Time (s)")
    fig.tight_layout()
    fig.savefig(output_path)
    if close:
        plt.close(fig)


def _plot_spectrogram(
    samples: np.ndarray, sample_rate: int, output_path: Path, *, close: bool = True
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise AudioAnalysisError(
            "Audio analysis needs the audio extra: pip install wyze-video-joiner[audio]"
        ) from exc
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.specgram(samples, NFFT=2048, Fs=sample_rate, noverlap=1024, scale="dB")
    ax.set_title("Spectrogram")
    ax.set_xlabel("Time (s)")
    fig.tight_layout()
    fig.savefig(output_path)
    if close:
        plt.close(fig)


def _plot_rms(
    samples: np.ndarray, sample_rate: int, output_path: Path, *, close: bool = True
) -> None:
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as exc:
        raise AudioAnalysisError(
            "Audio analysis needs the audio extra: pip install wyze-video-joiner[audio]"
        ) from exc
    frame = 2048
    count = len(samples) // frame
    if count == 0:
        raise AudioAnalysisError("Audio is too short to compute RMS energy")
    framed = samples[: count * frame].reshape(count, frame)
    rms = np.sqrt(np.mean(framed.astype(np.float64) ** 2, axis=1))
    times = np.arange(count) * frame / sample_rate
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(times, rms)
    ax.set_title("RMS Energy")
    ax.set_xlabel("Time (s)")
    fig.tight_layout()
    fig.savefig(output_path)
    if close:
        plt.close(fig)


def analyze_audio(
    file_path: Path,
    *,
    output_dir: Path | None = None,
    save_plots: bool = True,
    save_audio: bool = True,
    show_plots: bool = False,
) -> AudioAnalysis:
    """Analyze ``file_path`` and save plots (and extracted audio).

    Plots and audio land in ``output_dir`` (default: the input file's
    folder) as ``<stem>_waveform.png``, ``<stem>_spectrogram.png``,
    ``<stem>_rms.png``, and ``<stem>_audio.wav``. With ``show_plots``,
    matplotlib also displays the figures (a no-op under the headless
    Agg backend). Raises :class:`AudioAnalysisError` for a missing file,
    a file with no audio track, or a failed extraction.
    """
    if not file_path.is_file():
        raise AudioAnalysisError(f"File not found: {file_path}")
    out_dir = output_dir if output_dir is not None else file_path.parent
    if save_plots or save_audio:
        out_dir.mkdir(parents=True, exist_ok=True)
    if not _has_audio(file_path):
        raise AudioAnalysisError(f"No audio stream found in {file_path}")
    basename = file_path.stem
    audio_path: Path | None = None
    if save_audio:
        audio_path = out_dir / f"{basename}_audio.wav"
        if file_path.suffix.lower() == ".wav" and (
            not audio_path.exists() or audio_path.resolve() != file_path.resolve()
        ):
            shutil.copy2(file_path, audio_path)
        elif file_path.suffix.lower() != ".wav":
            _extract_audio(file_path, audio_path, mono=False)
        # Else the input already is the target file; nothing to copy.
    waveform_path: Path | None = None
    spectrogram_path: Path | None = None
    rms_path: Path | None = None
    if save_plots or show_plots:
        with tempfile.TemporaryDirectory(prefix="wyze-audio-") as tmp:
            analysis_wav = Path(tmp) / "analysis.wav"
            _extract_audio(file_path, analysis_wav, mono=True)
            samples, sample_rate = _read_wav_mono(analysis_wav)
            # Figures are drawn once. When only displaying, they are
            # saved to a scratch file and kept open for plt.show().
            plot_dir = out_dir if save_plots else Path(tmp)
            waveform_target = plot_dir / f"{basename}_waveform.png"
            spectrogram_target = plot_dir / f"{basename}_spectrogram.png"
            rms_target = plot_dir / f"{basename}_rms.png"
            _plot_waveform(samples, sample_rate, waveform_target, close=not show_plots)
            _plot_spectrogram(samples, sample_rate, spectrogram_target, close=not show_plots)
            _plot_rms(samples, sample_rate, rms_target, close=not show_plots)
            if save_plots:
                waveform_path = waveform_target
                spectrogram_path = spectrogram_target
                rms_path = rms_target
            if show_plots:
                try:
                    import matplotlib.pyplot as plt
                except ImportError as exc:
                    raise AudioAnalysisError(
                        "Audio analysis needs the audio extra: pip install wyze-video-joiner[audio]"
                    ) from exc
                plt.show()
    return AudioAnalysis(
        source=file_path,
        audio_path=audio_path,
        waveform_path=waveform_path,
        spectrogram_path=spectrogram_path,
        rms_path=rms_path,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wyze-audio",
        description="Analyze audio from a video or audio file.",
    )
    parser.add_argument("file_path", type=Path, help="Path to the MP4 or WAV file to analyze")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to save plots/audio (default: same as the input file)",
    )
    parser.add_argument("--show", action="store_true", help="Display plots (default: False)")
    parser.add_argument(
        "--no-save", action="store_true", help="Do not save plots to files (default: False)"
    )
    parser.add_argument(
        "--no-audio", action="store_true", help="Do not save extracted audio (default: False)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = analyze_audio(
            args.file_path,
            output_dir=args.output_dir,
            save_plots=not args.no_save,
            save_audio=not args.no_audio,
            show_plots=args.show,
        )
    except AudioAnalysisError as exc:
        print(f"wyze-audio: {exc}", file=sys.stderr)
        return 1
    for label, path in (
        ("audio", result.audio_path),
        ("waveform plot", result.waveform_path),
        ("spectrogram plot", result.spectrogram_path),
        ("RMS plot", result.rms_path),
    ):
        if path is not None:
            print(f"Saved {label} to: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
