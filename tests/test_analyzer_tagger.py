"""Tests for BPM/Camelot key detection, 320kbps MP3 encoding, ID3v2.3 DJ tags, and deduplication."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from mutagen.id3 import ID3

from musci.analyzer import SAMPLE_RATE, analyze_audio_file, detect_key_from_samples, fold_bpm_into_range
from musci.downloader import ManifestStore, download_single_track, sanitize_filename
from musci.models import TrackSpec
from musci.tagger import apply_filename_prefix, read_dj_tags, write_track_tags


def _synthesize_dj_test_signal(
    bpm: float,
    chord_freqs: list[float],
    duration_sec: float = 16.0,
    sr: int = SAMPLE_RATE,
) -> np.ndarray:
    """Synthesize a harmonic pad chord with periodic kick/click transients at exact BPM."""
    t = np.linspace(0.0, duration_sec, int(sr * duration_sec), endpoint=False, dtype=np.float32)

    # Harmonic chord with 2nd harmonic overtones
    pad = np.zeros_like(t)
    for idx, freq in enumerate(chord_freqs):
        weight = 1.0 if idx == 0 else 0.75
        pad += weight * np.sin(2.0 * np.pi * freq * t)
        pad += 0.3 * weight * np.sin(2.0 * np.pi * (2.0 * freq) * t)
    pad *= 0.2 / max(1, len(chord_freqs))

    # Crisp percussive pulse every beat
    pulses = np.zeros_like(t)
    beat_interval = 60.0 / bpm
    rng = np.random.default_rng(42)
    beat_time = 0.25
    while beat_time < duration_sec - 0.1:
        start = int(beat_time * sr)
        pulse_len = int(0.045 * sr)
        end = min(len(t), start + pulse_len)
        seg_t = np.arange(end - start, dtype=np.float32) / sr
        env = np.exp(-seg_t * 65.0)
        kick = np.sin(2.0 * np.pi * (110.0 * np.exp(-seg_t * 25.0) + 50.0) * seg_t)
        click = 0.35 * rng.standard_normal(end - start).astype(np.float32) * np.exp(-seg_t * 180.0)
        pulses[start:end] += 0.75 * env * kick + click
        beat_time += beat_interval

    mix = np.clip(pad + pulses, -0.98, 0.98)
    return mix


def _encode_cbr320_mp3(wav_path: Path, mp3_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(wav_path),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-ar",
            "44100",
            "-b:a",
            "320k",
            str(mp3_path),
        ],
        check=True,
    )


def test_fold_bpm_into_range() -> None:
    assert fold_bpm_into_range(64.0, 70.0, 180.0) == 128.0
    assert fold_bpm_into_range(256.0, 70.0, 180.0) == 128.0
    assert fold_bpm_into_range(128.0, 70.0, 180.0) == 128.0


def test_detect_key_c_major_is_8b() -> None:
    # C major scale weighted triad + 5th/octave (C4=261.63, E4=329.63, G4=392.00, D4=293.66, F4=349.23)
    y = _synthesize_dj_test_signal(
        bpm=124.0,
        chord_freqs=[261.63, 329.63, 392.00, 523.25],
        duration_sec=8.0,
    )
    camelot, musical, confidence = detect_key_from_samples(y, sr=SAMPLE_RATE)
    assert camelot == "8B"
    assert musical == "C"
    assert confidence > 0.5


def test_full_mp3_analysis_id3v23_tagging_and_prefix_idempotency(tmp_path: Path) -> None:
    # A minor triad: A3=220.00, C4=261.63, E4=329.63, A4=440.00 -> Camelot 8A (Am) at 128 BPM
    y = _synthesize_dj_test_signal(
        bpm=128.0,
        chord_freqs=[220.00, 261.63, 329.63, 440.00],
        duration_sec=18.0,
    )
    wav_path = tmp_path / "synth.wav"
    mp3_path = tmp_path / "Test Artist - Club Tool.mp3"
    sf.write(str(wav_path), y, SAMPLE_RATE)
    _encode_cbr320_mp3(wav_path, mp3_path)

    # Verify CBR 320k encoding via ffprobe
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,bit_rate",
            "-of",
            "json",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream_info = json.loads(probe.stdout)["streams"][0]
    assert stream_info["codec_name"] == "mp3"
    assert int(stream_info["bit_rate"]) == 320000

    # Verify BPM & Camelot key detection on the encoded MP3
    analysis = analyze_audio_file(mp3_path, min_bpm=70.0, max_bpm=180.0)
    assert abs(analysis.bpm_rounded - 128) <= 1
    assert analysis.camelot_key == "8A"
    assert analysis.musical_key == "Am"

    # Write ID3v2.3 tags and verify persistence
    spec = TrackSpec(title="Club Tool", artist="Test Artist", album="Warehouse EP")
    write_track_tags(mp3_path, track=spec, analysis=analysis, key_format="camelot", embed_artwork=False)

    raw_id3 = ID3(str(mp3_path))
    assert raw_id3.version == (2, 3, 0)
    assert str(raw_id3["TIT2"].text[0]) == "Club Tool"
    assert str(raw_id3["TPE1"].text[0]) == "Test Artist"
    assert str(raw_id3["TALB"].text[0]) == "Warehouse EP"

    read_bpm, read_key = read_dj_tags(mp3_path)
    assert read_bpm == analysis.bpm_rounded
    assert read_key == "8A"

    # Verify idempotent filename prefixing
    prefixed_once = apply_filename_prefix(mp3_path, analysis, key_format="camelot")
    assert prefixed_once.name == f"[{analysis.bpm_rounded} - 8A] Test Artist - Club Tool.mp3"
    prefixed_twice = apply_filename_prefix(prefixed_once, analysis, key_format="musical")
    assert prefixed_twice.name == f"[{analysis.bpm_rounded} - Am] Test Artist - Club Tool.mp3"


def test_manifest_deduplication_skips_already_downloaded_track(tmp_path: Path) -> None:
    existing_mp3 = tmp_path / "[128 - 8A] Bicep - Glue.mp3"
    existing_mp3.write_bytes(b"ID3-mock-non-empty-mp3-payload")

    manifest = ManifestStore(tmp_path)
    lock = threading.Lock()
    spec = TrackSpec(title="Glue", artist="Bicep", source_id="spotify:track:2aJDlirz6v2a4HREki98cP")

    outcome = download_single_track(spec, tmp_path, manifest, lock)
    assert outcome.status == "skipped"
    assert outcome.file_path == existing_mp3
    assert sanitize_filename('AC/DC: "Back*In?Black"  ') == "AC DC Back In Black"
