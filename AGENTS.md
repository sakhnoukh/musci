# AGENTS.md

## Project Overview

`musci` is a Python 3.12 CLI tool for bulk-downloading Spotify, Apple Music, and YouTube playlists/tracks as 44.1 kHz 320kbps CBR MP3s with optional `librosa`-based BPM & Camelot Wheel key detection and ID3v2.3 DJ metadata tagging.

## Environment & Commands

- Virtual environment: `.venv` (Python 3.12 via `/opt/homebrew/bin/python3.12`)
- System dependency: `ffmpeg` / `ffprobe` (installed via Homebrew)
- Install editable with dev dependencies:
  ```bash
  .venv/bin/pip install -e ".[dev]"
  ```
- Run test suite:
  ```bash
  .venv/bin/pytest -v
  ```
- Run CLI:
  ```bash
  .venv/bin/musci download <spotify_or_youtube_playlist_url>
  .venv/bin/musci download <url> --download-only
  .venv/bin/musci analyze <folder_of_mp3s>
  ```
