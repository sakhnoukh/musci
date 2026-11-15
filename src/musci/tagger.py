"""ID3v2.3 metadata & DJ tagger (TBPM, TKEY, artwork) and filename prefixer."""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TBPM, TKEY, TIT2, TPE1

from musci.models import AnalysisResult, KeyFormat, TrackSpec

MAX_ARTWORK_BYTES = 5 * 1024 * 1024
EXISTING_PREFIX_RE = re.compile(r"^\[\d{2,3}\s*-\s*[0-9A-Za-z#b]+\]\s*")


def _load_or_init_id3(mp3_path: Path) -> ID3:
    try:
        return ID3(str(mp3_path))
    except ID3NoHeaderError:
        return ID3()


def _fetch_artwork(cover_url: str) -> tuple[bytes, str] | None:
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(cover_url)
            if resp.status_code != 200 or not resp.content:
                return None
            if len(resp.content) > MAX_ARTWORK_BYTES:
                return None
            content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip().lower()
            if content_type not in {"image/jpeg", "image/jpg", "image/png"}:
                content_type = "image/jpeg"
            return resp.content, content_type
    except Exception:
        return None


def write_track_tags(
    mp3_path: Path,
    track: TrackSpec | None = None,
    analysis: AnalysisResult | None = None,
    key_format: KeyFormat = "camelot",
    embed_artwork: bool = True,
) -> None:
    """Write ID3v2.3 metadata and/or DJ BPM + Key tags into an MP3 file."""
    tags = _load_or_init_id3(mp3_path)

    if track is not None:
        if track.title:
            tags.delall("TIT2")
            tags.add(TIT2(encoding=1, text=[track.title]))
        if track.artist:
            tags.delall("TPE1")
            tags.add(TPE1(encoding=1, text=[track.artist]))
        album_val = track.album or track.playlist_name or ""
        if album_val:
            tags.delall("TALB")
            tags.add(TALB(encoding=1, text=[album_val]))

        if embed_artwork and track.cover_url and not tags.getall("APIC"):
            artwork = _fetch_artwork(track.cover_url)
            if artwork is not None:
                img_bytes, mime = artwork
                tags.add(
                    APIC(
                        encoding=1,
                        mime=mime,
                        type=3,
                        desc="Cover",
                        data=img_bytes,
                    )
                )

    if analysis is not None:
        tags.delall("TBPM")
        tags.add(TBPM(encoding=1, text=[str(analysis.bpm_rounded)]))
        key_value = analysis.camelot_key if key_format == "camelot" else analysis.musical_key
        tags.delall("TKEY")
        tags.add(TKEY(encoding=1, text=[key_value]))

    tags.save(str(mp3_path), v2_version=3)


def read_dj_tags(mp3_path: Path) -> tuple[int | None, str | None]:
    """Read existing (TBPM, TKEY) from an MP3 file if present."""
    try:
        tags = ID3(str(mp3_path))
    except ID3NoHeaderError:
        return None, None

    bpm_val: int | None = None
    tbpm = tags.get("TBPM")
    if tbpm and tbpm.text:
        try:
            bpm_val = int(round(float(str(tbpm.text[0]))))
        except ValueError:
            bpm_val = None

    key_val: str | None = None
    tkey = tags.get("TKEY")
    if tkey and tkey.text:
        key_val = str(tkey.text[0]).strip() or None

    return bpm_val, key_val


def apply_filename_prefix(
    mp3_path: Path,
    analysis: AnalysisResult,
    key_format: KeyFormat = "camelot",
) -> Path:
    """Rename 'Artist - Title.mp3' -> '[128 - 8A] Artist - Title.mp3' idempotently."""
    key_label = analysis.camelot_key if key_format == "camelot" else analysis.musical_key
    clean_stem = EXISTING_PREFIX_RE.sub("", mp3_path.stem).strip()
    new_name = f"[{analysis.bpm_rounded} - {key_label}] {clean_stem}{mp3_path.suffix}"
    target = mp3_path.with_name(new_name)
    if target == mp3_path:
        return mp3_path
    os.replace(mp3_path, target)
    return target
