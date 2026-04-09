"""Unix-socket control channel for communicating with a running watch session."""

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ..config import AppConfig

if TYPE_CHECKING:
    from .session import WatchCoordinator


class ControlServer:
    """Control server that handles offset/status commands over a Unix socket."""

    _socket_path: Path
    _server: asyncio.AbstractServer | None

    def __init__(
        self,
        config: AppConfig,
        socket_path: Path | None = None,
    ) -> None:
        """Initialize control server with socket path from config or explicit override."""
        self._socket_path: Path = socket_path or Path(config.watch.socket_path)
        self._server: asyncio.AbstractServer | None = None

    async def start(self, session: "WatchCoordinator") -> bool:
        """Start listening for control requests and bind session handlers."""
        if not await self._prepare_socket_path():
            return False

        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            lambda r, w: self._handle(session, r, w),
            path=str(self._socket_path),
        )
        return True

    async def _prepare_socket_path(self) -> bool:
        """Ensure socket path is usable and reject when another session is active."""
        if not self._socket_path.exists():
            return True

        try:
            reader, writer = await asyncio.open_unix_connection(str(self._socket_path))
            writer.close()
            await writer.wait_closed()
            logger.error(
                "A watch session is already running. Use 'lrx watch ctl status'."
            )
            return False
        except Exception:
            try:
                self._socket_path.unlink(missing_ok=True)
            except Exception:
                pass
            return True

    async def stop(self) -> None:
        """Stop control server and remove stale socket path."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        try:
            self._socket_path.unlink(missing_ok=True)
        except Exception:
            pass

    async def _handle(
        self,
        session: "WatchCoordinator",
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one control request and send JSON response."""
        resp: dict[str, object] = {"ok": False, "error": "internal error"}
        try:
            line = await reader.readline()
            if not line:
                resp = {"ok": False, "error": "empty request"}
            else:
                req = json.loads(line.decode("utf-8"))
                cmd = req.get("cmd")
                if cmd == "offset":
                    delta = int(req.get("delta", 0))
                    resp = session.handle_offset(delta)
                elif cmd == "status":
                    resp = session.handle_status()
                else:
                    resp = {"ok": False, "error": "unknown command"}
        except Exception as e:
            resp = {"ok": False, "error": str(e)}
        finally:
            writer.write((json.dumps(resp) + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()


class ControlClient:
    """Control client used by CLI commands to talk to active watch session."""

    _socket_path: Path

    def __init__(
        self,
        config: AppConfig,
        socket_path: Path | None = None,
    ) -> None:
        """Initialize control client with socket path from config or explicit override."""
        self._socket_path: Path = socket_path or Path(config.watch.socket_path)

    async def _send_async(self, cmd: dict[str, object]) -> dict[str, object]:
        """Send one JSON command to control server and return JSON response."""
        if not self._socket_path.exists():
            return {"ok": False, "error": "No watch session running."}

        try:
            reader, writer = await asyncio.open_unix_connection(str(self._socket_path))
        except Exception:
            return {"ok": False, "error": "No watch session running."}

        writer.write((json.dumps(cmd) + "\n").encode("utf-8"))
        await writer.drain()
        line = await reader.readline()
        writer.close()
        await writer.wait_closed()
        if not line:
            return {"ok": False, "error": "Empty response."}
        return json.loads(line.decode("utf-8"))

    def send(self, cmd: dict[str, object]) -> dict[str, object]:
        """Synchronous wrapper around async control request."""
        return asyncio.run(self._send_async(cmd))


def parse_delta(raw: str) -> tuple[bool, int | None, str | None]:
    """Parse signed millisecond offset delta string for ctl offset command."""
    value = raw.strip()
    try:
        if value.startswith("+"):
            return True, int(value[1:]), None
        if value.startswith("-"):
            return True, -int(value[1:]), None
        return True, int(value), None
    except ValueError:
        return False, None, f"Invalid offset delta: {raw}"
