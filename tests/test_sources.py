"""Unit tests for Spotify URL/embed parsing and YouTube candidate scoring."""

from __future__ import annotations

import json

import pytest

from musci.models import TrackSpec
from musci.sources.apple_music import (
    AppleMusicRef,
    AppleMusicResolutionError,
    format_apple_artwork_url,
    is_apple_music_input,
    parse_apple_music_ref,
    parse_apple_ssr_html,
)
from musci.sources.spotify import (
    SpotifyResolutionError,
    is_spotify_input,
    parse_embed_html,
    parse_spotify_ref,
)
from musci.sources.ytdlp_source import pick_best_candidate, score_search_candidate


def _make_embed_html(entity: dict) -> str:
    payload = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return (
        "<!DOCTYPE html><html><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )


def test_parse_spotify_ref_variants() -> None:
    assert parse_spotify_ref("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123") == (
        "playlist",
        "37i9dQZF1DXcBWIGoYBM5M",
    )
    assert parse_spotify_ref("https://open.spotify.com/intl-en/album/4LH4d3cOWNNsVw41Gqt2kv") == (
        "album",
        "4LH4d3cOWNNsVw41Gqt2kv",
    )
    assert parse_spotify_ref("https://open.spotify.com/embed/track/4PTG3Z6ehGkBFwjybzWkR8") == (
        "track",
        "4PTG3Z6ehGkBFwjybzWkR8",
    )
    assert parse_spotify_ref("spotify:playlist:37i9dQZF1DX4dyzvuaRJ0n") == (
        "playlist",
        "37i9dQZF1DX4dyzvuaRJ0n",
    )
    assert parse_spotify_ref("https://www.youtube.com/playlist?list=PL12345") is None
    assert is_spotify_input("spotify:track:4PTG3Z6ehGkBFwjybzWkR8") is True
    assert is_spotify_input("Daft Punk - One More Time") is False


def test_parse_embed_html_playlist_and_non_breaking_spaces() -> None:
    entity = {
        "type": "playlist",
        "name": "House Mixing Crate",
        "coverArt": {"sources": [{"url": "https://i.scdn.co/image/cover640.jpg"}]},
        "trackList": [
            {
                "uri": "spotify:track:0Gms8M0f1kJxAA2pXuLdtI",
                "title": "One More Time",
                "subtitle": "Daft\u00a0Punk",
                "duration": 320400,
            },
            {
                "uri": "spotify:track:1Bms8M0f1kJxAA2pXuLdtZ",
                "title": "Cola",
                "subtitle": "CamelPhat,\u00a0Elderbrook",
                "duration": 258000,
            },
        ],
    }
    batch = parse_embed_html(_make_embed_html(entity), "playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert batch.name == "House Mixing Crate"
    assert batch.warnings == []
    assert len(batch.tracks) == 2
    assert batch.tracks[0].artist == "Daft Punk"
    assert batch.tracks[0].title == "One More Time"
    assert batch.tracks[0].duration_sec == 320.4
    assert batch.tracks[0].source_id == "spotify:track:0Gms8M0f1kJxAA2pXuLdtI"
    assert batch.tracks[0].cover_url == "https://i.scdn.co/image/cover640.jpg"
    assert batch.tracks[1].artist == "CamelPhat, Elderbrook"


def test_parse_embed_html_warns_at_100_track_cap() -> None:
    entity = {
        "type": "playlist",
        "name": "Big Club Playlist",
        "trackList": [
            {
                "uri": f"spotify:track:{idx:022d}",
                "title": f"Track {idx}",
                "subtitle": "Artist",
                "duration": 180000,
            }
            for idx in range(100)
        ],
    }
    batch = parse_embed_html(_make_embed_html(entity), "playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert len(batch.tracks) == 100
    assert len(batch.warnings) == 1
    assert "SPOTIFY_CLIENT_ID" in batch.warnings[0]


def test_parse_embed_html_single_track_and_invalid_html() -> None:
    entity = {
        "type": "track",
        "name": "Strobe",
        "artists": [{"name": "deadmau5"}],
        "duration": 637000,
    }
    batch = parse_embed_html(_make_embed_html(entity), "track", "4PTG3Z6ehGkBFwjybzWkR8")
    assert batch.name is None
    assert len(batch.tracks) == 1
    assert batch.tracks[0].artist == "deadmau5"
    assert batch.tracks[0].title == "Strobe"
    assert batch.tracks[0].duration_sec == 637.0

    with pytest.raises(SpotifyResolutionError):
        parse_embed_html("<html><body>no script</body></html>", "playlist", "37i9dQZF1DXcBWIGoYBM5M")


def test_candidate_scoring_prefers_duration_matched_studio_audio() -> None:
    target = TrackSpec(title=" Cola ", artist="CamelPhat, Elderbrook", duration_sec=258.0)
    candidates = [
        {
            "id": "vid_music_video",
            "title": "CamelPhat & Elderbrook - Cola (Official Music Video)",
            "channel": "CamelPhatVEVO",
            "duration": 295,  # 37s narrative intro/outro mismatch
        },
        {
            "id": "vid_slowed",
            "title": "CamelPhat - Cola (Slowed + Reverb)",
            "channel": "ChillEdits",
            "duration": 260,
        },
        {
            "id": "vid_live",
            "title": "CamelPhat & Elderbrook - Cola (Live at Printworks)",
            "channel": "DJ Clips",
            "duration": 258,
        },
        {
            "id": "vid_topic_studio",
            "title": "Cola",
            "channel": "CamelPhat - Topic",
            "duration": 258,
        },
        {
            "id": "vid_hour_loop",
            "title": "CamelPhat - Cola (1 Hour Loop)",
            "channel": "Loops",
            "duration": 3600,
        },
    ]

    best = pick_best_candidate(target, candidates)
    assert best is not None
    assert best["id"] == "vid_topic_studio"


def test_candidate_scoring_allows_live_when_requested_in_track_title() -> None:
    live_track = TrackSpec(title="Alive 2007 (Live)", artist="Daft Punk", duration_sec=330.0)
    live_entry = {
        "id": "live1",
        "title": "Daft Punk - Alive 2007 (Live)",
        "channel": "Daft Punk - Topic",
        "duration": 330,
    }
    karaoke_entry = {
        "id": "kar1",
        "title": "Daft Punk - Alive 2007 Karaoke",
        "channel": "Karaoke Fun",
        "duration": 330,
    }
    assert score_search_candidate(live_track, live_entry) > score_search_candidate(live_track, karaoke_entry)


def _make_apple_ssr_html(sections: list[dict]) -> str:
    payload = {"data": [{"intent": {}, "data": {"sections": sections}}]}
    return (
        "<!DOCTYPE html><html><body>"
        f'<script id="serialized-server-data" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )


def test_parse_apple_music_ref_variants_and_library_guard() -> None:
    pl = parse_apple_music_ref("https://music.apple.com/us/playlist/acid-house/pl.u-8aAVZ6oIv0p123")
    assert pl == AppleMusicRef(storefront="us", kind="playlist", item_id="pl.u-8aAVZ6oIv0p123", track_filter_id=None)

    song_in_album = parse_apple_music_ref("https://music.apple.com/de/album/self-aware/1877803357?i=1877803360")
    assert song_in_album == AppleMusicRef(
        storefront="de",
        kind="album",
        item_id="1877803357",
        track_filter_id="1877803360",
    )

    direct_song = parse_apple_music_ref("https://embed.music.apple.com/gb/song/da-funk/696884591")
    assert direct_song == AppleMusicRef(storefront="gb", kind="song", item_id="696884591", track_filter_id=None)

    assert is_apple_music_input("https://music.apple.com/us/playlist/pl.9b672a18307c4cd7ba1ece0106891868") is True
    assert is_apple_music_input("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") is False

    with pytest.raises(AppleMusicResolutionError, match="Share -> Copy Link"):
        parse_apple_music_ref("https://music.apple.com/library/playlist/p.12345abc")


def test_parse_apple_ssr_html_playlist_and_single_track_filter() -> None:
    sections = [
        {
            "itemKind": "containerDetailHeaderLockup",
            "items": [
                {
                    "title": "Acid Crate",
                    "artwork": {"dictionary": {"url": "https://is1-ssl.mzstatic.com/img/{w}x{h}bb.{f}"}},
                }
            ],
        },
        {
            "itemKind": "trackLockup",
            "items": [
                {
                    "title": "Acid Tracks",
                    "artistName": "Phuture",
                    "duration": 732000,
                    "tertiaryLinks": [{"title": "Acid Tracks - EP"}],
                    "contentDescriptor": {"identifiers": {"storeAdamID": "1001"}},
                    "artwork": {"dictionary": {"url": "https://is1-ssl.mzstatic.com/t1/{w}x{h}bb.{f}"}},
                },
                {
                    "title": "Energy Flash",
                    "artistName": "Joey\u00a0Beltram",
                    "duration": 350500,
                    "tertiaryLinks": [{"title": "Energy Flash - Single"}],
                    "contentDescriptor": {"identifiers": {"storeAdamID": "1002"}},
                },
            ],
        },
    ]
    html = _make_apple_ssr_html(sections)

    pl_ref = AppleMusicRef(storefront="us", kind="playlist", item_id="pl.test123")
    batch = parse_apple_ssr_html(html, pl_ref)
    assert batch.name == "Acid Crate"
    assert len(batch.tracks) == 2
    assert batch.tracks[0].title == "Acid Tracks"
    assert batch.tracks[0].artist == "Phuture"
    assert batch.tracks[0].album == "Acid Tracks - EP"
    assert batch.tracks[0].duration_sec == 732.0
    assert batch.tracks[0].source_id == "apple:song:1001"
    assert batch.tracks[0].cover_url == "https://is1-ssl.mzstatic.com/t1/600x600bb.jpg"
    assert batch.tracks[1].artist == "Joey Beltram"
    assert batch.tracks[1].cover_url == "https://is1-ssl.mzstatic.com/img/600x600bb.jpg"

    # Album URL with ?i=1002 should return only that specific track
    filtered_ref = AppleMusicRef(storefront="us", kind="album", item_id="999", track_filter_id="1002")
    single_batch = parse_apple_ssr_html(html, filtered_ref)
    assert single_batch.name is None
    assert len(single_batch.tracks) == 1
    assert single_batch.tracks[0].title == "Energy Flash"
    assert format_apple_artwork_url("https://example.com/{w}x{h}bb.{f}", size=300) == "https://example.com/300x300bb.jpg"
