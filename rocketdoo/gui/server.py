from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import asyncio

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Rocketdoo GUI",
        version="3.0.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from rocketdoo.gui.api import router as api_router
    app.include_router(api_router, prefix="/api")

    @app.websocket("/ws/logs/{container_name}")
    async def ws_logs(websocket: WebSocket, container_name: str, tail: int = 150):
        await websocket.accept()
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "logs", "-f", "--tail", str(tail), container_name,
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
