# musci

I wanted to start fucking around with mixing music, and couldn't be asked to download mp3s myself from youtube, so i thought i'd do something about it. 
Could extend this to be like an npm package, but i still don't know if Spotify and Apple apis will let me do that. 

Bulk playlist MP3 downloader and DJ mixing prep CLI. Supports **Spotify playlists/albums/tracks**, **Apple Music playlists/albums/songs**, **YouTube playlists/videos**, SoundCloud/Bandcamp links, and text tracklists.

Downloads audio concurrently as **320kbps CBR MP3s** (constant bitrate to prevent beatgrid drift in Rekordbox, Serato, Traktor, and CDJs) with **BPM + Camelot Wheel key detection** and **ID3v2.3** tagging.

## Install (No Code / `.venv` Needed)

Install `ffmpeg` + `pipx` once on macOS (Homebrew), then install `musci` globally:

```bash
brew install ffmpeg pipx
pipx ensurepath
pipx install git+https://github.com/sakhnoukh/musci.git
```
*(Once published to PyPI, this becomes `pipx install musci`.)*

## Launch the Interactive Terminal UI

Just run:

```bash
musci
```

`musci` boots with an animated **3D neon logo + spinning DJ turntable/EQ sprite**, system readiness indicators, a command cheat sheet, and an interactive `musci ▸` prompt where you can simply **paste any playlist URL** and hit Enter (or run commands directly from your shell below).

### Direct CLI Commands

#### 1. Download a Spotify, Apple Music, or YouTube playlist (with BPM & Camelot key detection)

```bash
musci download "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
musci download "https://music.apple.com/us/playlist/..."
musci download "https://www.youtube.com/playlist?list=PL..."
```

### 2. Fast download-only mode (skip BPM & key analysis)

```bash
musci download "https://open.spotify.com/playlist/..." --download-only
```

### 3. Prefix filenames with `[BPM - Camelot]` for quick folder browsing

```bash
musci download "https://open.spotify.com/playlist/..." --prefix-filename
# Produces e.g.: [128 - 8A] Artist - Track Title.mp3
```

### 4. Batch download from a text file of playlist URLs or `Artist - Title` lines

```bash
musci download --from-file playlists.txt --workers 6 -o ./downloads
```

### 5. Analyze an existing folder of MP3s for BPM & Camelot key

```bash
musci analyze ./downloads/my-playlist --prefix-filename
```

## Large Spotify Playlists (>100 tracks)

Out of the box, `musci` reads public Spotify playlists with **zero configuration** (up to ~100 tracks per playlist via Spotify's public embed metadata).

For playlists with **100+ tracks** (using your Spotify Premium account):

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and click **Create app**:
   - **Redirect URI**: `http://127.0.0.1:8888/callback`
   - Check **Web API** and click **Save**.
2. Run the guided setup wizard (prompts for your Client ID & Secret, stores them securely in `~/.config/musci/.env`, and opens browser login automatically):
   ```bash
   musci spotify-setup
   ```
4. **If the 100+ track playlist was created by someone else**: Under Spotify's 2026 Developer Mode rules, the API only returns full 100+ item lists for playlists you own or collaborate on. In Spotify Desktop, open the playlist, click `...` -> **Add to other playlist** -> **New playlist** (takes 2 seconds), and pass your new playlist's link to `musci download`.

## Notes for DJ Mixing

- **320kbps CBR MP3**: All downloads are encoded using `libmp3lame` at 320kbps constant bitrate with ID3v2.3 tags (`TBPM`, `TKEY`, `TIT2`, `TPE1`, `TALB`, cover art) for reliable beatgrids across Rekordbox, Serato, VirtualDJ, Traktor, and Mixxx.
- **Source Audio Quality**: YouTube native audio streams top out around 128–160kbps Opus/AAC. While 320kbps CBR MP3 packaging works well for bedroom mixing, set prep, and casual gigs, large club sound systems benefit from lossless/DJ-pool masters.
- **Incremental Sync**: Each output folder stores `.musci-manifest.json` so re-running `musci download` on an updated playlist skips tracks you already downloaded.
