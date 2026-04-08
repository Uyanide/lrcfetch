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
   (requires `SPOTIFY_SP_DC` and Spotify trackid)
4. **LRCLIB** — exact match from [lrclib.net](https://lrclib.net)
   (requires full metadata)
5. **Musixmatch (Spotify)** — Musixmatch API with Spotify trackid
   (requires Spotify trackid)
6. **LRCLIB Search** — fuzzy search from lrclib.net (requires at least a title)
7. **Musixmatch** — Musixmatch API with metadata search (requires at least a title)
8. **Netease** — Netease Cloud Music public API
9. **QQ Music** — QQ Music via self-hosted API proxy
   (requires `QQ_MUSIC_API_URL` that provides the same interface as [tooplick/qq-music-api](https://github.com/tooplick/qq-music-api))

> I'm aware that Spotify's lyrics are provided by Musixmatch, but the fact is
> that Musixmatch's own search will yield different (and more) results than
> Spotify's, so I treat them as separate sources.

## Usage

See `lrx --help` for full command reference. Common use cases:

- Fetch lyrics for the currently playing track:

  ```bash
  lrx fetch
  ```

  targeting a specific player and a source to fetch from:

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

- Cache management:

  ```bash
  lrx cache stats                    # statistics
  lrx cache query                    # inspect cache entries for current track
  lrx cache clear                    # clear cache of current track
  lrx cache clear --all              # clear entire cache
  lrx cache confidence spotify 100   # manually set confidence for a source
  ```

## Configuration

Set credentials via environment variable or `.env` file:

- `~/.config/lrx/.env` — user-level
- `.env` in working directory — project-local
- Shell environment — highest priority

```env
SPOTIFY_SP_DC=your_cookie_value
MUSIXMATCH_USERTOKEN=your_musixmatch_usertoken
QQ_MUSIC_API_URL=https://api.example.com
PREFERRED_PLAYER=spotify
```

- `SPOTIFY_SP_DC` — required for Spotify source. Defaults to empty
  (disabled Spotify source).
- `MUSIXMATCH_USERTOKEN` — optional for Musixmatch sources
  ([Curators Settings Page](https://curators.musixmatch.com/settings)
  -> Login (if required)
  -> "Copy debug info").
  If not set, an anonymous token will be fetched at runtime.
- `QQ_MUSIC_API_URL` — required for QQ Music source. Defaults to empty
  (disabled QQ Music source).
- `PREFERRED_PLAYER` — preferred MPRIS player when multiple are active.
  Defaults to `spotify`. Only used when no `--player` flag is given and more
  than one player (or none of them) is currently playing.

Shell completion (zsh/fish/bash):

```bash
lrx --install-completion
```

## Development

Clone this repository:

```bash
git clone https://github.com/Uyanide/LRX-CLI.git
cd LRX-CLI
```

Create a virtual environment and install dependencies (for example, using uv):

```bash
uv venv .venv
uv sync
```

Run tests without network calls

```bash
uv run pytest -m "not network"
```

or full tests:

```bash
uv run pytest
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
