import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from db import get_response, init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "mini_app")

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


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
