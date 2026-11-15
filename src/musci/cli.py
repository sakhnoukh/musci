"""Typer + Rich command-line interface for musci."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Annotated, Optional

import typer
from dotenv import load_dotenv, set_key
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from musci.analyzer import analyze_audio_file
from musci.downloader import ManifestStore, download_batch, sanitize_filename
from musci.models import AnalysisResult, KeyFormat, TrackOutcome, TrackSpec, lookup_keys_from_tag
from musci.sources import resolve_inputs
from musci.sources.spotify import SPOTIFY_REDIRECT_URI, run_spotify_oauth_login
from musci.tagger import apply_filename_prefix, read_dj_tags, write_track_tags

app = typer.Typer(
    name="musci",
    help="Bulk Spotify, Apple Music & YouTube playlist 320kbps CBR MP3 downloader and DJ BPM/Camelot key analyzer.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _parse_bpm_range(bpm_range: str) -> tuple[float, float]:
    parts = bpm_range.strip().split("-")
    if len(parts) != 2:
        raise typer.BadParameter("BPM range must be formatted as '<min>-<max>', e.g. '70-180'.")
    try:
        low, high = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise typer.BadParameter("BPM range bounds must be numeric, e.g. '70-180'.") from exc
    if low <= 0 or high <= low:
        raise typer.BadParameter("BPM range must have 0 < min < max.")
    return low, high


def _validate_key_format(value: str) -> KeyFormat:
    lowered = value.strip().lower()
    if lowered not in {"camelot", "musical"}:
        raise typer.BadParameter("Key format must be 'camelot' (e.g. 8A) or 'musical' (e.g. Am).")
    return lowered  # type: ignore[return-value]


def _write_summary_csv(csv_path: Path, outcomes: list[TrackOutcome]) -> None:
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "title",
                "artist",
                "bpm",
                "camelot_key",
                "musical_key",
                "key_confidence",
                "status",
                "filename",
                "error",
            ]
        )
        for item in outcomes:
            writer.writerow(
                [
                    item.track.title,
                    item.track.artist,
                    item.analysis.bpm_rounded if item.analysis else "",
                    item.analysis.camelot_key if item.analysis else "",
                    item.analysis.musical_key if item.analysis else "",
                    item.analysis.key_confidence if item.analysis else "",
                    item.status,
                    item.file_path.name if item.file_path else "",
                    item.error or "",
                ]
            )


def _render_results_table(title: str, outcomes: list[TrackOutcome], show_analysis: bool) -> None:
    table = Table(title=title, show_lines=False)
    table.add_column("Track", style="cyan", overflow="fold")
    table.add_column("Status", style="bold")
    if show_analysis:
        table.add_column("BPM", justify="right", style="magenta")
        table.add_column("Camelot", justify="center", style="green")
        table.add_column("Key", justify="center", style="yellow")
    table.add_column("File / Detail", overflow="fold")

    for item in outcomes:
        status_label = {
            "downloaded": "[green]downloaded[/green]",
            "skipped": "[yellow]cached[/yellow]",
            "failed": "[red]failed[/red]",
        }[item.status]
        detail = item.file_path.name if item.file_path else (item.error or "-")
        if show_analysis:
            bpm_str = str(item.analysis.bpm_rounded) if item.analysis else "-"
            cam_str = item.analysis.camelot_key if item.analysis else "-"
            mus_str = item.analysis.musical_key if item.analysis else "-"
            table.add_row(item.track.display_name, status_label, bpm_str, cam_str, mus_str, detail)
        else:
            table.add_row(item.track.display_name, status_label, detail)

    console.print(table)


@app.command("download")
def download_cmd(
    inputs: Annotated[
        Optional[list[str]],
        typer.Argument(
            help="Spotify, Apple Music, or YouTube/SoundCloud/Bandcamp URLs, or 'Artist - Title' queries.",
        ),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option("-o", "--output-dir", help="Base directory for downloaded MP3s."),
    ] = Path("./downloads"),
    from_file: Annotated[
        Optional[Path],
        typer.Option("-f", "--from-file", help="Text file of URLs or 'Artist - Title' lines (one per line)."),
    ] = None,
    download_only: Annotated[
        bool,
        typer.Option("--download-only", help="Skip BPM & key detection; only download MP3s and write basic tags."),
    ] = False,
    workers: Annotated[
        int,
        typer.Option("-w", "--workers", min=1, max=16, help="Concurrent download workers."),
    ] = 4,
    prefix_filename: Annotated[
        bool,
        typer.Option("--prefix-filename", help="Rename files to '[BPM - Key] Artist - Title.mp3'."),
    ] = False,
    key_format: Annotated[
        str,
        typer.Option("--key-format", help="Tag/filename key notation: 'camelot' (8A) or 'musical' (Am)."),
    ] = "camelot",
    bpm_range: Annotated[
        str,
        typer.Option("--bpm-range", help="Target DJ BPM range '<min>-<max>' for half/double-time folding."),
    ] = "70-180",
    flat: Annotated[
        bool,
        typer.Option("--flat", help="Save all tracks directly in --output-dir without playlist subfolders."),
    ] = False,
    no_artwork: Annotated[
        bool,
        typer.Option("--no-artwork", help="Skip downloading and embedding cover art."),
    ] = False,
) -> None:
    """Bulk-download Spotify or YouTube playlists/tracks as 320kbps CBR MP3s with optional BPM & key tagging."""
    load_dotenv()
    url_list = inputs or []
    if not url_list and from_file is None:
        console.print("[red]Error:[/red] Provide at least one URL/query argument or --from-file.")
        raise typer.Exit(code=1)
    if from_file is not None and not from_file.exists():
        console.print(f"[red]Error:[/red] Input file not found: {from_file}")
        raise typer.Exit(code=1)

    min_bpm, max_bpm = _parse_bpm_range(bpm_range)
    validated_key_fmt = _validate_key_format(key_format)

    spotify_id = os.getenv("SPOTIFY_CLIENT_ID") or None
    spotify_secret = os.getenv("SPOTIFY_CLIENT_SECRET") or None
    spotify_refresh = os.getenv("SPOTIFY_REFRESH_TOKEN") or None

    with console.status("[bold cyan]Resolving playlist & track metadata..."):
        try:
            batches = resolve_inputs(
                url_list,
                from_file=from_file,
                spotify_client_id=spotify_id,
                spotify_client_secret=spotify_secret,
                spotify_refresh_token=spotify_refresh,
            )
        except Exception as exc:
            console.print(f"[red]Failed to resolve input sources:[/red] {exc}")
            raise typer.Exit(code=1) from exc

    total_tracks = sum(len(b.tracks) for b in batches)
    if total_tracks == 0:
        console.print("[yellow]No downloadable tracks found in the provided inputs.[/yellow]")
        raise typer.Exit(code=0)

    any_failures = False
    for batch in batches:
        for warn in batch.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warn}")
        if not batch.tracks:
            continue

        if batch.name and not flat:
            target_dir = output_dir / sanitize_filename(batch.name)
        else:
            target_dir = output_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        manifest = ManifestStore(target_dir)

        batch_label = batch.name or "Tracks"
        console.print(
            f"\n[bold]{batch_label}[/bold] — {len(batch.tracks)} track(s) -> [dim]{target_dir}[/dim]"
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            dl_task = progress.add_task("Downloading 320kbps MP3s", total=len(batch.tracks))
            outcomes = download_batch(
                batch.tracks,
                output_dir=target_dir,
                workers=workers,
                on_complete=lambda _: progress.advance(dl_task),
            )

            ready_outcomes = [o for o in outcomes if o.file_path is not None and o.file_path.exists()]
            if ready_outcomes:
                tag_desc = "Writing ID3 tags" if download_only else "Analyzing BPM & Camelot key"
                an_task = progress.add_task(tag_desc, total=len(ready_outcomes))
                for outcome in ready_outcomes:
                    assert outcome.file_path is not None
                    mp3_path = outcome.file_path
                    analysis: AnalysisResult | None = None

                    if not download_only:
                        existing_bpm, existing_key = read_dj_tags(mp3_path)
                        if outcome.status == "skipped" and existing_bpm and existing_key:
                            cam_k, mus_k = lookup_keys_from_tag(existing_key)
                            analysis = AnalysisResult(
                                bpm=float(existing_bpm),
                                bpm_rounded=existing_bpm,
                                camelot_key=cam_k,
                                musical_key=mus_k,
                                key_confidence=1.0,
                            )
                        else:
                            try:
                                analysis = analyze_audio_file(
                                    mp3_path,
                                    min_bpm=min_bpm,
                                    max_bpm=max_bpm,
                                )
                            except Exception as exc:
                                outcome.error = f"Analysis warning: {exc}"
                        outcome.analysis = analysis

                    if outcome.status == "downloaded" or analysis is not None:
                        write_track_tags(
                            mp3_path,
                            track=outcome.track if outcome.status == "downloaded" else None,
                            analysis=analysis,
                            key_format=validated_key_fmt,
                            embed_artwork=not no_artwork,
                        )

                    if prefix_filename and analysis is not None:
                        renamed = apply_filename_prefix(mp3_path, analysis, key_format=validated_key_fmt)
                        outcome.file_path = renamed
                        manifest.record(outcome.track.dedup_key, renamed.name)

                    progress.advance(an_task)

        summary_csv = target_dir / "playlist_summary.csv"
        _write_summary_csv(summary_csv, outcomes)
        _render_results_table(batch_label, outcomes, show_analysis=not download_only)

        downloaded_n = sum(1 for o in outcomes if o.status == "downloaded")
        skipped_n = sum(1 for o in outcomes if o.status == "skipped")
        failed_n = sum(1 for o in outcomes if o.status == "failed")
        if failed_n > 0:
            any_failures = True
        console.print(
            f"[bold]Done:[/bold] {downloaded_n} downloaded, {skipped_n} cached, {failed_n} failed. "
            f"Summary CSV: [dim]{summary_csv}[/dim]"
        )

    if any_failures:
        raise typer.Exit(code=2)


@app.command("analyze")
def analyze_cmd(
    target: Annotated[
        Path,
        typer.Argument(help="MP3 file or directory of MP3 files to analyze for BPM & key."),
    ],
    recursive: Annotated[
        bool,
        typer.Option("-r", "--recursive", help="Recursively scan subdirectories for .mp3 files."),
    ] = False,
    prefix_filename: Annotated[
        bool,
        typer.Option("--prefix-filename", help="Rename files to '[BPM - Key] Filename.mp3'."),
    ] = False,
    key_format: Annotated[
        str,
        typer.Option("--key-format", help="Tag/filename key notation: 'camelot' (8A) or 'musical' (Am)."),
    ] = "camelot",
    bpm_range: Annotated[
        str,
        typer.Option("--bpm-range", help="Target DJ BPM range '<min>-<max>' for half/double-time folding."),
    ] = "70-180",
) -> None:
    """Detect BPM & Camelot/musical key for existing MP3 files and write ID3v2.3 DJ tags."""
    if not target.exists():
        console.print(f"[red]Error:[/red] Path does not exist: {target}")
        raise typer.Exit(code=1)

    min_bpm, max_bpm = _parse_bpm_range(bpm_range)
    validated_key_fmt = _validate_key_format(key_format)

    if target.is_file():
        mp3_files = [target]
        summary_dir = target.parent
    else:
        pattern = "**/*.mp3" if recursive else "*.mp3"
        mp3_files = sorted(p for p in target.glob(pattern) if p.is_file() and not p.name.startswith("."))
        summary_dir = target

    if not mp3_files:
        console.print(f"[yellow]No .mp3 files found in {target}.[/yellow]")
        raise typer.Exit(code=0)

    outcomes: list[TrackOutcome] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Analyzing BPM & Camelot key", total=len(mp3_files))
        for mp3_path in mp3_files:
            spec = TrackSpec(title=mp3_path.stem)
            try:
                analysis = analyze_audio_file(mp3_path, min_bpm=min_bpm, max_bpm=max_bpm)
                write_track_tags(mp3_path, analysis=analysis, key_format=validated_key_fmt, embed_artwork=False)
                final_path = (
                    apply_filename_prefix(mp3_path, analysis, key_format=validated_key_fmt)
                    if prefix_filename
                    else mp3_path
                )
                outcomes.append(
                    TrackOutcome(
                        track=spec,
                        status="downloaded",
                        file_path=final_path,
                        analysis=analysis,
                    )
                )
            except Exception as exc:
                outcomes.append(TrackOutcome(track=spec, status="failed", file_path=mp3_path, error=str(exc)))
            progress.advance(task_id)

    summary_csv = summary_dir / "playlist_summary.csv"
    _write_summary_csv(summary_csv, outcomes)
    _render_results_table(f"Analysis — {target}", outcomes, show_analysis=True)
    console.print(f"Summary CSV written to [dim]{summary_csv}[/dim]")


@app.command("spotify-login")
def spotify_login_cmd(
    env_file: Annotated[
        Path,
        typer.Option("--env-file", help="Path to .env file containing SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET."),
    ] = Path(".env"),
) -> None:
    """Authenticate once in your browser to save SPOTIFY_REFRESH_TOKEN for 100+ track playlists."""
    load_dotenv(dotenv_path=env_file)
    client_id = os.getenv("SPOTIFY_CLIENT_ID") or ""
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET") or ""
    if not client_id or not client_secret:
        console.print(
            f"[red]Error:[/red] Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in {env_file} first.\n"
            f"Make sure Redirect URI [bold]{SPOTIFY_REDIRECT_URI}[/bold] is added in your Spotify App Settings."
        )
        raise typer.Exit(code=1)

    console.print(
        f"Opening Spotify authorization in your browser (listening on [cyan]{SPOTIFY_REDIRECT_URI}[/cyan])..."
    )
    try:
        refresh_token = run_spotify_oauth_login(client_id=client_id, client_secret=client_secret)
    except Exception as exc:
        console.print(f"[red]Spotify login failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    env_file.touch(mode=0o600, exist_ok=True)
    os.chmod(env_file, 0o600)
    set_key(str(env_file), "SPOTIFY_REFRESH_TOKEN", refresh_token, quote_mode="never")
    console.print(f"[green]Success![/green] Saved SPOTIFY_REFRESH_TOKEN to [bold]{env_file}[/bold].")


if __name__ == "__main__":
    app()
