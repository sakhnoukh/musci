"""Spotify playlist, album, and track metadata resolver (Zero-auth Embed + Official Web API)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import httpx

from musci.models import PlaylistBatch, TrackSpec

SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative"

SPOTIFY_URI_RE = re.compile(r"^spotify:(playlist|album|track):([A-Za-z0-9]{15,30})$")
SPOTIFY_URL_RE = re.compile(
    r"^https?://open\.spotify\.com/(?:intl-[A-Za-z-]+/)?(?:embed/)?(playlist|album|track)/([A-Za-z0-9]{15,30})"
)
NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)

EMBED_TRACK_CAP = 100
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class SpotifyResolutionError(RuntimeError):
    """Raised when a Spotify URL cannot be parsed or fetched."""


def parse_spotify_ref(raw: str) -> tuple[str, str] | None:
    """Extract (kind, spotify_id) from a Spotify URL or URI, or return None."""
    cleaned = raw.strip()
    uri_match = SPOTIFY_URI_RE.match(cleaned)
    if uri_match:
        return uri_match.group(1), uri_match.group(2)

    url_match = SPOTIFY_URL_RE.match(cleaned)
    if url_match:
        return url_match.group(1), url_match.group(2)
    return None


def is_spotify_input(raw: str) -> bool:
    """Return True if raw string is a supported Spotify playlist, album, or track URL/URI."""
    return parse_spotify_ref(raw) is not None


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("\u00a0", " ").strip()


def _extract_spotify_id(uri_or_id: str | None) -> str | None:
    if not uri_or_id:
        return None
    if ":" in uri_or_id:
        return uri_or_id.rsplit(":", 1)[-1]
    return uri_or_id


def _extract_embed_cover(entity: dict[str, Any]) -> str | None:
    sources = entity.get("coverArt", {}).get("sources") or []
    if sources and isinstance(sources[-1], dict):
        url = sources[-1].get("url")
        if url:
            return str(url)
    images = entity.get("visualIdentity", {}).get("image") or []
    if images and isinstance(images[-1], dict):
        url = images[-1].get("url")
        if url:
            return str(url)
    return None


def parse_embed_html(html: str, kind: str, spotify_id: str) -> PlaylistBatch:
    """Parse Spotify embed SSR HTML (__NEXT_DATA__) into a PlaylistBatch."""
    match = NEXT_DATA_RE.search(html)
    if not match:
        raise SpotifyResolutionError(
            f"Could not find __NEXT_DATA__ payload on Spotify embed page for {kind}/{spotify_id}."
        )

    try:
        payload = json.loads(match.group(1))
        entity = payload["props"]["pageProps"]["state"]["data"]["entity"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SpotifyResolutionError(
            f"Unexpected Spotify embed payload structure for {kind}/{spotify_id}: {exc}"
        ) from exc

    entity_name = _clean_text(entity.get("name") or entity.get("title"))
    cover_url = _extract_embed_cover(entity)
    warnings: list[str] = []

    if kind == "track":
        artists_list = entity.get("artists") or []
        if artists_list and isinstance(artists_list, list):
            artist = ", ".join(
                _clean_text(a.get("name")) for a in artists_list if isinstance(a, dict) and a.get("name")
            )
        else:
            artist = _clean_text(entity.get("subtitle"))
        duration_ms = entity.get("duration")
        duration_sec = round(duration_ms / 1000.0, 2) if isinstance(duration_ms, (int, float)) else None
        track = TrackSpec(
            title=entity_name,
            artist=artist,
            duration_sec=duration_sec,
            source_id=f"spotify:track:{spotify_id}",
            cover_url=cover_url,
        )
        return PlaylistBatch(name=None, tracks=[track], warnings=warnings)

    raw_tracks = entity.get("trackList") or []
    tracks: list[TrackSpec] = []
    for item in raw_tracks:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"))
        if not title:
            continue
        artist = _clean_text(item.get("subtitle"))
        duration_ms = item.get("duration")
        duration_sec = round(duration_ms / 1000.0, 2) if isinstance(duration_ms, (int, float)) else None
        track_id = _extract_spotify_id(item.get("uri"))
        tracks.append(
            TrackSpec(
                title=title,
                artist=artist,
                album=entity_name if kind == "album" else "",
                duration_sec=duration_sec,
                source_id=f"spotify:track:{track_id}" if track_id else None,
                cover_url=cover_url,
                playlist_name=entity_name or None,
            )
        )

    if kind == "playlist" and len(tracks) >= EMBED_TRACK_CAP:
        warnings.append(
            f"Spotify embed returned {len(tracks)} tracks (public embed cap is ~{EMBED_TRACK_CAP}). "
            "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env to fetch larger playlists."
        )

    return PlaylistBatch(name=entity_name or f"spotify-{spotify_id}", tracks=tracks, warnings=warnings)


def _fetch_via_embed(client: httpx.Client, kind: str, spotify_id: str) -> PlaylistBatch:
    url = f"https://open.spotify.com/embed/{kind}/{spotify_id}"
    response = client.get(url, headers=DEFAULT_HEADERS, follow_redirects=True)
    if response.status_code != 200:
        raise SpotifyResolutionError(
            f"Spotify embed request failed for {kind}/{spotify_id} (HTTP {response.status_code})."
        )
    return parse_embed_html(response.text, kind, spotify_id)


def _get_spotify_api_token(
    client: httpx.Client,
    client_id: str,
    client_secret: str,
    refresh_token: str | None = None,
) -> str:
    if refresh_token:
        resp = client.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(client_id, client_secret),
        )
        resp.raise_for_status()
        return str(resp.json()["access_token"])

    resp = client.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


def run_spotify_oauth_login(
    client_id: str,
    client_secret: str,
    redirect_uri: str = SPOTIFY_REDIRECT_URI,
    timeout_sec: float = 120.0,
) -> str:
    """Run one-time localhost OAuth browser login and return SPOTIFY_REFRESH_TOKEN."""
    parsed_redirect = urlparse(redirect_uri)
    host = parsed_redirect.hostname or "127.0.0.1"
    port = parsed_redirect.port or 8888
    state = secrets.token_urlsafe(16)
    captured: dict[str, str] = {}

    class _CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            if "error" in qs:
                captured["error"] = qs["error"][0]
            if "state" in qs:
                captured["state"] = qs["state"][0]
            if "code" in qs:
                captured["code"] = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;padding:2rem;'>"
                b"<h2>Spotify connected to musci!</h2>"
                b"<p>You can close this tab and return to your terminal.</p>"
                b"</body></html>"
            )

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    auth_params = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SPOTIFY_SCOPES,
            "state": state,
        }
    )
    auth_url = f"https://accounts.spotify.com/authorize?{auth_params}"

    server = HTTPServer((host, port), _CallbackHandler)
    server.timeout = timeout_sec
    try:
        webbrowser.open(auth_url)
        server.handle_request()
    finally:
        server.server_close()

    if "error" in captured:
        raise SpotifyResolutionError(f"Spotify authorization declined: {captured['error']}")
    if captured.get("state") != state or "code" not in captured:
        raise SpotifyResolutionError("Timed out or received invalid OAuth callback from Spotify.")

    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": captured["code"],
                "redirect_uri": redirect_uri,
            },
            auth=(client_id, client_secret),
        )
        resp.raise_for_status()
        token_data = resp.json()

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise SpotifyResolutionError("Spotify token response did not include a refresh_token.")
    return str(refresh_token)


def _parse_api_track(track_obj: dict[str, Any], playlist_name: str | None = None, fallback_album: str = "", fallback_cover: str | None = None) -> TrackSpec | None:
    if not isinstance(track_obj, dict) or track_obj.get("is_local"):
        return None
    title = _clean_text(track_obj.get("name"))
    if not title:
        return None
    artists = ", ".join(
        _clean_text(a.get("name")) for a in (track_obj.get("artists") or []) if isinstance(a, dict) and a.get("name")
    )
    album_obj = track_obj.get("album") or {}
    album_name = _clean_text(album_obj.get("name")) or fallback_album
    images = album_obj.get("images") or []
    cover_url = images[0].get("url") if images and isinstance(images[0], dict) else fallback_cover
    duration_ms = track_obj.get("duration_ms")
    duration_sec = round(duration_ms / 1000.0, 2) if isinstance(duration_ms, (int, float)) else None
    track_id = track_obj.get("id")
    return TrackSpec(
        title=title,
        artist=artists,
        album=album_name,
        duration_sec=duration_sec,
        source_id=f"spotify:track:{track_id}" if track_id else None,
        cover_url=cover_url,
        playlist_name=playlist_name,
    )


def _fetch_via_web_api(
    client: httpx.Client,
    kind: str,
    spotify_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str | None = None,
) -> PlaylistBatch:
    token = _get_spotify_api_token(client, client_id, client_secret, refresh_token=refresh_token)
    headers = {"Authorization": f"Bearer {token}"}

    if kind == "track":
        resp = client.get(f"https://api.spotify.com/v1/tracks/{spotify_id}", headers=headers)
        resp.raise_for_status()
        spec = _parse_api_track(resp.json())
        return PlaylistBatch(name=None, tracks=[spec] if spec else [], warnings=[])

    if kind == "album":
        resp = client.get(f"https://api.spotify.com/v1/albums/{spotify_id}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        album_name = _clean_text(data.get("name"))
        images = data.get("images") or []
        cover_url = images[0].get("url") if images and isinstance(images[0], dict) else None
        tracks: list[TrackSpec] = []
        next_url: str | None = f"https://api.spotify.com/v1/albums/{spotify_id}/tracks?limit=50"
        while next_url:
            page = client.get(next_url, headers=headers)
            page.raise_for_status()
            page_data = page.json()
            for item in page_data.get("items") or []:
                spec = _parse_api_track(item, playlist_name=album_name, fallback_album=album_name, fallback_cover=cover_url)
                if spec:
                    tracks.append(spec)
            next_url = page_data.get("next")
        return PlaylistBatch(name=album_name or f"spotify-{spotify_id}", tracks=tracks, warnings=[])

    meta_resp = client.get(
        f"https://api.spotify.com/v1/playlists/{spotify_id}?fields=name,images",
        headers=headers,
    )
    meta_resp.raise_for_status()
    meta = meta_resp.json()
    playlist_name = _clean_text(meta.get("name")) or f"spotify-{spotify_id}"
    images = meta.get("images") or []
    playlist_cover = images[0].get("url") if images and isinstance(images[0], dict) else None

    tracks = []
    # Spotify Feb 2026 API uses /items (and entry['item']); falls back to /tracks if needed
    next_url = f"https://api.spotify.com/v1/playlists/{spotify_id}/items?limit=100"
    while next_url:
        page = client.get(next_url, headers=headers)
        page.raise_for_status()
        page_data = page.json()
        for entry in page_data.get("items") or []:
            if not isinstance(entry, dict):
                continue
            track_obj = entry.get("item") or entry.get("track")
            spec = _parse_api_track(track_obj or {}, playlist_name=playlist_name, fallback_cover=playlist_cover)
            if spec:
                tracks.append(spec)
        next_url = page_data.get("next")

    if not tracks:
        raise SpotifyResolutionError(
            "Spotify API returned 0 tracks (Spotify Dev Mode only returns >100-track items for playlists "
            "you own/collaborate on after running 'musci spotify-login')."
        )

    return PlaylistBatch(name=playlist_name, tracks=tracks, warnings=[])


def fetch_spotify_batch(
    raw_url: str,
    client_id: str | None = None,
    client_secret: str | None = None,
    refresh_token: str | None = None,
    timeout_sec: float = 20.0,
) -> PlaylistBatch:
    """Resolve a Spotify playlist, album, or track URL/URI into a PlaylistBatch."""
    parsed = parse_spotify_ref(raw_url)
    if not parsed:
        raise SpotifyResolutionError(f"Unsupported Spotify URL or URI: {raw_url}")
    kind, spotify_id = parsed

    with httpx.Client(timeout=timeout_sec) as client:
        if client_id and client_secret:
            try:
                return _fetch_via_web_api(
                    client,
                    kind,
                    spotify_id,
                    client_id,
                    client_secret,
                    refresh_token=refresh_token,
                )
            except (httpx.HTTPError, SpotifyResolutionError) as exc:
                batch = _fetch_via_embed(client, kind, spotify_id)
                batch.warnings.insert(
                    0,
                    f"Spotify Web API could not read full playlist ({exc}); fell back to public embed (~100 tracks). "
                    "Tip: For 100+ track third-party playlists, copy the playlist into your own Spotify library "
                    "(Right-click playlist -> Add to other playlist -> New playlist) and run 'musci spotify-login'.",
                )
                return batch
        return _fetch_via_embed(client, kind, spotify_id)
