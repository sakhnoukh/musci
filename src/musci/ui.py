"""3D neon block logo, animated DJ turntable/EQ sprite, command guide, and interactive shell."""

from __future__ import annotations

import os
import shlex
import shutil
import time
from typing import Callable

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from musci import __version__

LOGO_LINES: tuple[str, ...] = (
    "███╗   ███╗ ██╗   ██╗ ███████╗  ██████╗ ██╗",
    "████╗ ████║ ██║   ██║ ██╔════╝ ██╔════╝ ██║",
    "██╔████╔██║ ██║   ██║ ███████╗ ██║      ██║",
    "██║╚██╔╝██║ ██║   ██║ ╚════██║ ██║      ██║",
    "██║ ╚═╝ ██║ ╚██████╔╝ ███████║ ╚██████╗ ██║",
    "╚═╝     ╚═╝  ╚═════╝  ╚══════╝  ╚═════╝ ╚═╝",
)

BEVEL_CHARS = set("╔╗╚╝═║")

GRADIENT_STOPS: tuple[tuple[int, int, int], ...] = (
    (204, 255, 0),    # Acid lime
    (0, 245, 212),    # Cyber cyan
    (56, 189, 248),   # Electric sky
    (168, 85, 247),   # Neon violet
    (255, 46, 147),   # Club magenta
)

PLATTER_SPIN: tuple[tuple[str, str], ...] = (
    ("╱", "╲"),
    ("─", "─"),
    ("╲", "╱"),
    ("│", "│"),
)

EQ_PATTERNS: tuple[tuple[int, int, int, int, int, int], ...] = (
    (3, 6, 8, 5, 7, 4),
    (5, 8, 6, 7, 4, 6),
    (7, 5, 9, 8, 6, 3),
    (4, 7, 7, 4, 8, 5),
    (6, 9, 5, 6, 5, 7),
    (8, 6, 8, 9, 7, 4),
)

SUB_BLOCKS = " ▁▂▃▄▅▆▇█"


def _lerp_rgb(
    c1: tuple[int, int, int],
    c2: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _sample_gradient(pos: float) -> str:
    wrapped = pos % 1.0
    scaled = wrapped * (len(GRADIENT_STOPS) - 1)
    idx = min(int(scaled), len(GRADIENT_STOPS) - 2)
    frac = scaled - idx
    r, g, b = _lerp_rgb(GRADIENT_STOPS[idx], GRADIENT_STOPS[idx + 1], frac)
    return f"#{r:02x}{g:02x}{b:02x}"


def render_3d_logo(phase: float = 0.0) -> list[Text]:
    """Render the 6-row 3D extruded MUSCI block logo with a smooth neon gradient."""
    rows: list[Text] = []
    width = max(len(line) for line in LOGO_LINES)
    for row_idx, raw_line in enumerate(LOGO_LINES):
        t = Text()
        for col_idx, ch in enumerate(raw_line):
            if ch == " ":
                t.append(" ")
            elif ch == "█":
                pos = (col_idx / max(1, width)) * 0.85 + row_idx * 0.03 - phase
                color = _sample_gradient(pos)
                t.append(ch, style=f"bold {color}")
            elif ch in BEVEL_CHARS:
                t.append(ch, style="bold #6d28d9")
            else:
                t.append(ch, style="#94a3b8")
        rows.append(t)
    return rows


def _eq_cell(height_0_to_9: int, row_tier: int) -> tuple[str, str]:
    """Map bar height (0..9) across 3 vertical rows (row_tier 2=top, 1=mid, 0=bottom)."""
    local = max(0, min(3, height_0_to_9 - row_tier * 3))
    if local == 0:
        return " ", "dim"
    ch = SUB_BLOCKS[min(8, local * 3 - 1)]
    if row_tier == 2:
        return ch, "bold #ff2e93"
    if row_tier == 1:
        return ch, "bold #00f5d4"
    return ch, "bold #ccff00"


def render_dj_sprite(step: int = 0) -> list[Text]:
    """Render the 6-row animated DJ turntable platter + 6-band spectrum analyzer sprite."""
    s_top, s_bot = PLATTER_SPIN[step % len(PLATTER_SPIN)]
    eq = EQ_PATTERNS[step % len(EQ_PATTERNS)]

    border = "#475569"
    vinyl = "bold #e2e8f0"
    accent = "bold #ccff00"
    arm = "#38bdf8"

    def _build_eq_row(tier: int) -> Text:
        seg = Text()
        for val in eq:
            ch, style = _eq_cell(val, tier)
            seg.append(ch, style=style)
        return seg

    r0 = Text()
    r0.append("╭───────────┬────────╮", style=border)

    r1 = Text()
    r1.append("│", style=border)
    r1.append("  ╭─────╮ ", style="#64748b")
    r1.append("╲", style=arm)
    r1.append("│ ", style=border)
    r1.append_text(_build_eq_row(2))
    r1.append(" │", style=border)

    r2 = Text()
    r2.append("│", style=border)
    r2.append(" ╭│  ", style=vinyl)
    r2.append(s_top, style=accent)
    r2.append("  │╮", style=vinyl)
    r2.append("╲", style=arm)
    r2.append("│ ", style=border)
    r2.append_text(_build_eq_row(1))
    r2.append(" │", style=border)

    r3 = Text()
    r3.append("│", style=border)
    r3.append(" ╰│  ", style=vinyl)
    r3.append("●", style="bold #ff2e93")
    r3.append("  │╯", style=vinyl)
    r3.append("▪", style=arm)
    r3.append("│ ", style=border)
    r3.append_text(_build_eq_row(0))
    r3.append(" │", style=border)

    r4 = Text()
    r4.append("│", style=border)
    r4.append("  ╰─", style="#64748b")
    r4.append(s_bot, style=accent)
    r4.append("───╯ ", style="#64748b")
    r4.append("▮", style="bold #00f5d4")
    r4.append("│ ", style=border)
    r4.append("▀▀▀▀▀▀", style="#334155")
    r4.append(" │", style=border)

    r5 = Text()
    r5.append("╰─", style=border)
    r5.append("[128 BPM]", style="bold #00f5d4")
    r5.append("─┴─", style=border)
    r5.append("[320K]", style="bold #ccff00")
    r5.append("─╯", style=border)

    return [r0, r1, r2, r3, r4, r5]


def build_hero_banner(step: int = 0) -> Table:
    """Combine the 3D MUSCI block logo and the DJ turntable/EQ sprite side-by-side."""
    logo_rows = render_3d_logo(phase=step * 0.08)
    sprite_rows = render_dj_sprite(step=step)

    grid = Table.grid(padding=(0, 3))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    for l_row, s_row in zip(logo_rows, sprite_rows):
        grid.add_row(l_row, s_row)
    return grid


def build_status_line() -> Text:
    """Build a compact system readiness badge strip."""
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    spotify_linked = bool(os.getenv("SPOTIFY_REFRESH_TOKEN"))

    status = Text()
    status.append(f" v{__version__} ", style="bold black on #ccff00")
    status.append("  ")
    status.append("● ", style="bold #22c55e" if ffmpeg_ok else "bold #ef4444")
    status.append("FFmpeg 320k CBR" if ffmpeg_ok else "FFmpeg missing (brew install ffmpeg)", style="dim")
    status.append("   ")
    status.append("● ", style="bold #22c55e")
    status.append("Apple Music & YouTube", style="dim")
    status.append("   ")
    status.append("● ", style="bold #22c55e" if spotify_linked else "bold #eab308")
    status.append("Spotify 100+ Linked" if spotify_linked else "Spotify (<=100 zero-setup)", style="dim")
    return status


def build_welcome_panel() -> Panel:
    """Build the introduction and quick-command reference card."""
    intro = Text()
    intro.append(
        "Bulk DJ crate builder — downloads playlists & tracks from ",
        style="#e2e8f0",
    )
    intro.append("Spotify", style="bold #22c55e")
    intro.append(", ", style="#e2e8f0")
    intro.append("Apple Music", style="bold #fb7185")
    intro.append(", ", style="#e2e8f0")
    intro.append("YouTube", style="bold #38bdf8")
    intro.append(" & ", style="#e2e8f0")
    intro.append("SoundCloud", style="bold #fb923c")
    intro.append(
        " as 44.1kHz 320kbps CBR MP3s with BPM & Camelot Wheel harmonic tags.\n",
        style="#e2e8f0",
    )

    cmds = Table.grid(padding=(0, 2))
    cmds.add_column(style="bold #00f5d4", no_wrap=True)
    cmds.add_column(style="#cbd5e1")

    cmds.add_row("<paste any URL>", "Download a Spotify, Apple Music, YouTube, or SoundCloud link")
    cmds.add_row("download <url> --download-only", "Fast MP3 download without BPM & Camelot key analysis")
    cmds.add_row("download <url> --prefix-filename", "Save files as '[128 - 8A] Artist - Title.mp3'")
    cmds.add_row("download <url> --bpm-range 105-145", "Lock BPM octave-folding to house/techno tempo")
    cmds.add_row("analyze <folder>", "Detect BPM & Camelot key on an existing folder of MP3s")
    cmds.add_row("spotify-setup", "Guided setup for 100+ track Spotify playlists")
    cmds.add_row("help  |  clear  |  exit", "Show all CLI flags, clear screen, or quit")

    body = Group(
        intro,
        build_status_line(),
        Text(""),
        cmds,
    )
    return Panel(
        body,
        title="[bold #ccff00]Welcome to musci[/bold #ccff00]",
        subtitle="[dim]Paste a playlist URL or type a command below[/dim]",
        border_style="#334155",
        padding=(1, 2),
    )


def show_startup_screen(console: Console, animate: bool = True) -> None:
    """Display the animated 3D logo + DJ turntable sprite followed by the welcome panel."""
    console.print()
    if animate and console.is_terminal:
        frames = 12
        with Live(build_hero_banner(0), console=console, refresh_per_second=20, transient=False) as live:
            for step in range(1, frames):
                time.sleep(0.055)
                live.update(build_hero_banner(step))
    else:
        console.print(build_hero_banner(0))

    console.print()
    console.print(build_welcome_panel())


def parse_repl_line(raw_line: str) -> list[str] | str | None:
    """Parse an interactive prompt line into CLI argv tokens or a special action sentinel."""
    stripped = raw_line.strip()
    if not stripped:
        return None

    lowered = stripped.lower()
    if lowered in {"exit", "quit", "q", ":q"}:
        return "__EXIT__"
    if lowered in {"clear", "cls"}:
        return "__CLEAR__"
    if lowered in {"help", "?", "-h", "--help"}:
        return "__HELP__"

    try:
        tokens = shlex.split(stripped)
    except ValueError:
        tokens = stripped.split()

    if not tokens:
        return None

    # Gracefully strip leading 'musci' if user pasted a full README command
    if tokens[0].lower() == "musci":
        tokens = tokens[1:]
        if not tokens:
            return "__HELP__"

    first = tokens[0]
    if first.startswith(("http://", "https://", "spotify:")):
        return ["download", *tokens]

    return tokens


def run_interactive_shell(
    console: Console,
    dispatch_argv: Callable[[list[str]], None],
    reload_env: Callable[[], None],
    animate: bool = True,
) -> None:
    """Launch the startup splash + interactive 'musci ▸' command loop."""
    try:
        import readline  # noqa: F401
    except ImportError:
        pass

    reload_env()
    show_startup_screen(console, animate=animate)

    while True:
        try:
            console.print()
            raw = console.input("[bold #ccff00]musci[/bold #ccff00] [bold #00f5d4]▸[/bold #00f5d4] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Closing crate. Happy mixing![/dim]")
            break

        parsed = parse_repl_line(raw)
        if parsed is None:
            continue
        if parsed == "__EXIT__":
            console.print("[dim]Closing crate. Happy mixing![/dim]")
            break
        if parsed == "__CLEAR__":
            console.clear()
            reload_env()
            show_startup_screen(console, animate=False)
            continue
        if parsed == "__HELP__":
            dispatch_argv(["download", "--help"])
            continue

        assert isinstance(parsed, list)
        dispatch_argv(parsed)
        reload_env()
