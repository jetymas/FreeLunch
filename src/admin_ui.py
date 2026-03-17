from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

ASSET_DIR = Path(__file__).with_name("admin_assets")


def build_admin_ui_router() -> APIRouter:
    router = APIRouter()

    @router.get("/admin/ui")
    async def admin_ui_index() -> FileResponse:
        return FileResponse(ASSET_DIR / "index.html", media_type="text/html")

    @router.get("/admin/ui/")
    async def admin_ui_trailing_slash() -> RedirectResponse:
        return RedirectResponse(url="/admin/ui", status_code=307)

    @router.get("/admin/ui/app.js")
    async def admin_ui_script() -> FileResponse:
        return FileResponse(ASSET_DIR / "app.js", media_type="application/javascript")

    @router.get("/admin/ui/app.css")
    async def admin_ui_styles() -> FileResponse:
        return FileResponse(ASSET_DIR / "app.css", media_type="text/css")

    return router
