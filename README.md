# lrcfetch

A CLI tool for fetching LRC lyrics on Linux. Automatically detects the currently playing track via MPRIS/DBus and retrieves synced (or plain with all time tags set to `[00:00.00]` if failed to find any synced) lyrics from multiple sources.

## Sources

Lyrics are fetched using a fallback pipeline (first synced result wins):

1. **Local** — sidecar `.lrc` files or embedded audio metadata (FLAC, MP3)
2. **Cache Search** — fuzzy cross-album lookup in local cache
3. **Spotify** — synced lyrics via Spotify's API (requires `SPOTIFY_SP_DC`)
4. **LRCLIB** — exact match from [lrclib.net](https://lrclib.net) (requires full metadata)
5. **LRCLIB Search** — fuzzy search from lrclib.net (requires at least a title)
6. **Netease** — Netease Cloud Music public API
7. **QQ Music** — QQ Music via self-hosted API proxy (requires `QQ_MUSIC_API_URL` that provides the same interface as [tooplick/qq-music-api](https://github.com/tooplick/qq-music-api))

## Usage

See `lrcfetch --help` for full command reference. Common use cases:

- Fetch lyrics for the currently playing track:

  ```bash
  lrcfetch fetch
  ```

  using a specific player or source to fetch from:

  ```bash
  lrcfetch --player mpd fetch --method lrclib-search
  ```

- Search by metadata (bypasses MPRIS):

  ```bash
  lrcfetch search -t "My Love" -a "Westlife"
  lrcfetch search --trackid "5p0ietGkLNEqx1Z7ijkw5g"
  ```

  or for a local file:

  ```bash
  lrcfetch search --path "/path/to/Westlife - My Love.flac"
  ```

- Export to sidecar `.lrc` file:

  ```bash
  lrcfetch export
  ```

  or to a custom path:

  ```bash
  lrcfetch export --output /path/to/lyrics.lrc
  ```

- Cache management:

  ```bash
  lrcfetch cache stats        # show cache statistics
  lrcfetch cache query        # query cache for current track
  lrcfetch cache clear        # clears cache of current track
  lrcfetch cache clear --all  # clears entire cache
  ```

## Configuration

Set credentials via environment variable or `.env` file:

- `~/.config/lrcfetch/.env` — user-level
- `.env` in working directory — project-local
- Shell environment — highest priority

```env
SPOTIFY_SP_DC=your_cookie_value
QQ_MUSIC_API_URL=https://api.example.com
LRCFETCH_PLAYER=spotify
```

- `SPOTIFY_SP_DC` — required for Spotify source. Defaults to empty (disabled Spotify source).
- `QQ_MUSIC_API_URL` — required for QQ Music source. Defaults to empty (disabled QQ Music source).
- `LRCFETCH_PLAYER` — preferred MPRIS player when multiple are active. Defaults to `spotify`. Only used when no `--player` flag is given and more than one player (or none of them) is currently playing.

Shell completion (zsh/fish/bash):

```bash
lrcfetch --install-completion
```

## Credits

- [lrclib.net](https://lrclib.net)
- [spotify-lyrics-api](https://github.com/akashrchandran/spotify-lyrics-api)
- [librelyrics-spotify](https://github.com/libre-lyrics/librelyrics-spotify)
- [NeteaseCloudMusicAPI](https://www.npmjs.com/package/NeteaseCloudMusicApi?activeTab=readme)
- [qq-music-api](https://github.com/tooplick/qq-music-api)
