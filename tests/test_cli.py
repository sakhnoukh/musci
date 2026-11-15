"""CLI integration tests for musci download and musci analyze."""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from rich.cells import cell_len
from typer.testing import CliRunner

from musci.analyzer import SAMPLE_RATE
from musci.cli import app
from musci.models import PlaylistBatch, TrackOutcome, TrackSpec
from musci.ui import parse_repl_line, render_3d_logo, render_dj_sprite

runner = CliRunner()


def _create_test_mp3(dest_mp3: Path, bpm: float = 126.0) -> None:
    sr = SAMPLE_RATE
    dur = 12.0
    t = np.linspace(0.0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    # E minor triad (E3=164.81, G3=196.00, B3=246.94 -> Camelot 9A)
    pad = 0.15 * (
        np.sin(2.0 * np.pi * 164.81 * t)
        + 0.8 * np.sin(2.0 * np.pi * 196.00 * t)
        + 0.8 * np.sin(2.0 * np.pi * 246.94 * t)
    )
    pulses = np.zeros_like(t)
    beat_time = 0.2
    while beat_time < dur - 0.1:
        idx = int(beat_time * sr)
        length = min(int(0.04 * sr), len(t) - idx)
        seg = np.arange(length, dtype=np.float32) / sr
        pulses[idx : idx + length] += 0.7 * np.exp(-seg * 70.0) * np.sin(2.0 * np.pi * 80.0 * seg)
        beat_time += 60.0 / bpm

    wav_tmp = dest_mp3.with_suffix(".wav")
    sf.write(str(wav_tmp), np.clip(pad + pulses, -0.95, 0.95), sr)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(wav_tmp),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-ar",
            "44100",
            "-b:a",
            "320k",
            str(dest_mp3),
        ],
        check=True,
    )
    wav_tmp.unlink()


def test_cli_analyze_writes_summary_and_prefixes_files(tmp_path: Path) -> None:
    mp3_file = tmp_path / "Artist - Deep Groove.mp3"
    _create_test_mp3(mp3_file, bpm=126.0)

    result = runner.invoke(app, ["analyze", str(tmp_path), "--prefix-filename"])
    assert result.exit_code == 0, result.stdout

    summary_csv = tmp_path / "playlist_summary.csv"
    assert summary_csv.exists()
    rows = list(csv.DictReader(summary_csv.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["camelot_key"] == "9A"
    assert abs(int(rows[0]["bpm"]) - 126) <= 1
    assert rows[0]["filename"].startswith("[")
    assert (tmp_path / rows[0]["filename"]).exists()


def test_cli_download_only_skips_analysis(tmp_path: Path, monkeypatch) -> None:
    fake_track = TrackSpec(title="Track One", artist="DJ Test", source_id="spotify:track:111")

    def fake_resolve(*args, **kwargs):
        return [PlaylistBatch(name="My Set", tracks=[fake_track], warnings=[])]

    def fake_download_batch(tracks, output_dir: Path, workers: int = 4, on_complete=None):
        out_mp3 = output_dir / "DJ Test - Track One.mp3"
        _create_test_mp3(out_mp3, bpm=128.0)
        outcome = TrackOutcome(track=tracks[0], status="downloaded", file_path=out_mp3)
        if on_complete:
            on_complete(outcome)
        return [outcome]

    monkeypatch.setattr("musci.cli.resolve_inputs", fake_resolve)
    monkeypatch.setattr("musci.cli.download_batch", fake_download_batch)

    result = runner.invoke(
        app,
        ["download", "https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n", "-o", str(tmp_path), "--download-only"],
    )
    assert result.exit_code == 0, result.stdout
    csv_path = tmp_path / "My Set" / "playlist_summary.csv"
    assert csv_path.exists()
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["bpm"] == ""
    assert rows[0]["camelot_key"] == ""
    assert rows[0]["status"] == "downloaded"


def test_splash_screen_and_3d_sprite_alignment() -> None:
    for step in range(6):
        logo_widths = {cell_len(r.plain) for r in render_3d_logo(step * 0.1)}
        sprite_widths = {cell_len(r.plain) for r in render_dj_sprite(step)}
        assert logo_widths == {43}
        assert sprite_widths == {22}

    res = runner.invoke(app, ["--splash"])
    assert res.exit_code == 0, res.stdout
    assert "Welcome to musci" in res.stdout
    assert "[128 BPM]" in res.stdout
    assert "[320K]" in res.stdout


def test_parse_repl_line_auto_routes_urls_and_commands() -> None:
    assert parse_repl_line("   ") is None
    assert parse_repl_line("exit") == "__EXIT__"
    assert parse_repl_line("clear") == "__CLEAR__"
    assert parse_repl_line("help") == "__HELP__"
    assert parse_repl_line("https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n") == [
        "download",
        "https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n",
    ]
    assert parse_repl_line('musci download "https://music.apple.com/us/playlist/pl.123" --download-only') == [
        "download",
        "https://music.apple.com/us/playlist/pl.123",
        "--download-only",
    ]
    assert parse_repl_line("analyze ./downloads") == ["analyze", "./downloads"]
