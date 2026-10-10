"""Standalone test server for exercising the real PDF editor in Chromium."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from papermint_edit_api import router

ROOT = Path(__file__).resolve().parents[1]
app = FastAPI()
app.include_router(router)


@app.get("/", include_in_schema=False)
def editor_home():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/edit-pdf-prototype.html", status_code=307)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def production_script_policy(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; connect-src 'self'"
    )
    return response
