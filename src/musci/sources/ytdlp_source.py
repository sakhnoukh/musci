"""YouTube / yt-dlp playlist resolver and duration-scored audio search matcher."""

from __future__ import annotations

import re
from typing import Any

import yt_dlp

from musci.models import PlaylistBatch, TrackSpec

UNWANTED_VARIANTS: tuple[str, ...] = (
    "karaoke",
    "8d audio",
    "nightcore",
    "slowed",
    "reverb",
    "acapella",
    "instrumental",
    "tribute",
    "cover",
    "live at",
    "live from",
    "reaction",
    "tutorial",
)

TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


def score_search_candidate(track: TrackSpec, entry: dict[str, Any]) -> float:
    """Score a YouTube search result dictionary against a target TrackSpec."""
    title = str(entry.get("title") or "")
    channel = str(entry.get("channel") or entry.get("uploader") or "")
    if not title:
        return -1000.0
    if entry.get("is_live"):
        return -1000.0

    score = 100.0
    cand_lower = f"{title} {channel}".lower()
    expected_lower = f"{track.artist} {track.title}".lower()

    # 1. Duration match against Spotify / reference track length
    cand_dur = entry.get("duration")
    if track.duration_sec is not None and isinstance(cand_dur, (int, float)) and cand_dur > 0:
        diff = abs(float(cand_dur) - track.duration_sec)
        if diff <= 3.0:
            score += 30.0
        elif diff <= 8.0:
            score += 18.0
        elif diff <= 15.0:
            score += 4.0
        else:
            score -= min(90.0, (diff - 15.0) * 2.5)
    elif isinstance(cand_dur, (int, float)) and cand_dur > 900:
        # Without known duration, avoid >15min mixes/compilations for single track searches
        score -= 50.0

    # 2. Penalize unwanted variants (live, slowed, karaoke, etc.) unless requested in original title
    for variant in UNWANTED_VARIANTS:
        if variant in cand_lower and variant not in expected_lower:
            score -= 35.0
    if re.search(r"\blive\b", cand_lower) and not re.search(r"\blive\b", expected_lower):
        score -= 25.0

    # 3. Prefer official Topic / audio uploads over narrative music videos
    channel_lower = channel.lower()
    title_lower = title.lower()
    if channel_lower.endswith("- topic") or "official audio" in title_lower:
        score += 18.0
    elif "lyric" in title_lower or "audio" in title_lower:
        score += 6.0
    elif "official video" in title_lower or "music video" in title_lower:
        score -= 5.0

    # 4. Token overlap with artist and title
    expected_tokens = _tokens(expected_lower)
    cand_tokens = _tokens(cand_lower)
    if expected_tokens:
        overlap_ratio = len(expected_tokens & cand_tokens) / len(expected_tokens)
        score += overlap_ratio * 25.0

    return round(score, 3)


def pick_best_candidate(track: TrackSpec, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the highest-scoring search candidate entry, or None if no valid candidates exist."""
    valid = [e for e in entries if isinstance(e, dict) and (e.get("url") or e.get("webpage_url") or e.get("id"))]
    if not valid:
        return None
    return max(valid, key=lambda item: score_search_candidate(track, item))


def _entry_to_url(entry: dict[str, Any]) -> str:
    url = entry.get("webpage_url") or entry.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    vid_id = entry.get("id") or url
    return f"https://www.youtube.com/watch?v={vid_id}"


def resolve_search_url(track: TrackSpec, max_results: int = 5) -> str:
    """Find the best-matching YouTube URL for a metadata-only TrackSpec."""
    query = f"{track.artist} - {track.title}".strip(" -")
    ydl_opts: dict[str, Any] = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)

    entries = (info or {}).get("entries") or []
    best = pick_best_candidate(track, list(entries))
    if not best:
        raise RuntimeError(f"No matching audio results found for '{query}'.")
    return _entry_to_url(best)


def _split_artist_title(raw_title: str, uploader: str = "") -> tuple[str, str]:
    cleaned_uploader = re.sub(r"\s*-\s*Topic$", "", uploader.strip(), flags=re.IGNORECASE)
    if " - " in raw_title:
        artist, title = raw_title.split(" - ", 1)
        return artist.strip(), title.strip()
    return cleaned_uploader, raw_title.strip()


def fetch_ytdlp_batch(raw_input: str) -> PlaylistBatch:
    """Resolve a YouTube/SoundCloud/Bandcamp URL or plain 'Artist - Title' query into a PlaylistBatch."""
    cleaned = raw_input.strip()
    if not cleaned.startswith(("http://", "https://")):
        artist, title = _split_artist_title(cleaned)
        return PlaylistBatch(
            name=None,
            tracks=[TrackSpec(title=title, artist=artist)],
            warnings=[],
        )

    ydl_opts: dict[str, Any] = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(cleaned, download=False)

    if not info:
        raise RuntimeError(f"Could not extract track or playlist metadata from URL: {cleaned}")

    warnings: list[str] = []
    if info.get("_type") == "playlist" or "entries" in info:
        playlist_title = str(info.get("title") or "playlist").strip()
        tracks: list[TrackSpec] = []
        skipped_unavailable = 0
        for entry in info.get("entries") or []:
            if not isinstance(entry, dict):
                skipped_unavailable += 1
                continue
            raw_title = str(entry.get("title") or "").strip()
            if not raw_title or raw_title in {"[Deleted video]", "[Private video]"}:
                skipped_unavailable += 1
                continue
            uploader = str(entry.get("channel") or entry.get("uploader") or "")
            artist, title = _split_artist_title(raw_title, uploader)
            dur = entry.get("duration")
            duration_sec = float(dur) if isinstance(dur, (int, float)) else None
            entry_url = _entry_to_url(entry)
            entry_id = entry.get("id")
            thumbnails = entry.get("thumbnails") or []
            cover_url = thumbnails[-1].get("url") if thumbnails and isinstance(thumbnails[-1], dict) else None
            tracks.append(
                TrackSpec(
                    title=title,
                    artist=artist,
                    duration_sec=duration_sec,
                    source_id=f"ytdlp:{entry_id}" if entry_id else entry_url,
                    direct_url=entry_url,
                    cover_url=cover_url,
                    playlist_name=playlist_title,
                )
            )
        if skipped_unavailable:
            warnings.append(f"Skipped {skipped_unavailable} unavailable/private entries in '{playlist_title}'.")
        return PlaylistBatch(name=playlist_title, tracks=tracks, warnings=warnings)

    raw_title = str(info.get("title") or cleaned).strip()
    uploader = str(info.get("channel") or info.get("uploader") or "")
    artist, title = _split_artist_title(raw_title, uploader)
    dur = info.get("duration")
    duration_sec = float(dur) if isinstance(dur, (int, float)) else None
    vid_id = info.get("id")
    cover_url = info.get("thumbnail")
    webpage_url = str(info.get("webpage_url") or cleaned)
    return PlaylistBatch(
        name=None,
        tracks=[
            TrackSpec(
                title=title,
                artist=artist,
                album=str(info.get("album") or ""),
                duration_sec=duration_sec,
                source_id=f"ytdlp:{vid_id}" if vid_id else webpage_url,
                direct_url=webpage_url,
                cover_url=str(cover_url) if cover_url else None,
            )
        ],
        warnings=[],
    )
