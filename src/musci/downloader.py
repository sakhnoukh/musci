"""Concurrent 320kbps CBR MP3 downloader with manifest-based deduplication."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from musci.models import TrackOutcome, TrackSpec
from musci.sources.ytdlp_source import resolve_search_url

MANIFEST_FILENAME = ".musci-manifest.json"
INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE_RE = re.compile(r"\s+")
PREFIXED_BPM_KEY_RE = re.compile(r"^\[\d{2,3}\s*-\s*[0-9A-Za-z#b]+\]\s*")


def sanitize_filename(name: str, max_len: int = 180) -> str:
    """Produce a safe cross-platform filename stem without path separators."""
    cleaned = INVALID_FILENAME_CHARS_RE.sub(" ", name)
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_len].rstrip(" .")


class ManifestStore:
    """Thread-safe JSON manifest mapping track dedup keys to downloaded filenames."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / MANIFEST_FILENAME
        self._lock = threading.Lock()
        self._entries: dict[str, str] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._entries = {str(k): str(v) for k, v in data.items()}
            except (OSError, json.JSONDecodeError):
                self._entries = {}

    def get_existing_path(self, directory: Path, key: str) -> Path | None:
        with self._lock:
            rel_name = self._entries.get(key)
        if not rel_name:
            return None
        candidate = directory / rel_name
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
        return None

    def record(self, key: str, filename: str) -> None:
        with self._lock:
            self._entries[key] = filename
            tmp_path = self.path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(self._entries, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp_path, self.path)


def _find_matching_file_on_disk(directory: Path, stem: str) -> Path | None:
    exact = directory / f"{stem}.mp3"
    if exact.exists() and exact.stat().st_size > 0:
        return exact
    for mp3_file in directory.glob("*.mp3"):
        stripped = PREFIXED_BPM_KEY_RE.sub("", mp3_file.stem)
        if stripped.lower() == stem.lower() and mp3_file.stat().st_size > 0:
            return mp3_file
    return None


def _unique_destination(directory: Path, base_stem: str) -> Path:
    candidate = directory / f"{base_stem}.mp3"
    if not candidate.exists():
        return candidate
    idx = 2
    while True:
        alt = directory / f"{base_stem} ({idx}).mp3"
        if not alt.exists():
            return alt
        idx += 1


def download_single_track(
    track: TrackSpec,
    output_dir: Path,
    manifest: ManifestStore,
    dest_lock: threading.Lock,
) -> TrackOutcome:
    """Download one TrackSpec as a 320kbps CBR MP3 into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_filename(track.display_name)

    existing = manifest.get_existing_path(output_dir, track.dedup_key)
    if existing is None:
        existing = _find_matching_file_on_disk(output_dir, stem)
        if existing is not None:
            manifest.record(track.dedup_key, existing.name)

    if existing is not None:
        return TrackOutcome(track=track, status="skipped", file_path=existing)

    tmp_token = f".musci_tmp_{uuid.uuid4().hex[:12]}"
    tmp_template = str(output_dir / f"{tmp_token}.%(ext)s")
    expected_tmp_mp3 = output_dir / f"{tmp_token}.mp3"

    ffmpeg_path = shutil.which("ffmpeg")
    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": tmp_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
        "postprocessor_args": {
            "extractaudio": ["-c:a", "libmp3lame", "-ar", "44100", "-b:a", "320k"],
        },
    }
    if ffmpeg_path:
        ydl_opts["ffmpeg_location"] = str(Path(ffmpeg_path).parent)

    try:
        target_url = track.direct_url or resolve_search_url(track)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([target_url])

        if not expected_tmp_mp3.exists() or expected_tmp_mp3.stat().st_size == 0:
            raise RuntimeError("Audio extraction finished without producing an MP3 file.")

        with dest_lock:
            final_path = _unique_destination(output_dir, stem)
            os.replace(expected_tmp_mp3, final_path)

        manifest.record(track.dedup_key, final_path.name)
        return TrackOutcome(track=track, status="downloaded", file_path=final_path)
    except Exception as exc:
        for leftover in output_dir.glob(f"{tmp_token}*"):
            try:
                leftover.unlink()
            except OSError:
                pass
        return TrackOutcome(track=track, status="failed", error=str(exc))


def download_batch(
    tracks: list[TrackSpec],
    output_dir: Path,
    workers: int = 4,
    on_complete: Callable[[TrackOutcome], None] | None = None,
) -> list[TrackOutcome]:
    """Download a batch of tracks concurrently and return outcomes in original order."""
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = ManifestStore(output_dir)
    dest_lock = threading.Lock()

    # Deduplicate identical tracks within the same batch before submitting
    seen_keys: set[str] = set()
    unique_tracks: list[TrackSpec] = []
    for t in tracks:
        if t.dedup_key not in seen_keys:
            seen_keys.add(t.dedup_key)
            unique_tracks.append(t)

    outcomes_by_idx: dict[int, TrackOutcome] = {}
    max_workers = max(1, min(workers, len(unique_tracks) or 1))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {
            pool.submit(download_single_track, track, output_dir, manifest, dest_lock): idx
            for idx, track in enumerate(unique_tracks)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            outcome = future.result()
            outcomes_by_idx[idx] = outcome
            if on_complete is not None:
                on_complete(outcome)

    return [outcomes_by_idx[i] for i in range(len(unique_tracks))]
