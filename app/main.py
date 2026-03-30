"""FastAPI app demonstrating debaterhub-sdk Mode 1, Mode 2, and AI-AI debates."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from . import store
from .routes import debates, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for debate_id in store.all_ids():
        session = store.get(debate_id)
        if session and session.connected:
            await session.disconnect()
        client = getattr(session, "_client_ref", None)
        if client:
            await client.close()


app = FastAPI(
    title="Debate FastAPI Reference",
    description="Reference app for debaterhub-sdk: Mode 1 (token-only), Mode 2 (server-managed), and AI-AI debates",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(debates.router)
app.include_router(ws.router)

static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(static_dir / "index.html"))
