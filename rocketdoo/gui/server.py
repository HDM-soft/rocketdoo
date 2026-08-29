import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

STATIC_DIR = Path(__file__).parent / "static"

DEFAULT_PORT = 8070
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


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


def create_app(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> FastAPI:
    app = FastAPI(
        title="Rocketdoo GUI",
        version="3.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

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

    from rocketdoo.gui.api import router as api_router

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
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=600.0)
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

    @app.get("/", response_class=FileResponse)
    async def root():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "3.0.0"}

    @app.get("/{path:path}", response_class=FileResponse)
    async def spa_fallback(path: str):
        if path.startswith(("api/", "ws/")):
            return JSONResponse({"error": "Not found"}, status_code=404)
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"error": "GUI not found"}, status_code=404)

    return app
