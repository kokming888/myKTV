# Internal Developer Documentation

## Purpose

This repository contains a Windows desktop utility for YouTube download, local media processing, vocal separation, subtitle generation, and KTV-style video output. The primary implementation is in [MyKTV.py](MyKTV.py).

## Project architecture

The application entry point is `MyKTVApp`. Song organization and KTV selection are separated into `SongManagerViewMixin` and `KtvSelectionMixin` modules. The app manages:

- Tkinter UI construction
- configuration persistence
- local runtime directory setup
- subprocess execution for FFmpeg, yt-dlp, and model tools
- AI dependency detection and repair
- media processing and subtitle generation
- final merge workflow
- shared local song catalog and metadata

## Startup sequence

The script initializes on startup by:

1. creating local folders for runtime and models
2. adjusting PATH and PYTHONPATH
3. loading configuration from `config.json`
4. building the main tabbed GUI
5. checking components in the background

## Runtime layout

The app creates runtime directories such as:

- engine_ffmpeg
- runtime_python
- yt-dlp
- ai_libraries_common
- ai_libraries_cpu
- ai_libraries_gpu
- ai_libraries_directml
- ai_models
- ai_models_whisper

These folders are essential to the app’s functionality and should be treated as managed artifacts, not arbitrary user-created directories.

## Functional modules

### UI layer
The Tkinter UI includes tabs for:

- YouTube play/convert workflow
- YouTube download workflow
- local video processing
- local audio separation
- lyric recognition
- merge and subtitle generation
- runtime repair
- logs and settings
- song library organization and tagging
- KTV song selection and queue playback

### Download layer
The app integrates with yt-dlp to fetch video/audio content and select quality.

### Media processing layer
FFmpeg is the main media conversion engine for:

- audio extraction
- muxing and remuxing
- scaling and padding for 1080p output
- final merge output

### AI layer
The app supports transcription and AI-based processing through local Python packages and download-managed libraries. GPU-aware execution is checked internally.

### Subtitle / KTV layer
This layer styles and formats subtitles for karaoke use. It includes color selection, border options, font selection, spacing, line mode, and subtitle height tuning.

## Important implementation concerns

- This is primarily a single-user GUI workflow.
- Long-running operations should run off the UI thread.
- External tool calls must be handled carefully and logged.
- Runtime folders should be backed up before reinstalling or replacing the environment.

## Development guidance

### Local validation workflow
- run the app from the repo root
- verify missing dependency prompts
- test YouTube download path
- test local media recognition path
- verify merge output for MP4/MKV

### Common failure areas
- broken or incompatible local runtime directories
- missing FFmpeg binary
- missing yt-dlp stack
- incomplete Whisper model or AI library installation
- YouTube source availability restrictions

## Notes for maintainers

The project is in a state best described as a production-use desktop utility with rapidly evolving internal logic. Updating one part of the app may affect the UI, execution flow, or local environment assumptions, so changes should be tested end-to-end.

## Summary

This project is a local workflow application for creating KTV-ready content from online or local music sources. It depends on an integrated runtime stack and should be maintained with a clear understanding of the local dependency model.
