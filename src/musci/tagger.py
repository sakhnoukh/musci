"""ID3v2.3 (MP3) & Vorbis comment (FLAC) metadata/DJ tagger and filename prefixer."""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
from mutagen.flac import FLAC, Picture
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


def _write_flac_tags(
    flac_path: Path,
    track: TrackSpec | None = None,
    analysis: AnalysisResult | None = None,
    key_format: KeyFormat = "camelot",
    embed_artwork: bool = True,
) -> None:
    audio = FLAC(str(flac_path))
    if track is not None:
        if track.title:
            audio["TITLE"] = [track.title]
        if track.artist:
            audio["ARTIST"] = [track.artist]
        album_val = track.album or track.playlist_name or ""
        if album_val:
            audio["ALBUM"] = [album_val]

        if embed_artwork and track.cover_url and not audio.pictures:
            artwork = _fetch_artwork(track.cover_url)
            if artwork is not None:
                img_bytes, mime = artwork
                pic = Picture()
                pic.type = 3
                pic.mime = mime
                pic.desc = "Cover"
                pic.data = img_bytes
                audio.add_picture(pic)

    if analysis is not None:
        audio["BPM"] = [str(analysis.bpm_rounded)]
        audio["TBPM"] = [str(analysis.bpm_rounded)]
        key_value = analysis.camelot_key if key_format == "camelot" else analysis.musical_key
        audio["KEY"] = [key_value]
        audio["INITIALKEY"] = [key_value]

    audio.save()


def write_track_tags(
    mp3_path: Path,
    track: TrackSpec | None = None,
    analysis: AnalysisResult | None = None,
    key_format: KeyFormat = "camelot",
    embed_artwork: bool = True,
) -> None:
    """Write ID3v2.3 (MP3) or Vorbis (FLAC) metadata and DJ BPM + Key tags."""
    if mp3_path.suffix.lower() == ".flac":
        _write_flac_tags(
            mp3_path,
            track=track,
            analysis=analysis,
            key_format=key_format,
            embed_artwork=embed_artwork,
        )
        return

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
    """Read existing (BPM, KEY) from an MP3 or FLAC file if present."""
    if mp3_path.suffix.lower() == ".flac":
        try:
            audio = FLAC(str(mp3_path))
        except Exception:
            return None, None
        bpm_val: int | None = None
        raw_bpm_list = audio.get("BPM") or audio.get("TBPM")
        if raw_bpm_list:
            try:
                bpm_val = int(round(float(str(raw_bpm_list[0]))))
            except ValueError:
                bpm_val = None
        key_val: str | None = None
        raw_key_list = audio.get("INITIALKEY") or audio.get("KEY") or audio.get("TKEY")
        if raw_key_list:
            key_val = str(raw_key_list[0]).strip() or None
        return bpm_val, key_val

    try:
        tags = ID3(str(mp3_path))
    except ID3NoHeaderError:
        return None, None

    bpm_val = None
    tbpm = tags.get("TBPM")
    if tbpm and tbpm.text:
        try:
            bpm_val = int(round(float(str(tbpm.text[0]))))
        except ValueError:
            bpm_val = None

    key_val = None
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
