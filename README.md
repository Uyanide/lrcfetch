# LRX-CLI

> [!WARNING]
>
> This project is primarily provided for educational and experimental purposes.
> It is yet not ready for production or commercial use and may violate the terms
> of service (ToS) of third‑party music platforms. Use of this software is at
> your own risk; the authors provide no warranties and accept no liability for any
> consequences arising from its use.

A CLI tool for fetching LRC lyrics on Linux. Automatically detects the currently
playing track via MPRIS/DBus and retrieves the best-matching lyrics from
multiple sources, ranked by confidence scoring.

## Sources

Sources are queried in order. High-confidence results (exact match or manual
insert) terminate the pipeline early; otherwise all sources are tried and the
highest-confidence result wins.

1. **Local** — sidecar `.lrc` files or embedded audio metadata (FLAC, MP3)
2. **Cache Search** — fuzzy cross-album lookup in local cache
3. **Spotify** — synced lyrics via Spotify's API
   (requires `credentials.spotify_sp_dc` and Spotify trackid)
4. **LRCLIB** — exact match from [lrclib.net](https://lrclib.net)
   (requires full metadata)
5. **Musixmatch (Spotify)** — Musixmatch API with Spotify trackid
   (requires Spotify trackid)
6. **LRCLIB Search** — fuzzy search from lrclib.net (requires at least a title)
7. **Musixmatch** — Musixmatch API with metadata search (requires at least a title)
8. **Netease** — Netease Cloud Music public API
9. **QQ Music** — QQ Music via self-hosted API proxy
   (requires `credentials.qq_music_api_url`; compatible with [tooplick/qq-music-api](https://github.com/tooplick/qq-music-api))

> I'm aware that Spotify's lyrics are provided by Musixmatch, but the fact is
> that Musixmatch's own search will yield different (and more) results than
> Spotify's, so I treat them as separate sources.

## Usage

See `lrx --help` for full command reference. Common use cases:

- Fetch lyrics for the currently playing track:

  ```bash
  lrx fetch
  ```

  targeting a specific player and source:

  ```bash
  lrx fetch --player mpd --method lrclib-search
  ```

- Search by metadata (bypasses MPRIS):

  ```bash
  lrx search -t "My Love" -a "Westlife"
  lrx search --trackid "5p0ietGkLNEqx1Z7ijkw5g"
  ```

  or by path to a local audio file:

  ```bash
  lrx search --path "/path/to/Westlife - My Love.flac"
  ```

- Export to sidecar `.lrc` file (or `.txt` with `--plain`):

  ```bash
  lrx export
  lrx export --plain
  lrx export --output /path/to/lyrics.lrc
  ```

- Watch active player and stream lyrics continuously to stdout:

  ```bash
  lrx watch pipe
  lrx watch pipe --before 1 --after 2   # show context lines
  ```

  Control a running watch session:

  ```bash
  lrx watch ctl status                  # print session status as JSON
  lrx watch ctl offset +200             # shift lyrics forward 200 ms
  lrx watch ctl offset -150
  ```

- Cache management:

  ```bash
  lrx cache stats                       # statistics
  lrx cache query                       # inspect cache entries for current track
  lrx cache clear                       # clear cache of current track
  lrx cache clear --all                 # clear entire cache
  lrx cache confidence spotify 100      # manually set confidence for a source
  ```

Shell completion (zsh/fish/bash):

```bash
lrx --install-completion
```

## Configuration

Configuration is read from `~/.config/lrx-cli/config.toml`. The file is
optional; all values have defaults. Unknown keys are rejected with an error.

```toml
[general]
preferred_player  = ""              # preferred MPRIS player when multiple are active
player_blacklist  = ["firefox", "zen", "chrome", "chromium", "vivaldi", "edge", "opera", "mpv"]  # bypassed by --player/-p
http_timeout      = 10.0            # seconds

[credentials]
spotify_sp_dc         = ""          # required for Spotify source
musixmatch_usertoken  = ""          # optional; anonymous token fetched if empty
qq_music_api_url      = ""          # required for QQ Music source

[watch]
debounce_ms             = 400       # ms to wait after a track change before fetching
calibration_interval_s  = 3.0       # seconds between full MPRIS position recalibrations
position_tick_ms        = 50        # ms between local position ticks
socket_path             = ""        # Unix socket path; defaults to <cache_dir>/watch.sock
```

**Credentials:**

- `spotify_sp_dc` — `SP_DC` cookie from a logged-in Spotify web session. Required
  for the Spotify source; leave empty to disable it.
- `musixmatch_usertoken` — found at
  [Curators Settings Page](https://curators.musixmatch.com/settings) → Login → "Copy debug info".
  If empty, an anonymous token will be fetched at runtime, which could be more likely to
  hit the rate limits.
- `qq_music_api_url` — base URL of a self-hosted
  [qq-music-api](https://github.com/tooplick/qq-music-api) (compatible) instance. Required
  for the QQ Music source; leave empty to disable it.

## Development

Clone this repository:

```bash
git clone https://github.com/Uyanide/lrx-cli.git
cd lrx-cli
```

Create a virtual environment and install dependencies (for example, using uv):

```bash
uv venv .venv
uv sync
```

Run tests (without network access):

```bash
uv run poe test
```

Run tests including **REAL EXTERNAL** API calls. Some of them will be skipped
if the required credentials are not configured as [above](#configuration). This might be useful
to verify whether the lyric sources are still valid and working as expected:

```bash
uv run poe test-api
```

Other unified tasks:

```bash
uv run poe fmt      # ruff format
uv run poe lint     # ruff check + pyright
```

Run the CLI:

```bash
uv run lrx --help
```

Install to user-level (optional):

```bash
uv tool install .
```

## Credits

- [lrclib.net](https://lrclib.net)
- [spotify-lyrics-api](https://github.com/akashrchandran/spotify-lyrics-api)
- [librelyrics-spotify](https://github.com/libre-lyrics/librelyrics-spotify)
- [NeteaseCloudMusicAPI](https://www.npmjs.com/package/NeteaseCloudMusicApi?activeTab=readme)
- [qq-music-api](https://github.com/tooplick/qq-music-api)
- [LyricsMPRIS-Rust](https://github.com/BEST8OY/LyricsMPRIS-Rust)
- [onetagger](https://github.com/Marekkon5/onetagger)
- [Rise Media Player](https://github.com/theimpactfulcompany/Rise-Media-Player)
