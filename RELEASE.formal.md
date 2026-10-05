# MyKTV 1.0 Release Package

## Release version

Version: 1.0

## Release summary

MyKTV 1.0 is the first release under the MyKTV name, continuing the YTToMKV codebase. It provides a Windows workflow for YouTube and local media, AI vocal separation, lyric recognition, subtitle alignment, song-library organization, KTV selection, and output merging.

## Product scope

This release targets users who want to:

- download songs or video from YouTube
- create lyric subtitles from local media
- generate KTV-style subtitles with custom appearance
- merge subtitle tracks into MP4 or MKV output
- manage local runtime dependencies automatically

## Included functionality

- YouTube URL conversion and download
- MP3 and MP4 output modes
- quality selection and forced 1080p output
- local audio/video listing and batch handling
- vocal separation workflow
- Whisper transcription and stable-ts timing alignment
- subtitle language conversion support
- KTV subtitle styling controls
- final merge and export pipeline
- environment repair feature for missing components
- searchable song catalog with editable tags
- KTV song selection and playback queue

## System requirements

- Windows 10 or later
- Python-compatible execution environment
- FFmpeg-compatible runtime
- Internet connection for downloads and package installation
- Additional disk space for model downloads and generated media
- Optional GPU for improved processing speed

## Deployment and operation

The application is intended to run directly from the project directory. It creates and uses local service directories to host FFmpeg, AI libraries, and downloaded model files.

Build the onedir executable with `MyKTV.spec`. Previous YTToMKV source snapshots and packaging output are retained under `archive/`.

## Risk and limitation notes

- The app depends on local runtime and model installation.
- YouTube access may be restricted depending on source content.
- AI transcription quality depends on source audio clarity and language selection.
- Large processing tasks may require significant time on CPU-only machines.
- The project does not yet include a formal end-user installer package or distribution bundle.

## Release readiness checklist

- verify app launches correctly
- verify FFmpeg runtime exists
- verify yt-dlp runtime is available
- verify Whisper model stack is present
- validate YouTube download flow
- validate local recognition flow
- validate subtitle merge output
- validate 1080p pad/scale behavior

## Support posture

This release is intended for desktop end-user use in controlled local environments. It is best operated in a dedicated project folder with a stable internet connection and sufficient storage.

## Summary

MyKTV 1.0 is a full-featured local media-to-KTV workflow tool. It is designed for creators who want to generate lyric-based karaoke output with a single Windows desktop application.
