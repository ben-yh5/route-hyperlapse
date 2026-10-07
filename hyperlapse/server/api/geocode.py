from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...core.geocode import geocode
from . import ctx

router = APIRouter()


class GeocodeIn(BaseModel):
    query: str


@router.post("/geocode")
async def geocode_ep(body: GeocodeIn, c=Depends(ctx)):
    results = await geocode(body.query, http=c.http, cache=c.cache)
    return {"results": results}
