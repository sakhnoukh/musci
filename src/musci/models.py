"""Core domain models and Camelot Wheel harmonic mixing lookup tables."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

KeyFormat = Literal["camelot", "musical"]

PITCH_NAMES_SHARP: tuple[str, ...] = (
    "C",
    "C#",
    "D",
    "Eb",
    "E",
    "F",
    "F#",
    "G",
    "Ab",
    "A",
    "Bb",
    "B",
)

# Maps (pitch_class 0..11, mode "minor"|"major") -> (Camelot code, DJ-friendly musical key name)
CAMELOT_MAP: dict[tuple[int, str], tuple[str, str]] = {
    # Minor keys (A)
    (8, "minor"): ("1A", "Abm"),
    (3, "minor"): ("2A", "Ebm"),
    (10, "minor"): ("3A", "Bbm"),
    (5, "minor"): ("4A", "Fm"),
    (0, "minor"): ("5A", "Cm"),
    (7, "minor"): ("6A", "Gm"),
    (2, "minor"): ("7A", "Dm"),
    (9, "minor"): ("8A", "Am"),
    (4, "minor"): ("9A", "Em"),
    (11, "minor"): ("10A", "Bm"),
    (6, "minor"): ("11A", "F#m"),
    (1, "minor"): ("12A", "Dbm"),
    # Major keys (B)
    (11, "major"): ("1B", "B"),
    (6, "major"): ("2B", "F#"),
    (1, "major"): ("3B", "Db"),
    (8, "major"): ("4B", "Ab"),
    (3, "major"): ("5B", "Eb"),
    (10, "major"): ("6B", "Bb"),
    (5, "major"): ("7B", "F"),
    (0, "major"): ("8B", "C"),
    (7, "major"): ("9B", "G"),
    (2, "major"): ("10B", "D"),
    (9, "major"): ("11B", "A"),
    (4, "major"): ("12B", "E"),
}


def lookup_keys_from_tag(tag_value: str) -> tuple[str, str]:
    """Map an existing TKEY tag (either Camelot like '9B' or musical like 'G') to (camelot, musical)."""
    cleaned = tag_value.strip()
    for camelot, musical in CAMELOT_MAP.values():
        if cleaned.upper() == camelot.upper() or cleaned.lower() == musical.lower():
            return camelot, musical
    return cleaned, cleaned


@dataclass(frozen=True)
class TrackSpec:
    """Normalized specification of a track to download."""

    title: str
    artist: str = ""
    album: str = ""
    duration_sec: float | None = None
    source_id: str | None = None
    direct_url: str | None = None
    cover_url: str | None = None
    playlist_name: str | None = None

    @property
    def display_name(self) -> str:
        if self.artist:
            return f"{self.artist} - {self.title}"
        return self.title

    @property
    def dedup_key(self) -> str:
        if self.source_id:
            return self.source_id
        if self.direct_url:
            return self.direct_url
        return f"query:{self.artist.lower().strip()}::{self.title.lower().strip()}"


@dataclass(frozen=True)
class PlaylistBatch:
    """Resolved collection of tracks from a single input source."""

    name: str | None
    tracks: list[TrackSpec]
    warnings: list[str]


@dataclass(frozen=True)
class AnalysisResult:
    """BPM and musical/Camelot key analysis result for an audio file."""

    bpm: float
    bpm_rounded: int
    camelot_key: str
    musical_key: str
    key_confidence: float


@dataclass
class TrackOutcome:
    """Final pipeline result for a single track."""

    track: TrackSpec
    status: Literal["downloaded", "skipped", "failed"]
    file_path: Path | None = None
    analysis: AnalysisResult | None = None
    error: str | None = None
