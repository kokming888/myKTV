---
applyTo: "**/*.py"
---

# MyKTV workflow for coding and maintenance

## Goal

Keep the app stable, usable, and easy to maintain as a Windows desktop media-processing tool.

## Workflow

### 1. Understand the current entry point

- Confirm the active source file before editing.
- Treat `MyKTV.py` as the entry point; screen modules are `myktv_song_manager.py` and `myktv_ktv_selection.py`.
- Historical YTToMKV source snapshots and packaging output live under `archive/`.

### 2. Identify the affected subsystem

Before editing, classify the task into one of these areas:

- GUI and UI behavior
- YouTube download and ffmpeg processing
- AI separation / transcription logic
- subtitle generation / merge logic
- environment setup and runtime repair
- packaging and release workflow

### 3. Preserve app runtime assumptions

When changing startup, environment variables, or dependency management:

- maintain the local runtime directory structure,
- preserve PATH and PYTHONPATH setup,
- respect Windows packaging assumptions,
- avoid removing auto-repair support without a clear reason.

### 4. Prefer minimal, reversible change

- Keep fixes local to the relevant method or component.
- Avoid broad rewrites or architecture changes unless the task demands it.
- Prefer repeated small validation steps over large speculative refactors.

### 5. Validate before completion

After making a fix or feature change:

1. Run syntax validation for the edited Python file.
2. Check for runtime issues related to the touched subsystem.
3. If packaging is impacted, rebuild the EXE using the project’s standard PyInstaller pattern.
4. Confirm no generated output folders were accidentally modified without an explicit packaging request.

## Release workflow

When preparing a release:

- review the active source file and docs,
- update release summary and user-facing documentation,
- confirm the packaging command still works,
- verify that the built output is placed under `dist/` and not confused with source files.

## Maintenance notes

- Keep the app self-contained and local-first.
- Minimize hidden network or silent install behavior unless it is explicitly required for user workflow.
- Make any new feature or change understandable from the existing GUI and logging patterns.
