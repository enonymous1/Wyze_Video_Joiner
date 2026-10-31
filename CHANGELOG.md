# Changelog

## 1.1.1 — 2026-10-31

Staff review pass (code, docs, and assets; no new features):

- **Fixed:** the CLI no longer silently ignores `--recursive`, `--split-by-day`, or `--extensions` in `--record-dir` mode — they are rejected with an error, and record mode is documented as MP4-only.
- **Fixed:** ffmpeg progress parsing now reads only the tail of the progress file instead of re-reading a growing file on every 50 ms poll.
- **Fixed:** an `assert` on ffmpeg's stderr (stripped under `python -O`) is now an explicit error; figures shown with `wyze-audio --show` are closed after display; the GUI clip list refreshes when "Include subfolders" is toggled.
- **Code quality:** CLI split into record/single runners with one shared join tail; audio plotting imports deduplicated into one helper; bare numbers in core/audio became named constants; missing why-comments added (concat quoting, fps tolerance, 24-bit sign extension, non-overlapping RMS frames).
- **Tests:** 23 → 25 (apostrophe-in-filename join regression test; record-mode flag-rejection test).
- **Docs and assets:** README rewritten (Description, Prerequisites, Installation, Local Development, Deployment); real GUI screenshot, real `wyze-audio` example plots, and an architecture diagram added under `docs/assets/`; this changelog added.

## 1.1.0 — 2026-10-30

- Folded in the two unique pieces of the separate `ss-wyze` tool: record-folder batch mode (`--record-dir`, one join per `record/<YYYYMMDD>/<HH>`) and audio analysis (`wyze-audio`: ffmpeg extraction + numpy/matplotlib waveform, spectrogram, RMS).
- Fixes shipped with the fold-in: numeric clip sorting, missing-dependency install, WAV-input crash, moviepy/librosa replaced.
- Post-release fixes (same version, before push): mypy CI gate no longer opens unparseable PyQt6 stubs (skip `PIL.ImageQt`); tests became a real package so bare `pytest` can import them.

## 1.0.0 — 2026-10-26

- Modernization of the 2023 PyQt5 script into an installable package: Qt-free core, `wyze-join` CLI, PyQt6 GUI, stream-copy joining, sorted discovery, output exclusion and overwrite protection, gap warnings, split-by-day output.
