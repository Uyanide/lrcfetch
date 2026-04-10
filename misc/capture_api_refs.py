from __future__ import annotations

import argparse
import asyncio
import json
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from lrx_cli.authenticators import create_authenticators
from lrx_cli.cache import CacheEngine
from lrx_cli.config import AppConfig, load_config
from lrx_cli.fetchers import (
    create_fetchers,
    LrclibFetcher,
    LrclibSearchFetcher,
    NeteaseFetcher,
    SpotifyFetcher,
    QQMusicFetcher,
    MusixmatchFetcher,
    MusixmatchSpotifyFetcher,
)
from lrx_cli.models import TrackMeta


SAMPLE_TRACK = TrackMeta(
    title="One Last Kiss",
    artist="Hikaru Utada",
    album="One Last Kiss",
    length=252026,
    trackid="5RhWszHMSKzb7KiXk4Ae0M",
    url="https://open.spotify.com/track/5RhWszHMSKzb7KiXk4Ae0M",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.hex()
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return repr(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _clear_output_files(out_dir: Path) -> None:
    for pattern in ("*.json", "*.db"):
        for path in out_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def _new_runtime(config: AppConfig, db_path: Path):
    cache = CacheEngine(str(db_path))
    authenticators = create_authenticators(cache, config)
    fetchers = create_fetchers(cache, authenticators, config)
    return fetchers, authenticators


async def _response_dump(resp: httpx.Response) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "url": str(resp.request.url),
        "method": resp.request.method,
    }
    try:
        out["json"] = resp.json()
    except Exception:
        out["text"] = resp.text
    return out


def _decode_body(content: bytes) -> str:
    if not content:
        return ""
    try:
        return content.decode("utf-8")
    except Exception:
        return content.hex()


def _dump_request(req: httpx.Request) -> dict[str, Any]:
    query_params = {k: v for k, v in req.url.params.multi_items()}
    return {
        "method": req.method,
        "url": str(req.url),
        "headers": dict(req.headers),
        "query_params": query_params,
        "body": _decode_body(req.content),
    }


async def run_capture(out_dir: Path, timeout: float, strict: bool) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_output_files(out_dir)

    # Use isolated cache DBs to avoid polluting normal runtime cache.
    anon_fetchers, _ = _new_runtime(AppConfig(), out_dir / ".capture-anon.db")
    cred_fetchers, _ = _new_runtime(load_config(), out_dir / ".capture-cred.db")

    calls: list[tuple[str, dict[str, Any], Callable[[], Awaitable[Any]]]] = []

    captured_requests: list[dict[str, Any]] = []
    original_send = httpx.AsyncClient.send

    async def _patched_send(
        self: httpx.AsyncClient,
        request: httpx.Request,
        *args: Any,
        **kwargs: Any,
    ) -> httpx.Response:
        captured_requests.append(_dump_request(request))
        return await original_send(self, request, *args, **kwargs)

    httpx.AsyncClient.send = _patched_send  # type: ignore[method-assign]

    async with httpx.AsyncClient(timeout=timeout) as client:
        # LRCLIB
        lrclib = anon_fetchers["lrclib"]
        assert isinstance(lrclib, LrclibFetcher)
        calls.append(
            (
                "lrclib_get",
                {"track": asdict(SAMPLE_TRACK)},
                lambda: lrclib._api_get(client, SAMPLE_TRACK),
            )
        )

        lrclib_search = anon_fetchers["lrclib-search"]
        assert isinstance(lrclib_search, LrclibSearchFetcher)
        calls.append(
            (
                "lrclib_search_candidates",
                {"track": asdict(SAMPLE_TRACK)},
                lambda: lrclib_search._api_candidates(client, SAMPLE_TRACK),
            )
        )

        # Netease
        netease = anon_fetchers["netease"]
        assert isinstance(netease, NeteaseFetcher)
        calls.append(
            (
                "netease_search_track",
                {"track": asdict(SAMPLE_TRACK), "limit": 5},
                lambda: netease._api_search_track(client, SAMPLE_TRACK, 5),
            )
        )
        calls.append(
            (
                "netease_lyric_track",
                {"track": asdict(SAMPLE_TRACK), "limit": 5},
                lambda: netease._api_lyric_track(client, SAMPLE_TRACK, 5),
            )
        )

        # Spotify (credentialed runtime)
        spotify = cred_fetchers["spotify"]
        assert isinstance(spotify, SpotifyFetcher)
        calls.append(
            (
                "spotify_lyrics",
                {"track": asdict(SAMPLE_TRACK)},
                lambda: spotify._api_lyrics(SAMPLE_TRACK),
            )
        )

        # QQMusic (credentialed runtime)
        qq = cred_fetchers["qqmusic"]
        assert isinstance(qq, QQMusicFetcher)
        calls.append(
            (
                "qqmusic_search_track",
                {"track": asdict(SAMPLE_TRACK), "limit": 10},
                lambda: qq._api_search(SAMPLE_TRACK, 10),
            )
        )
        calls.append(
            (
                "qqmusic_lyric_track",
                {"track": asdict(SAMPLE_TRACK), "limit": 10},
                lambda: qq._api_lyric_track(SAMPLE_TRACK, 10),
            )
        )

        # Musixmatch anonymous
        mxm_anon = anon_fetchers["musixmatch"]
        mxm_sp_anon = anon_fetchers["musixmatch-spotify"]
        assert isinstance(mxm_anon, MusixmatchFetcher)
        assert isinstance(mxm_sp_anon, MusixmatchSpotifyFetcher)
        calls.append(
            (
                "musixmatch_anonymous_search_track",
                {"track": asdict(SAMPLE_TRACK)},
                lambda: mxm_anon._api_search_track(SAMPLE_TRACK),
            )
        )
        calls.append(
            (
                "musixmatch_anonymous_macro_track",
                {"track": asdict(SAMPLE_TRACK)},
                lambda: mxm_anon._api_macro_track(SAMPLE_TRACK),
            )
        )
        calls.append(
            (
                "musixmatch_spotify_anonymous_macro_track",
                {"track": asdict(SAMPLE_TRACK)},
                lambda: mxm_sp_anon._api_macro_track(SAMPLE_TRACK),
            )
        )

        # Musixmatch credentialed (if token configured, this uses it)
        mxm_cred = cred_fetchers["musixmatch"]
        mxm_sp_cred = cred_fetchers["musixmatch-spotify"]
        assert isinstance(mxm_cred, MusixmatchFetcher)
        assert isinstance(mxm_sp_cred, MusixmatchSpotifyFetcher)
        calls.append(
            (
                "musixmatch_token_search_track",
                {"track": asdict(SAMPLE_TRACK)},
                lambda: mxm_cred._api_search_track(SAMPLE_TRACK),
            )
        )
        calls.append(
            (
                "musixmatch_token_macro_track",
                {"track": asdict(SAMPLE_TRACK)},
                lambda: mxm_cred._api_macro_track(SAMPLE_TRACK),
            )
        )
        calls.append(
            (
                "musixmatch_spotify_token_macro_track",
                {"track": asdict(SAMPLE_TRACK)},
                lambda: mxm_sp_cred._api_macro_track(SAMPLE_TRACK),
            )
        )

        failures = 0
        try:
            for idx, (name, request_payload, fn) in enumerate(calls, start=1):
                stem = f"{idx:03d}_{name}"
                req_path = out_dir / f"{stem}.request.json"
                resp_path = out_dir / f"{stem}.response.json"

                captured_requests.clear()

                try:
                    result = await fn()
                    if isinstance(result, httpx.Response):
                        payload = await _response_dump(result)
                    else:
                        payload = _jsonable(result)
                    _write_json(
                        req_path,
                        {
                            "call": name,
                            "input": request_payload,
                            "http_requests": _jsonable(captured_requests),
                        },
                    )
                    _write_json(resp_path, {"ok": True, "response": payload})
                except Exception as exc:
                    failures += 1
                    _write_json(
                        req_path,
                        {
                            "call": name,
                            "input": request_payload,
                            "http_requests": _jsonable(captured_requests),
                        },
                    )
                    _write_json(
                        resp_path,
                        {
                            "ok": False,
                            "error": str(exc),
                            "traceback": traceback.format_exc(),
                        },
                    )
                    if strict:
                        break
        finally:
            httpx.AsyncClient.send = original_send  # type: ignore[method-assign]

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Call external provider APIs with sample data and save request/response "
            "pairs for API reference."
        )
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("misc/api_ref"),
        help="Output directory for request/response files.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Stop on first failed call.",
    )
    args = parser.parse_args()

    failures = asyncio.run(run_capture(args.out_dir, args.timeout, args.strict))
    print(f"capture finished: failures={failures}, out_dir={args.out_dir}")
    return 1 if (args.strict and failures > 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
