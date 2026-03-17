from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
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

    @router.get("/admin/ui/{asset_path:path}")
    async def admin_ui_asset(asset_path: str) -> FileResponse:
        resolved = (ASSET_DIR / asset_path).resolve()
        asset_root = ASSET_DIR.resolve()
        if asset_root not in resolved.parents and resolved != asset_root:
            raise HTTPException(status_code=404, detail="asset not found")
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(resolved)

    return router
