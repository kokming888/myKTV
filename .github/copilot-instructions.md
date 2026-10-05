# MyKTV Copilot Instructions

## Project context

This repository contains a Windows desktop utility for YouTube download, local media conversion, AI vocal separation, lyric recognition, KTV subtitle generation, and final media merge.

The active entry point is `MyKTV.py` (version 1.0). Song organization lives in `myktv_song_manager.py`; KTV selection and playback live in `myktv_ktv_selection.py`. Historical YTToMKV scripts and previous packaging output are preserved under `archive/` and are not active source.

## Working rules

- Prefer to edit `MyKTV.py` or the relevant MyKTV module. Only inspect archived snapshots when explicitly asked.
- Keep the runtime folder layout intact: `engine_ffmpeg`, `runtime_python`, `yt-dlp`, `ai_libraries_common`, `ai_libraries_cpu`, `ai_libraries_gpu`, `ai_libraries_directml`, `ai_models`, and `ai_models_whisper`.
- Do not modify generated packaging artifacts in `dist/` or `build/` unless the user specifically asks for a packaging step. `MyKTV.spec` is the active packaging configuration.
- Preserve the app’s GUI threading model. Long-running downloads, transcription, and media conversion jobs should continue to run on background threads rather than blocking the UI.
- When changing PATH or PYTHONPATH behavior, validate that local runtime directories are still discovered correctly.
- Treat external download tools as a security boundary: do not silently add remote execution or unverified downloads without clear user intent.
- Keep changes minimal and targeted. This project is a local utility, not a broad framework.

## Validation guidance

- Use a lightweight verification step after code changes, such as Python syntax validation:
  - `python -m py_compile MyKTV.py myktv_song_manager.py myktv_ktv_selection.py`
- If packaging is needed, prefer the existing PyInstaller pattern:
  - `pyinstaller MyKTV.spec`
- If editing YouTube/download logic, validate that the app still handles ffmpeg, yt-dlp, and local temp file cleanup correctly.
- If editing AI-related code, verify the runtime directories and environment variable setup remain compatible with the app’s startup repair logic.

## Documentation and release expectations

- Keep release and user documentation aligned with the current version.
- When creating or updating docs, distinguish between:
  - end-user usage,
  - implementation/technical details,
  - release notes,
  - and internal maintenance notes.

## Preferred default behavior

- Favor stable, existing patterns over new abstractions.
- Preserve compatibility with Windows-only GUI behavior and self-contained runtime packaging.
- Keep logs readable and user-friendly in the Tkinter UI.
- If there is uncertainty, prefer a conservative fix that preserves the app’s existing runtime assumptions.
