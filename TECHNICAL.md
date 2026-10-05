# Technical Documentation

## Project purpose

This project is a Windows desktop application for media processing and karaoke subtitle creation. [MyKTV.py](MyKTV.py) is the entry point; `MyKTVApp` composes the core application with screen mixins from [myktv_song_manager.py](myktv_song_manager.py) and [myktv_ktv_selection.py](myktv_ktv_selection.py).

## Entry point

The script starts with a traditional Tkinter startup sequence:

```python
if __name__ == "__main__":
    root = tk.Tk()
    app = MyKTVApp(root)
    root.mainloop()
```

This creates the main GUI and initializes the application state.

## Core class structure

The application class is composed from the entry point and two screen mixins:

- `MyKTVApp` in `MyKTV.py`
- `SongManagerViewMixin` in `myktv_song_manager.py`
- `KtvSelectionMixin` in `myktv_ktv_selection.py`

Together, they provide:

- app initialization
- creating local runtime directories
- loading and saving configuration
- building the GUI
- launching subprocesses
- managing ffmpeg, yt-dlp, Whisper, and AI runtime workflows
- performing output generation and subtitle merging

## Initialization workflow

During `__init__`, the application does the following:

1. reads the shared app version as `v1.0`
2. sets the window title and size
3. chooses the application directory based on frozen/executable vs. source execution
4. creates local runtime folders
5. updates PATH and PYTHONPATH
6. loads config from `config.json`
7. calls `setup_ui_style()` and `setup_ui()`
8. triggers startup checks in the background

This makes the app self-contained and ensures local runtime assets are available without external installation steps.

## Runtime folder model

The script creates and manages multiple local directories:

| Directory | Purpose |
| --- | --- |
| engine_ffmpeg | FFmpeg binaries |
| runtime_python | bundled Python runtime |
| yt-dlp | YouTube download tool and related runtime support |
| ai_libraries_common | shared AI dependencies |
| ai_libraries_cpu | CPU inference libraries |
| ai_libraries_gpu | GPU inference libraries |
| ai_libraries_directml | AMD/Intel GPU runtime support |
| ai_models | primary model assets |
| ai_models_whisper | Whisper model files |

The app intentionally keeps model and runtime files next to the application, which helps with offline or portable execution.

## UI design

The GUI uses Tkinter with themed styling and a tabbed interface. Tabs include the following categories:

- YouTube one-click KTV
- YouTube download
- local video processing
- local audio batch separation
- local video lyric recognition
- merge subtitles and video
- repair environment
- logs
- Whisper settings
- contact / author section
- Song Library organization
- KTV song selection and queue playback

The interface is built using frames, labels, listboxes, buttons, comboboxes, and checkboxes. The app uses a custom style layer to keep the UI modern and easier to use.

## Data and configuration flow

The script maintains a `self.config` dictionary and writes it to `config.json` in the app directory.

Important config-driven items include:

- output format preferences
- lyric language preferences
- Whisper model selection
- stable-ts usage
- subtitles styling values
- video quality settings
- local runtime assumptions

The app reads configuration on startup and updates it when a user changes settings.

The song library stores its searchable catalog in `.myktv_catalog.sqlite3` inside the selected library folder. On first use, an existing `.yttomkv_catalog.sqlite3` is renamed in place when possible.

## External dependencies and tool usage

### FFmpeg
Used for:

- audio extraction
- format conversion
- joining or splitting media streams
- black-bar padding for 1080p output
- file conversion steps in the merge pipeline

The script references a local FFmpeg binary at:

- self.bin_dir / "ffmpeg.exe"

### yt-dlp
Used for:

- YouTube downloading
- media quality selection
- format conversion
- optional JS runtime support for restricted content

### Whisper
Used for:

- speech-to-text transcription
- timestamp alignment
- subtitle generation for local media

### stable-ts
Used for more accurate subtitle alignment and timing correction.

### AI separation libraries
Likely used to separate vocals from instrumental audio or provide inference backends for source separation.

## Processing pipeline

### 1) Download stage
The app can accept a URL and call a download routine that chooses MP3 and/or MP4 output.

### 2) Local media stage
Users can add one or more files or folders and run batch operations. The script then uses FFmpeg and AI separation steps to create new output files.

### 3) Recognition stage
The recognition path uses audio from the source file, selects Whisper settings, optionally separates vocals first, then writes:

- SRT subtitles
- JSON lyrics data

### 4) Subtitle styling stage
The app exposes many styling controls for KTV subtitles, including:

- color sets
- borders
- font family
- font size
- two-line mode
- subtitle placement offset
- advanced visual tuning

### 5) Merge stage
The final stage overlays subtitle data onto video and exports an MP4 or MKV output.

The app also supports a 1080p forced composition mode using filter logic such as:

```python
scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1
```

This ensures the output respects a fixed 1920x1080 frame while preserving its aspect ratio.

## Execution model

The script uses a mix of:

- Tkinter UI callbacks
- background threading for long-running work
- subprocess execution for external tools
- event-driven status updates
- cancellation logic via thread events

This pattern is important because media conversion and AI processing can block the UI if run in the main thread.

## Error handling and logging

The program includes logging utilities and exception wrappers such as:

- `log()`
- `_safe_log()`
- `_log_exception()`
- background update routines

It keeps a human-readable output panel for commands, warnings, and progress. This helps users understand whether FFmpeg, yt-dlp, or model processing failed.

## Safety and operational notes

- The app creates files in the local working directory and subfolders.
- Temporary files may be stored during processing and conversion.
- GPU and CPU runtime stacks are treated as local toolchains and may require large space.
- Because it downloads and installs support packages, strong internet access and a clean project folder are recommended.

## Scalability concerns

This application is not designed as a lightweight API or service. It is designed as a desktop productivity tool with a single-user interactive UI. For large-scale automation, this code would likely need to be refactored into a service-oriented or batch-oriented structure.

## Summary

This project is a practical, local AI-based karaoke and subtitle-generation platform packaged as a Tkinter desktop application. It uses background task execution, FFmpeg/yt-dlp integration, Whisper transcription, a local SQLite song catalog, and separate mixins for song organization and KTV selection.

---

For implementation details, the best starting points are:

- [MyKTV.py](MyKTV.py)
- [myktv_song_manager.py](myktv_song_manager.py)
- [myktv_ktv_selection.py](myktv_ktv_selection.py)
- the `MyKTVApp.__init__` startup flow
- the `setup_ui()` layout creation
- the environment repair logic in `check_components()`
- the recognition and merge routines used after the UI actions are triggered
