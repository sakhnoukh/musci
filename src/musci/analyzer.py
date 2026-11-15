"""Audio BPM and Camelot Wheel key detection using librosa."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import librosa
from mutagen import File as MutagenFile
import numpy as np
import soundfile as sf

from musci.models import CAMELOT_MAP, AnalysisResult

SAMPLE_RATE = 22050
MAX_ANALYSIS_WINDOW_SEC = 90.0

# Krumhansl-Kessler pitch-class key profiles (C, C#, D, ..., B)
KRUMHANSL_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
    dtype=np.float64,
)
KRUMHANSL_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17],
    dtype=np.float64,
)


def fold_bpm_into_range(bpm: float, min_bpm: float = 70.0, max_bpm: float = 180.0) -> float:
    """Fold half-time or double-time tempo estimates into [min_bpm, max_bpm]."""
    if bpm <= 0 or min_bpm <= 0 or max_bpm <= min_bpm:
        return max(0.0, bpm)
    adjusted = float(bpm)
    while adjusted < min_bpm:
        adjusted *= 2.0
    while adjusted > max_bpm:
        adjusted /= 2.0
    return round(adjusted, 2)


def detect_bpm_from_samples(
    y: np.ndarray,
    sr: int = SAMPLE_RATE,
    min_bpm: float = 70.0,
    max_bpm: float = 180.0,
) -> float:
    """Estimate BPM from a mono waveform with sub-BPM beat-interval refinement."""
    hop_length = 256
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    start_bpm = max(min_bpm, min(max_bpm, 124.0))
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        start_bpm=start_bpm,
    )
    raw_bpm = float(np.asarray(tempo).flat[0])

    # Refine integer-frame lag quantization using tracked inter-beat intervals
    if len(beat_frames) >= 5:
        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
        ibis = np.diff(beat_times)
        median_ibi = float(np.median(ibis))
        if median_ibi > 0:
            inliers = ibis[np.abs(ibis - median_ibi) <= 0.12 * median_ibi]
            if len(inliers) >= 3:
                raw_bpm = 60.0 / float(np.mean(inliers))

    if raw_bpm <= 0.0:
        fallback = librosa.feature.rhythm.tempo(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=hop_length,
            start_bpm=start_bpm,
        )
        raw_bpm = float(np.asarray(fallback).flat[0])
    return fold_bpm_into_range(raw_bpm, min_bpm=min_bpm, max_bpm=max_bpm)


def _pearson_corr(a: np.ndarray, b: np.ndarray) -> float:
    a_centered = a - np.mean(a)
    b_centered = b - np.mean(b)
    denom = float(np.linalg.norm(a_centered) * np.linalg.norm(b_centered))
    if denom < 1e-12:
        return 0.0
    return float(np.dot(a_centered, b_centered) / denom)


def detect_key_from_samples(y: np.ndarray, sr: int = SAMPLE_RATE) -> tuple[str, str, float]:
    """Detect (camelot_key, musical_key, confidence) using spectrogram-domain HPSS chromagram."""
    stft_mag = np.abs(librosa.stft(y=y, n_fft=4096, hop_length=2048))
    harm_mag, _ = librosa.decompose.hpss(stft_mag, kernel_size=15, margin=2.0)
    chroma = librosa.feature.chroma_stft(S=harm_mag**2, sr=sr, n_fft=4096)
    chroma_vec = np.mean(chroma, axis=1)

    if float(np.max(chroma_vec)) < 1e-8:
        camelot, musical = CAMELOT_MAP[(9, "minor")]
        return camelot, musical, 0.0

    best_corr = -2.0
    best_key_pair = (9, "minor")

    for pitch_class in range(12):
        maj_corr = _pearson_corr(chroma_vec, np.roll(KRUMHANSL_MAJOR, pitch_class))
        if maj_corr > best_corr:
            best_corr = maj_corr
            best_key_pair = (pitch_class, "major")

        min_corr = _pearson_corr(chroma_vec, np.roll(KRUMHANSL_MINOR, pitch_class))
        if min_corr > best_corr:
            best_corr = min_corr
            best_key_pair = (pitch_class, "minor")

    camelot_key, musical_key = CAMELOT_MAP[best_key_pair]
    confidence = round(max(0.0, min(1.0, best_corr)), 3)
    return camelot_key, musical_key, confidence


def _probe_duration_sec(audio_path: Path) -> float:
    meta = MutagenFile(str(audio_path))
    if meta is not None and getattr(meta, "info", None) is not None and getattr(meta.info, "length", 0):
        return float(meta.info.length)
    try:
        return float(sf.info(str(audio_path)).duration)
    except Exception:
        return float(librosa.get_duration(path=str(audio_path)))


def _choose_analysis_window(audio_path: Path, max_window_sec: float = MAX_ANALYSIS_WINDOW_SEC) -> tuple[float, float | None]:
    total_sec = _probe_duration_sec(audio_path)
    if total_sec <= max_window_sec:
        return 0.0, None
    # Sample from the middle of the track to avoid beatless DJ intros/outros
    offset = max(0.0, (total_sec - max_window_sec) / 2.0)
    return round(offset, 2), max_window_sec


def _load_audio_window(audio_path: Path, offset: float, duration: float | None) -> tuple[np.ndarray, int]:
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        cmd = [ffmpeg_bin, "-v", "error"]
        if offset > 0:
            cmd.extend(["-ss", str(offset)])
        if duration is not None:
            cmd.extend(["-t", str(duration)])
        cmd.extend(
            [
                "-i",
                str(audio_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "f32le",
                "pipe:1",
            ]
        )
        proc = subprocess.run(cmd, capture_output=True, check=False)
        if proc.returncode == 0 and proc.stdout:
            samples = np.frombuffer(proc.stdout, dtype=np.float32).copy()
            return samples, SAMPLE_RATE

    y, sr = librosa.load(
        str(audio_path),
        sr=SAMPLE_RATE,
        mono=True,
        offset=offset,
        duration=duration,
    )
    return y, int(sr)


def analyze_audio_file(
    audio_path: Path,
    min_bpm: float = 70.0,
    max_bpm: float = 180.0,
    max_window_sec: float = MAX_ANALYSIS_WINDOW_SEC,
) -> AnalysisResult:
    """Load representative window of an audio file and compute BPM & Camelot/musical key."""
    offset, duration = _choose_analysis_window(audio_path, max_window_sec=max_window_sec)
    y, sr = _load_audio_window(audio_path, offset=offset, duration=duration)
    if y.size < SAMPLE_RATE or float(np.max(np.abs(y))) < 1e-6:
        raise ValueError(f"Audio file '{audio_path.name}' is too short or silent to analyze.")

    bpm = detect_bpm_from_samples(y, sr=sr, min_bpm=min_bpm, max_bpm=max_bpm)
    camelot_key, musical_key, confidence = detect_key_from_samples(y, sr=sr)
    return AnalysisResult(
        bpm=bpm,
        bpm_rounded=int(round(bpm)),
        camelot_key=camelot_key,
        musical_key=musical_key,
        key_confidence=confidence,
    )
