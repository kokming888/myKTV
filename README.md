# MyKTV

**Version 1.0**

A Windows desktop utility for turning YouTube content into karaoke-style KTV videos and subtitle assets. The main entry point is [MyKTV.py](MyKTV.py).

## Overview

This project is a GUI-based workflow for:

- downloading YouTube videos or audio
- extracting audio from local files
- separating vocals from accompaniment
- recognizing lyrics with Whisper
- aligning timestamps with stable-ts
- converting lyrics into KTV subtitles
- merging subtitles back into video output
- repairing missing runtime components automatically

It is designed for users who want to produce karaoke-ready content from songs, music videos, or local media files.

## Credits

MyKTV was started from the code base of YT2MKV, originally developed by David Chang.

- LINE ID: `game76420`
- Facebook: [David Chang's Facebook page](https://www.facebook.com/share/p/1BHjRR3yfB/)

## Features

### YouTube workflow
- Input a YouTube URL
- Download MP3 and/or MP4
- Choose video quality (best, 1080p, 720p, 480p)
- Force 1080p output with black-bar padding for consistent video framing

### Local media workflow
- Add local videos or folders
- Batch process multiple files
- Extract audio from video files
- Separate vocal and instrumental tracks
- Recognize lyrics from video files
- Organize downloaded songs into a searchable local library
- Browse songs by language and tags in the KTV Selection screen

### AI subtitle / lyrics workflow
- Whisper-based transcription
- Auto language detection or fixed language selection
- Stable-ts timing alignment
- Simplified or traditional Chinese conversion options
- SRT and JSON subtitle export
- KTV subtitle styling with custom colors and fonts

### Subtitle merge workflow
- Select video + subtitle files
- Use external audio if needed
- Merge lyrics into final output video
- Export MP4 or MKV output
- Preserve a consistent video size using 1080p scaling

### Environment management
- Detect and repair missing dependencies automatically
- Download and install local FFmpeg, yt-dlp, Python runtime folders, and AI library stacks
- Support CPU and GPU-aware runtime selection for inference tasks

## Main script

- [MyKTV.py](MyKTV.py) — active application entry point
- [myktv_song_manager.py](myktv_song_manager.py) — song catalog, tagging, scanning, and file organization
- [myktv_ktv_selection.py](myktv_ktv_selection.py) — karaoke browsing, play queue, and playback
- [MyKTV.spec](MyKTV.spec) — PyInstaller onedir build configuration
- Historical YTToMKV scripts and build artifacts are preserved under [archived](archived/README.md)

## Supported environment

This project is primarily built for Windows and uses Tkinter for its interface. It relies on local binaries and runtime folders created next to the script.

Expected runtime folders created by the app include:

- engine_ffmpeg
- runtime_python
- yt-dlp
- ai_libraries_common
- ai_libraries_cpu
- ai_libraries_gpu
- ai_libraries_directml
- ai_models
- ai_models_whisper

## Requirements

### Minimum
- Windows 10 or newer
- Python 3.x compatible with the script
- A working FFmpeg installation or bundled FFmpeg runtime
- Internet access for downloading YouTube media and AI packages

### Strongly recommended
- NVIDIA GPU for faster AI inference
- A modern CPU with enough RAM for audio and video processing
- SSD storage for models and temporary files

## Quick start

1. Open the project folder.
2. Run the main file:

```powershell
python .\MyKTV.py
```

3. If the app reports missing components, open the Environment Repair tab and install the recommended dependencies.
4. Use the tabs in order:
   1. YouTube download or local media import
   2. audio separation / lyric recognition
   3. subtitle styling and merge

## Typical workflow

### Option A: YouTube to KTV
1. Copy a YouTube URL.
2. Open the YouTube tab.
3. Choose the desired format and quality.
4. Download the media.
5. Use the local recognition and subtitle-generation steps.
6. Merge the lyrics into the output video.

### Option B: Local file to lyrics and KTV
1. Add one or more local video files.
2. Extract the audio or perform vocal separation.
3. Run Whisper lyric recognition.
4. Review and correct lyrics if needed.
5. Style the subtitle output.
6. Merge the subtitle with the original video.

## Architecture summary

The Tkinter application entry point is `MyKTVApp`. Song management and KTV selection screens are separated into mixins in their own modules. The main app coordinates:

- UI setup
- file selection and drag/drop-like list management
- subprocess execution for FFmpeg, whisper, and packaging tools
- AI runtime detection and repair logic
- YouTube downloads via yt-dlp
- transcription and subtitle processing
- output generation

## Important notes

- The project is heavily automation-focused and expects local runtime folders to be created automatically.
- Audio separation and transcription can be computationally heavy.
- Some parts of the app are intentionally Windows-specific and use Windows-only conventions for setup and launching.
- AI models and libraries may be large; storage requirements vary by GPU/CPU target.

## Troubleshooting

### Missing component or installation errors
Use the Environment Repair tab and select the required components. The app is designed to help install or repair FFmpeg, AI libraries, Whisper models, and yt-dlp.

### YouTube download fails
Check:
- URL validity
- network connectivity
- yt-dlp support for the target content
- whether Deno / JS runtime support is required for restricted content

### Lyrics do not match audio well
Try:
- a stronger Whisper model
- stable-ts alignment
- checking the language configuration
- separating vocals before transcription

## License

This repository currently does not appear to include an explicit license file. If you plan to distribute or publish this tool, add a license before sharing it publicly.

## Recommendation

For end users, the best path is to:

- keep the current generated runtime folders local to the app
- update to the newest script version when available
- back up output and AI library folders before reinstalling dependencies

## Future enhancements

Possible follow-up improvements for this project include:

- a cleaner dependency manifest
- explicit requirements files for Python packages
- better offline packaging and installer generation
- documentation for exact AI model formats and output directories
- automated release packaging for end users

---

This project is best described as a local AI-assisted karaoke and KTV subtitle-making tool with YouTube download and media-processing automation built into a single Windows GUI application.
