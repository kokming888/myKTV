# Release Notes

## Version 1.0

MyKTV 1.0 is the first release under the MyKTV name. It continues the YTToMKV project and uses [MyKTV.py](MyKTV.py) as its entry point. Historical YTToMKV scripts and packaging output are preserved in [archived](archived/README.md).

## Credits

MyKTV was started from the code base of YT2MKV, originally developed by David Chang.

- LINE ID: `game76420`
- Facebook: [David Chang's Facebook page](https://www.facebook.com/share/p/1BHjRR3yfB/)

## Highlights

### 1) Full desktop utility interface
The project now behaves like a standalone Windows application with a tab-based interface for:

- YouTube to KTV
- YouTube download
- local video processing
- audio separation
- lyrics recognition
- subtitle merge
- environment repair
- logs and settings
- song library organization and tagging
- KTV song browsing, play queue, and playback

### 2) YouTube and media ingest
- video and audio downloading via yt-dlp
- quality options for MP4 and MP3
- combined MP3 + MP4 output options
- auto 1080p consistent output option

### 3) AI lyric and vocal processing
- local vocal/instrument separation workflow
- Whisper-based transcription
- optional stable-ts alignment
- simplified/traditional Chinese conversion support
- SRT and JSON subtitle outputs

### 4) KTV subtitle generation and styling
- adjustable font, size, colors, border styles
- unplayed/played lyric states
- two-line subtitle mode
- output alignments and subtitle height tuning
- smooth subtitle overlay customization

### 5) Runtime repair automation
- auto-detects missing local component stacks
- repairs or reinstalls common runtime packages
- supports CPU and GPU runtime assumptions

## Release scope

This build is intended as a practical end-user tool for local content creators and karaoke producers. It is not a lightweight library; it is a complete local application pipeline.

## System requirements

- Windows 10 or later
- Python runtime compatible with the app
- FFmpeg runtime available locally
- GPU optional but recommended for faster processing
- Enough disk space for models, downloads, and intermediate audio files

## Workflow summary

1. Install/repair required components
2. Download or add a media file
3. Separate vocals if needed
4. Recognize lyrics
5. Adjust subtitle style
6. Merge with video for final export

## Known limitations

- This project depends on large local AI runtimes and model folders.
- Transcription quality varies by audio quality and language.
- YouTube availability and restrictions can affect download success.
- Large processing jobs may take significant time on CPU-only machines.
- Packaging and installation are not formalized yet as a distributable installer package.

## Upgrade guidance

To use this release with existing MyKTV runtime folders and settings:

1. Back up any output video and generated subtitle files.
2. Back up model folders and local runtime directories if they are custom or manually edited.
3. Launch `MyKTV.py` from the project folder.
4. Run the environment repair tab if the app reports missing components.

## Compatibility and risk notes

Because the project creates its own runtime directories and installs dependencies locally, the app is best used in a dedicated project folder. Moving or deleting runtime folders without updating the app state may cause startup or repair issues.

## Suggested release checklist for future versions

- verify FFmpeg runtime installation
- verify yt-dlp version and JS runtime availability
- verify Whisper model and language support
- test local video processing end-to-end
- test YouTube download with restricted and unrestricted videos
- validate 1080p forced output behavior
- test subtitle merge output for MP4 and MKV

## Summary

Release 1.0 is a substantial milestone: it combines download, audio separation, transcription, styling, and final KTV-style output generation into a single user-focused app. It is especially useful for users creating karaoke content from YouTube or local audio/video sources.
