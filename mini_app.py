import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from db import get_response, init_db, save_response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "mini_app")

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

API_KEY = os.getenv("MINI_APP_API_KEY", "").strip()
cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


def _require_api_key(x_api_key: str | None) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/api/response")
def api_response(id: str):
    row = get_response(id)
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "id": row["response_id"],
        "created_at": row["created_at"],
        "content": row["content"],
    }


@app.post("/api/response")
def api_response_create(payload: dict, x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Empty content")
    user_id = payload.get("user_id")
    response_id = save_response(user_id, content)
    return {"id": response_id}
