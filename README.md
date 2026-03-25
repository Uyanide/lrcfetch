# lrcfetch

A CLI tool for fetching LRC lyrics on Linux. Automatically detects the currently playing track via MPRIS/DBus and retrieves synced or plain lyrics from multiple sources.

## Sources

Lyrics are fetched using a fallback pipeline (first synced result wins):

1. **Local** — sidecar `.lrc` files or embedded audio metadata (FLAC, MP3)
2. **Spotify** — synced lyrics via Spotify's API (requires `SPOTIFY_SP_DC`)
3. **LRCLIB** — exact match from [lrclib.net](https://lrclib.net) (requires full metadata)
4. **LRCLIB Search** — fuzzy search from lrclib.net (requires at least a title)
5. **Netease** — Netease Cloud Music public API

## Usage

```bash
# Fetch lyrics for the currently playing track
lrcfetch fetch

# Search by metadata (bypasses MPRIS)
lrcfetch search -t "Song Title" -a "Artist"

# Export to .lrc file
lrcfetch export

# Force a specific source
lrcfetch fetch --method spotify

# Cache management
lrcfetch cache --stats
lrcfetch cache --query
lrcfetch cache --clear
```

## Configuration

Set `SPOTIFY_SP_DC` via environment variable or `.env` file:

- `~/.config/lrcfetch/.env` — user-level
- `.env` in working directory — project-local
- Shell environment — highest priority

```env
SPOTIFY_SP_DC=your_cookie_value
```
