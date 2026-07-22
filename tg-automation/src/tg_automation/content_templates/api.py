from __future__ import annotations

from fastapi import APIRouter

from tg_automation.content_templates.catalog import get_preset, list_presets
from tg_automation.core.api import success
from tg_automation.core.errors import NotFoundError
from tg_automation.storage.enums import ContentType

router = APIRouter(prefix="/tg/content-templates", tags=["content-templates"])


@router.get("")
def templates(content_type: ContentType | None = None) -> dict:
    return success(list_presets(content_type))


@router.get("/{preset_id}")
def template(preset_id: str) -> dict:
    item = get_preset(preset_id)
    if item is None:
        raise NotFoundError("content template", preset_id)
    return success(item)
