"""3D neon MUSCi logo, 2D pixel-art cyber-retro DJ deck/EQ dashboard, and interactive shell."""

from __future__ import annotations

import math
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

# 6-row 3D isometric MUSCi block logo — lowercase 'i' has a distinct top dot + bevel gap on row 1
LOGO_LINES: tuple[str, ...] = (
    "███╗   ███╗ ██╗   ██╗ ███████╗  ██████╗ ██╗",
    "████╗ ████║ ██║   ██║ ██╔════╝ ██╔════╝ ╚═╝",
    "██╔████╔██║ ██║   ██║ ███████╗ ██║      ██╗",
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

# Palette for the 2D half-block square-pixel canvas
BG_DARK = "#070b14"
BG_GRID = "#0f172a"
PLATTER_RIM = "#1e293b"
PLATTER_TRACK = "#0d1322"
NEON_CYAN = "#00f5d4"
CYAN_BRIGHT = "#a5f3fc"
NEON_PINK = "#ff2e93"
PINK_BRIGHT = "#f9a8d4"
NEON_YELLOW = "#facc15"
NEON_LIME = "#ccff00"
NEON_GREEN = "#22c55e"
NEON_PURPLE = "#7c3aed"
CHROME_LIGHT = "#f1f5f9"
CHROME_MID = "#94a3b8"
CHROME_DARK = "#475569"
PEAK_WHITE = "#ffffff"

# 7-band EQ heights (0..10) + floating peak-hold heights across animation frames
EQ_FRAMES: tuple[tuple[int, ...], ...] = (
    (5, 8, 9, 6, 8, 7, 4),
    (7, 9, 7, 8, 6, 9, 6),
    (8, 6, 10, 9, 7, 5, 8),
    (6, 8, 8, 5, 9, 8, 5),
    (4, 10, 6, 7, 8, 6, 7),
    (7, 7, 9, 10, 5, 8, 6),
    (9, 5, 8, 7, 9, 7, 4),
    (6, 8, 7, 6, 7, 9, 5),
)

WAVE_FRAMES: tuple[str, ...] = (
    "▁▃▆█▆▃▁",
    "▃▆█▆▃▁▃",
    "▆█▆▃▁▃▆",
    "█▆▃▁▃▆█",
    "▆▃▁▃▆█▆",
    "▃▁▃▆█▆▃",
    "▁▃▆█▆▃▁",
    "▂▅▇▅▂▁▄",
)

SUB_WAVE_FRAMES: tuple[str, ...] = (
    "▁▂▄▆█▅▃▂▁▄▇▅▂",
    "▂▄▆█▅▃▂▁▄▇▅▂▁",
    "▄▆█▅▃▂▁▄▇▅▂▁▂",
    "▆█▅▃▂▁▄▇▅▂▁▂▄",
    "█▅▃▂▁▄▇▅▂▁▂▄▆",
    "▅▃▂▁▄▇▅▂▁▂▄▆█",
    "▃▂▁▄▇▅▂▁▂▄▆█▅",
    "▂▁▄▇▅▂▁▂▄▆█▅▃",
)


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


def render_3d_logo(phase: float = 0.0, step: int = 0) -> list[Text]:
    """Render the 10-row left hero column: cyber HUD header + 6-row 3D MUSCi logo + wave telemetry."""
    width = max(len(line) for line in LOGO_LINES)  # 43 cols

    top_hud = Text()
    top_hud.append("◈═╡ ", style="#475569")
    top_hud.append("HARMONIC CRATE ENGINE", style="bold #00f5d4")
    top_hud.append(" // ", style="#475569")
    top_hud.append("SYS:ONLINE", style="bold #ccff00" if step % 2 == 0 else "#22c55e")
    top_hud.append(" ╞═◈", style="#475569")

    logo_rows: list[Text] = []
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
        logo_rows.append(t)

    spacer = Text(" " * width)

    wave_str = SUB_WAVE_FRAMES[step % len(SUB_WAVE_FRAMES)]
    wave_hud = Text()
    wave_hud.append("▸ ", style="bold #ff2e93")
    wave_hud.append("CAMELOT 1A-12B ", style="#94a3b8")
    wave_hud.append(wave_str, style="bold #00f5d4")
    wave_hud.append("    ID3v2.3 ", style="#94a3b8")
    wave_hud.append("◂", style="bold #ccff00")

    core_label = f"AUDIO CORE v{__version__}"
    pad_total = max(2, width - len(core_label) - 6)
    left_eq = pad_total // 2
    right_eq = pad_total - left_eq
    bot_hud = Text(f"╚{'═' * left_eq}╡ ", style="#334155")
    bot_hud.append(core_label, style="bold #a855f7")
    bot_hud.append(f" ╞{'═' * right_eq}╝", style="#334155")

    return [top_hud, *logo_rows, spacer, wave_hud, bot_hud]


def _pack_pixel_grid(grid: list[list[str]]) -> list[Text]:
    """Convert an even-height 2D color grid (H x W) into H//2 half-block Text rows."""
    rows: list[Text] = []
    for y in range(0, len(grid), 2):
        top_row = grid[y]
        bot_row = grid[y + 1]
        line = Text()
        for fg, bg in zip(top_row, bot_row):
            if fg == bg:
                line.append("█", style=fg)
            else:
                line.append("▀", style=f"{fg} on {bg}")
        rows.append(line)
    return rows


def _build_deck_pixel_canvas(step: int) -> list[Text]:
    """Build 22w x 12h 2D pixel-art turntable (pink/cyan grooves + tonearm + sliders) -> 6 Text rows."""
    w, h = 22, 12
    grid = [[BG_GRID if (x + y) % 4 == 0 else BG_DARK for x in range(w)] for y in range(h)]

    # 1. Circular glowing pixel turntable / jog wheel on cols 1..13, rows 0..11
    cx, cy = 6.5, 5.5
    spin_angle = step * (math.pi / 4.0)
    for y in range(h):
        for x in range(14):
            dx = x - cx
            dy = y - cy
            dist = math.hypot(dx, dy)
            theta = math.atan2(dy, dx)

            if dist < 0.95:
                grid[y][x] = PEAK_WHITE
            elif dist < 1.85:
                grid[y][x] = NEON_PURPLE
            elif dist < 2.85:
                # Inner circular vinyl groove in neon pink with rotating sheen
                shimmer = math.sin(2.0 * theta - spin_angle) > 0.55
                grid[y][x] = PINK_BRIGHT if shimmer else NEON_PINK
            elif dist < 3.65:
                grid[y][x] = PLATTER_TRACK
            elif dist < 4.65:
                # Outer circular vinyl groove in neon cyan with rotating sheen
                shimmer = math.cos(2.0 * theta + spin_angle) > 0.55
                grid[y][x] = CYAN_BRIGHT if shimmer else NEON_CYAN
            elif dist <= 5.55:
                # Outer jog wheel bezel with rotating strobe dots
                strobe_idx = int(round(((theta + math.pi) / (2.0 * math.pi)) * 12 + step)) % 3
                grid[y][x] = NEON_YELLOW if strobe_idx == 0 else PLATTER_RIM

    # 2. Metallic tonearm assembly + headshell + gold stylus riding the groove
    for px, py, col in (
        (13, 0, CHROME_DARK),
        (14, 0, CHROME_LIGHT),
        (15, 0, NEON_PINK if step % 2 == 0 else NEON_CYAN),  # Servo indicator pixel
        (13, 1, CHROME_MID),
        (14, 1, CHROME_LIGHT),
        (15, 1, CHROME_DARK),
        (14, 2, CHROME_LIGHT),
        (14, 3, CHROME_MID),
        (14, 4, CHROME_LIGHT),
        (14, 5, CHROME_MID),
        (13, 6, CHROME_LIGHT),
        (12, 7, CHROME_LIGHT),
        (11, 8, CHROME_MID),
        (10, 8, NEON_YELLOW),  # Gold cartridge stylus directly on the vinyl groove
    ):
        grid[py][px] = col

    # 3. Digital pitch slider (3px crossbar cap on x=17..19) + vertical LED VU meter (x=21)
    fader_y = 4 + ((step // 2) % 4)
    for y in range(1, 11):
        grid[y][18] = "#334155" if y % 2 == 0 else "#1e293b"
    grid[fader_y][17] = NEON_CYAN
    grid[fader_y][18] = PEAK_WHITE
    grid[fader_y][19] = NEON_CYAN

    vu_level = 6 + (step % 4)
    for y in range(1, 11):
        lit = (11 - y) <= vu_level
        if not lit:
            grid[y][21] = "#1e293b"
        elif y <= 3:
            grid[y][21] = NEON_PINK
        elif y <= 6:
            grid[y][21] = NEON_YELLOW
        else:
            grid[y][21] = NEON_LIME

    return _pack_pixel_grid(grid)


def _bar_pixel_color(py: int) -> str:
    """Multi-colored arcade EQ spectrum gradient from top (y=0) to bottom (y=11)."""
    if py <= 3:
        return NEON_PINK
    if py <= 6:
        return NEON_CYAN
    if py <= 8:
        return NEON_YELLOW
    return NEON_GREEN


def _build_eq_pixel_canvas(step: int) -> list[Text]:
    """Build 22w x 12h 2D pixel-art 7-band multi-color spectrum analyzer with peak-hold caps -> 6 Text rows."""
    w, h = 22, 12
    grid = [[BG_GRID if (x % 3 == 0 and y % 2 == 0) else BG_DARK for x in range(w)] for y in range(h)]

    heights = EQ_FRAMES[step % len(EQ_FRAMES)]
    prev_heights = EQ_FRAMES[(step - 1) % len(EQ_FRAMES)]

    # Floor rail across y = 11
    for x in range(1, w - 1):
        grid[11][x] = "#1e293b"

    # 7 crisp 2px-wide bars with 1px dark gaps -> spans cols 1..20 (7*3 - 1 = 20 cols)
    for band_idx, bar_h in enumerate(heights):
        x0 = 1 + band_idx * 3
        clamped_h = max(1, min(9, bar_h))
        top_y = 11 - clamped_h

        for py in range(top_y, 11):
            col = _bar_pixel_color(py)
            grid[py][x0] = col
            grid[py][x0 + 1] = col

        # Floating peak-hold indicator with a crisp dark pixel gap above bar top
        peak_h = min(11, max(clamped_h + 2, prev_heights[band_idx] + 2))
        peak_y = max(0, 11 - peak_h)
        if peak_y < top_y:
            peak_col = PEAK_WHITE if band_idx % 2 == 0 else PINK_BRIGHT
            grid[peak_y][x0] = peak_col
            grid[peak_y][x0 + 1] = peak_col

    return _pack_pixel_grid(grid)


def render_dj_sprite(step: int = 0) -> list[Text]:
    """Render the 10-row (49-col) pixel-bordered cyber-retro DJ turntable + EQ dashboard."""
    frame = "#6d28d9"
    corner = "bold #ff2e93"
    cyan_hi = "bold #00f5d4"
    lime_hi = "bold #ccff00"

    deck_canvas = _build_deck_pixel_canvas(step)  # 6 rows x 22 cols
    eq_canvas = _build_eq_pixel_canvas(step)      # 6 rows x 22 cols
    wave = WAVE_FRAMES[step % len(WAVE_FRAMES)]   # 7 cols

    # Row 0: Pixel-bordered top bezel (47 cols: 1 + 22 + 1 + 22 + 1)
    r0 = Text()
    r0.append("▛", style=corner)
    r0.append("▀▀╡", style=frame)
    r0.append("●" if step % 2 == 0 else "▪", style="bold #ff2e93")
    r0.append(" DECK-01 // JOG", style=cyan_hi)
    r0.append("╞▀▀", style=frame)
    r0.append("╦", style=corner)
    r0.append("▀▀╡", style=frame)
    r0.append("◈", style=lime_hi)
    r0.append(" SPECTRUM // EQ", style="bold #facc15")
    r0.append("╞▀▀", style=frame)
    r0.append("▜", style=corner)

    # Row 1: Flashing deck LEDs + slider HUD (22 cols) | Rotary knobs + wave oscilloscope (22 cols)
    r1 = Text()
    r1.append("▌", style=frame)
    r1.append(" ●", style=f"bold #00f5d4 on {BG_DARK}")
    r1.append("LCK ", style=f"#94a3b8 on {BG_DARK}")
    r1.append("●", style=f"bold #facc15 on {BG_DARK}" if step % 3 != 0 else f"#475569 on {BG_DARK}")
    r1.append("CUE ", style=f"#94a3b8 on {BG_DARK}")
    r1.append("●", style=f"bold #ff2e93 on {BG_DARK}" if step % 2 == 0 else f"#4c0519 on {BG_DARK}")
    r1.append("REC    ", style=f"#94a3b8 on {BG_DARK}")
    r1.append("PIT ", style=f"bold #a855f7 on {BG_DARK}")
    r1.append("┃", style=frame)
    r1.append(" ▪", style=f"bold #ff2e93 on {BG_DARK}")
    r1.append("HI ", style=f"#94a3b8 on {BG_DARK}")
    r1.append("▪", style=f"bold #00f5d4 on {BG_DARK}")
    r1.append("MD ", style=f"#94a3b8 on {BG_DARK}")
    r1.append("▪", style=f"bold #ccff00 on {BG_DARK}")
    r1.append("LO ", style=f"#94a3b8 on {BG_DARK}")
    r1.append(wave, style=f"bold #00f5d4 on {BG_DARK}")
    r1.append(" ", style=f"on {BG_DARK}")
    r1.append("▐", style=frame)

    # Rows 2..7: 2D square-pixel canvases (Turntable + Tonearm + Sliders | 7-Band Peak-Hold EQ)
    mid_rows: list[Text] = []
    for d_row, e_row in zip(deck_canvas, eq_canvas):
        row = Text()
        row.append("▌", style=frame)
        row.append_text(d_row)
        row.append("┃", style=frame)
        row.append_text(e_row)
        row.append("▐", style=frame)
        mid_rows.append(row)

    # Row 8: Digital [128 BPM] cyan readout + [320K] yellow-green readout
    r8 = Text()
    r8.append("▌", style=frame)
    r8.append(" ▸PLAY ", style=f"bold #22c55e on {BG_DARK}" if step % 2 == 0 else f"#16a34a on {BG_DARK}")
    r8.append("[128 BPM]", style="bold #00f5d4 on #082f49")
    r8.append(" +0%  ", style=f"#38bdf8 on {BG_DARK}")
    r8.append("┃", style=frame)
    r8.append(" STEREO ", style=f"bold #ff2e93 on {BG_DARK}")
    r8.append("[320K]", style="bold #ccff00 on #1a2e05")
    r8.append("  44.1K ", style=f"#a3e635 on {BG_DARK}")
    r8.append("▐", style=frame)

    # Row 9: Pixel-bordered bottom bezel (47 cols: 1 + 22 + 1 + 22 + 1)
    r9 = Text()
    r9.append("▙", style=corner)
    r9.append("▄▄╡", style=frame)
    r9.append("QUARTZ // LOCK", style="#64748b")
    r9.append("╞▄▄▄▄", style=frame)
    r9.append("╩", style=corner)
    r9.append("▄▄▄╡", style=frame)
    r9.append("PEAK // HOLD", style="#64748b")
    r9.append("╞▄▄▄▄▄", style=frame)
    r9.append("▟", style=corner)

    return [r0, r1, *mid_rows, r8, r9]


def build_hero_banner(step: int = 0, terminal_width: int = 110) -> Table:
    """Combine the 3D MUSCi block logo and the 2D pixel-art cyber-retro dashboard."""
    logo_rows = render_3d_logo(phase=step * 0.08, step=step)
    sprite_rows = render_dj_sprite(step=step)

    if terminal_width < 94:
        stacked = Table.grid(padding=(0, 0))
        stacked.add_column(no_wrap=True)
        for l_row in logo_rows:
            stacked.add_row(l_row)
        stacked.add_row(Text(""))
        for s_row in sprite_rows:
            stacked.add_row(s_row)
        return stacked

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
    """Display the animated 3D MUSCi logo + pixel-art DJ dashboard followed by the welcome panel."""
    term_w = console.size.width
    console.print()
    if animate and console.is_terminal:
        frames = 14
        with Live(
            build_hero_banner(0, terminal_width=term_w),
            console=console,
            refresh_per_second=20,
            transient=False,
        ) as live:
            for step in range(1, frames):
                time.sleep(0.055)
                live.update(build_hero_banner(step, terminal_width=term_w))
    else:
        console.print(build_hero_banner(0, terminal_width=term_w))

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
