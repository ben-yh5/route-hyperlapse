from fastapi import APIRouter, Depends, HTTPException, Response

from ...core.mapillary.tiles import fetch_tile
from . import ctx

router = APIRouter()


@router.get("/tiles/{z}/{x}/{y}")
async def tile(z: int, x: int, y: int, c=Depends(ctx)):
    if not (0 <= z <= 14 and 0 <= x < 2**z and 0 <= y < 2**z):
        raise HTTPException(404, "tile out of range")
    if not c.cfg.mapillary_token:
        return Response(status_code=204)
    data = await fetch_tile(c.http, c.cache, z, x, y, ttl_s=c.cfg.tile_ttl_days * 86400, layer="public")
    if not data:
        return Response(status_code=204)
    return Response(data, media_type="application/x-protobuf", headers={"Cache-Control": "max-age=86400"})
