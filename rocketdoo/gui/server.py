import asyncio
import secrets
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import Headers, QueryParams
from starlette.routing import get_route_path

from rocketdoo import __version__

STATIC_DIR = Path(__file__).parent / "static"

DEFAULT_PORT = 8070
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


async def _stream_process(websocket: WebSocket, cmd: list[str], timeout: float = 600.0) -> None:
    """Stream a subprocess's combined stdout/stderr over an already-accepted websocket.

    Emits each line as its own text message, then a final ``\\x00exit:{code}``
    marker. Assumes the caller already called ``websocket.accept()`` and, on
    validation failure, already sent its own error and exit banner instead of
    calling this at all. Shared by every route that runs a CLI command and
    shows its live output in the frontend's DockerTerminal.
    """
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
            if not line:
                break
            await websocket.send_text(line.decode("utf-8", errors="replace").rstrip())
        await process.wait()
        await websocket.send_text(f"\x00exit:{process.returncode}")
    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        try:
            await websocket.send_text("[error] Timed out after 10 minutes")
            await websocket.send_text("\x00exit:1")
        except Exception:
            pass
    except Exception as e:
        try:
            await websocket.send_text(f"[error] {e}")
            await websocket.send_text("\x00exit:1")
        except Exception:
            pass
    finally:
        if process and process.returncode is None:
            try:
                process.kill()
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass


class TokenAuthMiddleware:
    """Require the session token on every /api and /ws request.

    CORS only constrains browsers; any local process can reach 127.0.0.1 and
    drive Docker or the filesystem through this API. The token raises that bar
    to "processes that can read the terminal running rkd gui". ASGI middleware
    is used instead of a FastAPI dependency because the 3 websocket routes are
    plain ASGI routes on `app`, outside the router, and a dependency cannot
    reach them; a `BaseHTTPMiddleware` cannot either, since it never sees the
    `websocket` scope.
    """

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and self._protected(scope):
            if not self._authorized(scope):
                await self._reject(scope, receive, send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    def _protected(scope) -> bool:
        # get_route_path, not scope["path"]: the router strips root_path before
        # matching, so mounting this app under a prefix would make the two
        # diverge and every protected route would answer unauthenticated.
        path = get_route_path(scope)
        return (path.startswith("/api/") or path.startswith("/ws/")) and scope.get("method") != "OPTIONS"

    def _authorized(self, scope) -> bool:
        provided = Headers(scope=scope).get("x-rkd-token") or QueryParams(scope["query_string"]).get("token")
        # isascii() first: compare_digest raises TypeError on non-ASCII strings,
        # and the value is entirely attacker-controlled. Without this a crafted
        # header answers 500 with a traceback instead of 401.
        if not provided or not provided.isascii():
            return False
        return secrets.compare_digest(provided, self.token)

    @staticmethod
    async def _reject(scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            response = JSONResponse({"detail": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)


def local_origins(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> list[str]:
    """Browser origins allowed to call this API.

    The SPA is served from this same app, so its own requests are same-origin
    and need no CORS at all. The header only ever matters for a *different*
    page calling in — which is exactly what must not be allowed: these
    endpoints drive Docker and browse the filesystem, and binding to localhost
    is no protection, since the request comes from the user's own browser.
    """
    origins = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    if host not in _LOOPBACK_HOSTS:
        # Explicitly bound elsewhere (e.g. --host 192.168.1.10): allow that too,
        # otherwise the page the user actually opens cannot call its own API.
        origins.append(f"http://{host}:{port}")
    return origins


def create_app(host: str = "127.0.0.1", port: int = DEFAULT_PORT, token: str | None = None) -> FastAPI:
    app = FastAPI(
        title="Rocketdoo GUI",
        version=__version__,
        docs_url="/api/docs",
        # Under /api so the token middleware covers it: on its default path it
        # served the full route inventory to anyone on the host.
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    # Generated even if the caller passes nothing: there is no way to end up
    # with an unprotected API by omission.
    app.state.rkd_token = token or secrets.token_urlsafe(32)

    # Added before CORSMiddleware so it ends up as the inner layer: CORS stays
    # the outermost middleware and keeps handling preflight and headers
    # exactly as before, unaffected by the token check.
    app.add_middleware(TokenAuthMiddleware, token=app.state.rkd_token)

    # Not allow_origins=["*"]: with allow_credentials=True Starlette echoes the
    # caller's Origin back, so any site the user visited while `rkd gui` was
    # running could read /api/workspace and POST /api/docker/down.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=local_origins(host, port),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/version")
    def get_version():
        """The installed rocketdoo version, so the SPA stops hardcoding it."""
        return {"version": __version__}

    from rocketdoo.core.addons_path import ensure_addons_path
    from rocketdoo.gui.api import router as api_router
    from rocketdoo.gui.api.odoo import build_update_command

    app.include_router(api_router, prefix="/api")

    _DOCKER_CMDS = {
        "up": ["docker", "compose", "up", "-d"],
        "build": ["docker", "compose", "build"],
        "down": ["docker", "compose", "down"],
        "restart": ["docker", "compose", "restart"],
        "stop": ["docker", "compose", "stop"],
        "pull": ["docker", "compose", "pull"],
    }

    @app.websocket("/ws/docker/{action}")
    async def ws_docker_action(websocket: WebSocket, action: str):
        cmd = _DOCKER_CMDS.get(action)
        await websocket.accept()
        if not cmd:
            await websocket.send_text(f"[error] Unknown action: {action}")
            await websocket.send_text("\x00exit:1")
            await websocket.close()
            return
        await _stream_process(websocket, cmd)

    @app.websocket("/ws/odoo/update")
    async def ws_odoo_update(websocket: WebSocket, module: str, db: str):
        await websocket.accept()
        action, changes = ensure_addons_path(Path.cwd())
        if action == "updated":
            await websocket.send_text(f"[rkd] addons_path updated: {', '.join(changes)}")
        elif action == "failed":
            await websocket.send_text(f"[rkd] could not update addons_path: {', '.join(changes)}")
        cmd, error = build_update_command(module, db)
        if error:
            await websocket.send_text(f"[error] {error}")
            await websocket.send_text("\x00exit:1")
            await websocket.close()
            return
        await _stream_process(websocket, cmd)

    @app.websocket("/ws/logs/{container_name}")
    async def ws_logs(websocket: WebSocket, container_name: str, tail: int = 150):
        await websocket.accept()
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "logs",
                "-f",
                "--tail",
                str(tail),
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=30.0)
                if not line:
                    break
                await websocket.send_text(line.decode("utf-8", errors="replace").rstrip())
        except WebSocketDisconnect:
            pass
        except asyncio.TimeoutError:
            await websocket.send_text("--- [no new logs for 30s] ---")
        except Exception as e:
            try:
                await websocket.send_text(f"[error] {e}")
            except Exception:
                pass
        finally:
            if process and process.returncode is None:
                process.kill()
            try:
                await websocket.close()
            except Exception:
                pass

    # The SPA is one file with a fixed name, so without this the browser keeps
    # serving its stored copy across upgrades — a user on a new version saw the
    # previous interface until a forced refresh. "no-cache" does not disable
    # caching, it requires revalidation: with the etag already sent, an
    # unchanged file still answers 304.
    _SPA_HEADERS = {"Cache-Control": "no-cache"}

    @app.get("/", response_class=FileResponse)
    async def root():
        return FileResponse(STATIC_DIR / "index.html", headers=_SPA_HEADERS)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/{path:path}", response_class=FileResponse)
    async def spa_fallback(path: str):
        if path.startswith(("api/", "ws/")):
            return JSONResponse({"error": "Not found"}, status_code=404)
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index, headers=_SPA_HEADERS)
        return JSONResponse({"error": "GUI not found"}, status_code=404)

    return app
