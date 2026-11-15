"""Playlist and track source resolvers (Spotify, Apple Music, YouTube/yt-dlp, plain queries)."""

from __future__ import annotations

from pathlib import Path

from musci.models import PlaylistBatch
from musci.sources.apple_music import fetch_apple_music_batch, is_apple_music_input
from musci.sources.spotify import fetch_spotify_batch, is_spotify_input
from musci.sources.ytdlp_source import fetch_ytdlp_batch


def resolve_inputs(
    inputs: list[str],
    from_file: Path | None = None,
    spotify_client_id: str | None = None,
    spotify_client_secret: str | None = None,
    spotify_refresh_token: str | None = None,
) -> list[PlaylistBatch]:
    """Resolve CLI URLs/queries (and optional batch file) into PlaylistBatch objects."""
    raw_items: list[str] = [item.strip() for item in inputs if item.strip()]
    if from_file is not None:
        lines = from_file.read_text(encoding="utf-8").splitlines()
        for line in lines:
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                raw_items.append(cleaned)

    batches: list[PlaylistBatch] = []
    for item in raw_items:
        if is_spotify_input(item):
            batches.append(
                fetch_spotify_batch(
                    item,
                    client_id=spotify_client_id,
                    client_secret=spotify_client_secret,
                    refresh_token=spotify_refresh_token,
                )
            )
        elif is_apple_music_input(item):
            batches.append(fetch_apple_music_batch(item))
        else:
            batches.append(fetch_ytdlp_batch(item))
    return batches
