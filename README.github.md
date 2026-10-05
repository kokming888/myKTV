# MyKTV

**Version 1.0**

A Windows desktop app for turning YouTube videos and local media into KTV-style karaoke videos with synced lyrics.

## What it does

MyKTV is a local media-processing workflow that combines:

- YouTube downloading
- audio extraction and video conversion
- vocal/instrument separation
- lyric recognition with Whisper
- timestamp alignment with stable-ts
- subtitle styling for karaoke output
- final video + subtitle merge

## Highlights

- Download MP3 and MP4 from YouTube
- Process local video/audio files in batches
- Separate vocals from instrumentals
- Generate SRT/JSON subtitle output
- Style karaoke subtitles with custom fonts and colors
- Merge subtitles into final MP4/MKV output
- Repair missing runtime components from the app itself
- Organize downloads in a searchable song library
- Browse by language and tags, build a queue, and play songs from the KTV Selection tab

## Project status

This repo contains a Windows GUI application built around the main script:

- [MyKTV.py](MyKTV.py)
- [myktv_song_manager.py](myktv_song_manager.py)
- [myktv_ktv_selection.py](myktv_ktv_selection.py)

It is a practical local tool for creating karaoke-ready output from music videos and songs.

## Key workflow

1. Download or load a video/audio source
2. Separate vocals if needed
3. Recognize lyrics with Whisper
4. Align timing and convert language if needed
5. Style the subtitle output
6. Merge the subtitle into the final video

## Requirements

- Windows 10+
- Python-compatible runtime
- FFmpeg support
- Internet access for YouTube downloads and model installation
- Optional GPU for faster AI processing

## Quick start

```powershell
python .\MyKTV.py
```

If the app detects missing components, use the Environment Repair tab before continuing.

## Notes

- This is a desktop application, not a library.
- Runtime folders are created locally next to the app.
- Large AI model files and media processing can require substantial disk space and time.

## License

No explicit license file was found in the repo. If this project is being shared publicly, consider adding a license before distribution.

## Repository structure

- [MyKTV.py](MyKTV.py) — main application
- [README.md](README.md) — general usage overview
- [RELEASE.md](RELEASE.md) — release notes
- [TECHNICAL.md](TECHNICAL.md) — architecture and implementation notes
- [archive/README.md](archive/README.md) — archived YTToMKV scripts and previous build artifacts

## Summary

MyKTV is a local AI-assisted karaoke and subtitle-creation tool designed for Windows users who want to turn music videos and YouTube tracks into KTV-style output.
