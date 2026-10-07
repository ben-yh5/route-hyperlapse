from fastapi import APIRouter, Depends, HTTPException, Response

from ...core.mapillary.download import get_image
from . import ctx

router = APIRouter()


@router.get("/image/{image_id}")
async def image(image_id: str, size: int = 256, c=Depends(ctx)):
    if not image_id.isdigit():
        raise HTTPException(400, "bad image id")
    try:
        data = await get_image(c.http, c.cache, image_id, size)
    except FileNotFoundError:
        raise HTTPException(404, "image unavailable") from None
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
