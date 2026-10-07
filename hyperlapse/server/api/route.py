from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from ...core.routing import GpxRouter, OSRMRouter, Route
from . import ctx

router = APIRouter()


class LatLon(BaseModel):
    lat: float
    lon: float


class RouteIn(BaseModel):
    waypoints: list[LatLon]


def route_out(r: Route) -> dict:
    return {
        "route_id": r.route_id,
        "geojson": r.geojson(),
        "distance_m": r.distance_m,
        "source": r.source,
        "waypoints": [{"lat": a, "lon": b} for a, b in r.waypoints] if r.waypoints else None,
    }


@router.post("/route")
async def route_ep(body: RouteIn, c=Depends(ctx)):
    if len(body.waypoints) < 2:
        raise HTTPException(400, "need a start and an end")
    r = await OSRMRouter(c.http, c.cache, c.cfg.osrm_url).route([(w.lat, w.lon) for w in body.waypoints])
    c.store.save_route(r)
    return route_out(r)


@router.post("/route/gpx")
async def route_gpx(file: UploadFile, c=Depends(ctx)):
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "GPX file too large")
    r = GpxRouter(c.cache).route_from(data)
    c.store.save_route(r)
    return route_out(r)


@router.get("/route/{route_id}")
async def route_get(route_id: str, c=Depends(ctx)):
    return route_out(c.store.load_route(route_id))
