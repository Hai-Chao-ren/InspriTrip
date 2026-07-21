from fastapi import APIRouter
from fastapi.responses import JSONResponse

from inspitrip.api.chat_service import demo_chat, full_chat
from inspitrip.api.models import FrontendChatRequest
from inspitrip.api.settings import RuntimeSettings


router = APIRouter(prefix="/api/v2", tags=["chat"])


@router.post("/chat")
def chat(request: FrontendChatRequest):
    settings = RuntimeSettings.load()
    if settings.mode == "demo":
        return demo_chat(request)
    status, payload = full_chat(request, settings)
    return JSONResponse(status_code=status, content=payload)
