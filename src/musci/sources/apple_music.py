"""Apple Music playlist, album, and song metadata resolver (Zero-auth SSR + AMP catalog pagination)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from musci.models import PlaylistBatch, TrackSpec

APPLE_MUSIC_HOSTS = {"music.apple.com", "embed.music.apple.com", "itunes.apple.com"}
APPLE_PATH_RE = re.compile(
    r"^/([a-zA-Z]{2})/(playlist|album|song)/(?:[^/]+/)?(pl\.[A-Za-z0-9._-]+|[0-9]+)$"
)
SERIALIZED_DATA_RE = re.compile(
    r'<script[^>]+id=["\']serialized-server-data["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)
JS_BUNDLE_RE = re.compile(r'src=["\'](/assets/index[^"\']+\.js)["\']')
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}")

SSR_TRACK_CAP = 100
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class AppleMusicResolutionError(RuntimeError):
    """Raised when an Apple Music URL cannot be parsed or fetched."""


@dataclass(frozen=True)
class AppleMusicRef:
    """Parsed Apple Music URL components."""

    storefront: str
    kind: str  # 'playlist' | 'album' | 'song'
    item_id: str
    track_filter_id: str | None = None

    @property
    def canonical_url(self) -> str:
        base = f"https://music.apple.com/{self.storefront}/{self.kind}/{self.item_id}"
        if self.track_filter_id:
            return f"{base}?i={self.track_filter_id}"
        return base


def parse_apple_music_ref(raw: str) -> AppleMusicRef | None:
    """Parse an Apple Music playlist, album, or song URL into an AppleMusicRef, or return None."""
    cleaned = raw.strip()
    if not cleaned.startswith(("http://", "https://")):
        return None
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    if host not in APPLE_MUSIC_HOSTS:
        return None

    path = parsed.path.rstrip("/")
    if path.startswith("/library/"):
        raise AppleMusicResolutionError(
            "Private Apple Music '/library/' URLs require local login. "
            "In Apple Music, click '...' -> Share -> Copy Link to get a public 'pl.u-...' playlist URL."
        )

    match = APPLE_PATH_RE.match(path)
    if not match:
        return None

    storefront, kind, item_id = match.group(1).lower(), match.group(2).lower(), match.group(3)
    qs = parse_qs(parsed.query)
    song_param = qs.get("i", [None])[0]
    track_filter_id = str(song_param).strip() if song_param else None

    return AppleMusicRef(
        storefront=storefront,
        kind=kind,
        item_id=item_id,
        track_filter_id=track_filter_id,
    )


def is_apple_music_input(raw: str) -> bool:
    """Return True if raw string is an Apple Music URL (including '/library/' for clear error reporting)."""
    cleaned = raw.strip()
    if not cleaned.startswith(("http://", "https://")):
        return False
    host = (urlparse(cleaned).hostname or "").lower()
    if host not in APPLE_MUSIC_HOSTS:
        return False
    if urlparse(cleaned).path.startswith("/library/"):
        return True
    return parse_apple_music_ref(cleaned) is not None


def format_apple_artwork_url(raw_artwork: Any, size: int = 600) -> str | None:
    """Convert an Apple Music artwork dict or template URL into a concrete JPEG URL."""
    url_template: str | None = None
    if isinstance(raw_artwork, str):
        url_template = raw_artwork
    elif isinstance(raw_artwork, dict):
        inner = raw_artwork.get("dictionary") if isinstance(raw_artwork.get("dictionary"), dict) else raw_artwork
        if isinstance(inner.get("url"), str):
            url_template = inner["url"]

    if not url_template:
        return None
    return (
        url_template.replace("{w}", str(size))
        .replace("{h}", str(size))
        .replace("{f}", "jpg")
    )


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\u00a0", " ").strip()


def _extract_sections(payload: Any) -> list[dict[str, Any]]:
    entries: list[Any]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        entries = payload["data"]
    elif isinstance(payload, list):
        entries = payload
    else:
        return []

    for entry in entries:
        if isinstance(entry, dict):
            data_obj = entry.get("data")
            if isinstance(data_obj, dict) and isinstance(data_obj.get("sections"), list):
                return [s for s in data_obj["sections"] if isinstance(s, dict)]
    return []


def _extract_subtitle_titles(item: dict[str, Any]) -> str:
    links = item.get("subtitleLinks") or []
    names = [
        _clean_text(link.get("title"))
        for link in links
        if isinstance(link, dict) and _clean_text(link.get("title"))
    ]
    return ", ".join(names)


def parse_apple_ssr_html(html: str, ref: AppleMusicRef) -> PlaylistBatch:
    """Parse Apple Music SSR HTML (<script id='serialized-server-data'>) into a PlaylistBatch."""
    match = SERIALIZED_DATA_RE.search(html)
    if not match:
        raise AppleMusicResolutionError(
            f"Could not find serialized-server-data on Apple Music page for {ref.kind}/{ref.item_id}."
        )

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise AppleMusicResolutionError(
            f"Invalid JSON in Apple Music serialized-server-data for {ref.kind}/{ref.item_id}: {exc}"
        ) from exc

    sections = _extract_sections(payload)
    if not sections:
        raise AppleMusicResolutionError(
            f"No content sections found on Apple Music page for {ref.kind}/{ref.item_id}."
        )

    header_title = ""
    header_artist = ""
    header_cover: str | None = None

    for sec in sections:
        kind = str(sec.get("itemKind") or "")
        items = sec.get("items") or []
        if kind in {"containerDetailHeaderLockup", "songDetailHeader"} and items and isinstance(items[0], dict):
            h = items[0]
            header_title = _clean_text(h.get("title"))
            header_artist = _clean_text(h.get("artistName")) or _extract_subtitle_titles(h)
            header_cover = format_apple_artwork_url(h.get("artwork"))
            break

    # Direct /song/<slug>/<id> page
    if ref.kind == "song":
        artist = header_artist
        if not artist:
            for sec in sections:
                if sec.get("itemKind") == "songDetailItemLockup":
                    for item in sec.get("items") or []:
                        if isinstance(item, dict) and _clean_text(item.get("artistName")):
                            artist = _clean_text(item["artistName"])
                            break
                if artist:
                    break
        if not header_title:
            raise AppleMusicResolutionError(f"Could not extract song title from Apple Music song {ref.item_id}.")
        return PlaylistBatch(
            name=None,
            tracks=[
                TrackSpec(
                    title=header_title,
                    artist=artist,
                    source_id=f"apple:song:{ref.item_id}",
                    cover_url=header_cover,
                )
            ],
            warnings=[],
        )

    tracks: list[TrackSpec] = []
    for sec in sections:
        if sec.get("itemKind") != "trackLockup":
            continue
        for item in sec.get("items") or []:
            if not isinstance(item, dict):
                continue
            title = _clean_text(item.get("title"))
            if not title:
                continue

            artist = _clean_text(item.get("artistName")) or _extract_subtitle_titles(item) or header_artist
            tertiary = item.get("tertiaryLinks") or []
            album = ""
            if tertiary and isinstance(tertiary[0], dict):
                album = _clean_text(tertiary[0].get("title"))
            if not album and ref.kind == "album":
                album = header_title

            dur_ms = item.get("duration")
            duration_sec = round(float(dur_ms) / 1000.0, 2) if isinstance(dur_ms, (int, float)) and dur_ms > 0 else None

            adam_id = (
                (item.get("contentDescriptor") or {})
                .get("identifiers", {})
                .get("storeAdamID")
            )
            song_id = str(adam_id).strip() if adam_id else None
            cover_url = format_apple_artwork_url(item.get("artwork")) or header_cover

            tracks.append(
                TrackSpec(
                    title=title,
                    artist=artist,
                    album=album,
                    duration_sec=duration_sec,
                    source_id=f"apple:song:{song_id}" if song_id else None,
                    cover_url=cover_url,
                    playlist_name=header_title if not ref.track_filter_id else None,
                )
            )

    if ref.track_filter_id:
        target_source_id = f"apple:song:{ref.track_filter_id}"
        matched = [t for t in tracks if t.source_id == target_source_id]
        if matched:
            return PlaylistBatch(name=None, tracks=matched, warnings=[])

    batch_name = header_title or f"apple-{ref.item_id}"
    return PlaylistBatch(name=batch_name, tracks=tracks, warnings=[])


def _extract_web_bearer_token(client: httpx.Client, page_html: str) -> str | None:
    bundle_match = JS_BUNDLE_RE.search(page_html)
    if not bundle_match:
        return None
    bundle_url = f"https://music.apple.com{bundle_match.group(1)}"
    resp = client.get(bundle_url, headers=DEFAULT_HEADERS)
    if resp.status_code != 200:
        return None
    jwt_match = JWT_RE.search(resp.text)
    return jwt_match.group(0) if jwt_match else None


def _paginate_remaining_playlist_tracks(
    client: httpx.Client,
    page_html: str,
    ref: AppleMusicRef,
    existing_tracks: list[TrackSpec],
    playlist_name: str | None,
    fallback_cover: str | None,
) -> tuple[list[TrackSpec], list[str]]:
    token = _extract_web_bearer_token(client, page_html)
    if not token:
        return existing_tracks, [
            f"Apple Music returned {len(existing_tracks)} tracks (could not locate catalog pagination token for remaining items)."
        ]

    headers = {
        **DEFAULT_HEADERS,
        "Authorization": f"Bearer {token}",
        "Origin": "https://music.apple.com",
        "Referer": "https://music.apple.com/",
    }
    seen_ids = {t.source_id for t in existing_tracks if t.source_id}
    combined = list(existing_tracks)
    next_path: str | None = (
        f"/v1/catalog/{ref.storefront}/playlists/{ref.item_id}/tracks?limit=100&offset={len(existing_tracks)}"
    )

    while next_path:
        url = next_path if next_path.startswith("http") else f"https://amp-api.music.apple.com{next_path}"
        resp = client.get(url, headers=headers)
        if resp.status_code != 200:
            break
        body = resp.json()
        for item in body.get("data") or []:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes") or {}
            title = _clean_text(attrs.get("name"))
            if not title:
                continue
            song_id = str(item.get("id") or "").strip() or None
            source_id = f"apple:song:{song_id}" if song_id else None
            if source_id and source_id in seen_ids:
                continue
            if source_id:
                seen_ids.add(source_id)
            dur_ms = attrs.get("durationInMillis")
            duration_sec = round(float(dur_ms) / 1000.0, 2) if isinstance(dur_ms, (int, float)) and dur_ms > 0 else None
            cover_url = format_apple_artwork_url(attrs.get("artwork")) or fallback_cover
            combined.append(
                TrackSpec(
                    title=title,
                    artist=_clean_text(attrs.get("artistName")),
                    album=_clean_text(attrs.get("albumName")),
                    duration_sec=duration_sec,
                    source_id=source_id,
                    cover_url=cover_url,
                    playlist_name=playlist_name,
                )
            )
        next_path = body.get("next")

    return combined, []


def fetch_apple_music_batch(raw_url: str, timeout_sec: float = 20.0) -> PlaylistBatch:
    """Resolve an Apple Music playlist, album, or song URL into a PlaylistBatch with zero credentials."""
    ref = parse_apple_music_ref(raw_url)
    if ref is None:
        raise AppleMusicResolutionError(f"Unsupported Apple Music URL: {raw_url}")

    with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
        resp = client.get(ref.canonical_url, headers=DEFAULT_HEADERS)
        if resp.status_code != 200:
            raise AppleMusicResolutionError(
                f"Apple Music request failed for {ref.kind}/{ref.item_id} (HTTP {resp.status_code})."
            )
        batch = parse_apple_ssr_html(resp.text, ref)

        if ref.kind == "playlist" and not ref.track_filter_id and len(batch.tracks) >= SSR_TRACK_CAP:
            fallback_cover = batch.tracks[0].cover_url if batch.tracks else None
            full_tracks, extra_warnings = _paginate_remaining_playlist_tracks(
                client,
                resp.text,
                ref,
                batch.tracks,
                playlist_name=batch.name,
                fallback_cover=fallback_cover,
            )
            return PlaylistBatch(
                name=batch.name,
                tracks=full_tracks,
                warnings=[*batch.warnings, *extra_warnings],
            )

        return batch
