from fastapi import APIRouter
from fastapi.responses import JSONResponse

from inspitrip.api.amap_client import AmapRoutePlanner
from inspitrip.api.models import ReverseLocationRequest


router = APIRouter(prefix="/api/location", tags=["location"])


@router.post("/reverse")
def reverse_location(request: ReverseLocationRequest):
    try:
        result = AmapRoutePlanner().reverse_geocode(request.longitude, request.latitude)
    except RuntimeError as exc:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(exc)})
    if not result:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "无法识别当前位置，请手动选择出发城市。"},
        )
    return {"ok": True, **result}
