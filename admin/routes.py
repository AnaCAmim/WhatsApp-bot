import re
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)

from admin.state import app_state
from bot.service import bot_service
from campaigns.contacts import ContactFileError, parse_contacts
from storage.campaign_repository import CampaignRepository


router = APIRouter()
campaign_repository = CampaignRepository()
UPLOAD_ROOT = Path("uploads") / "campaigns"
MAX_MEDIA_BYTES = 50 * 1024 * 1024


def _safe_filename(value):
    name = Path(value or "arquivo").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name[:180] or "arquivo"


@router.get("/api/status")
def status():
    data = app_state.to_dict()
    data["runtime"] = {"thread_alive": bot_service.is_alive()}
    latest = campaign_repository.get_latest()
    data["campaign"] = latest
    return data


@router.post("/api/bot/pause")
def pause_bot():
    if not app_state.pause_bot():
        raise HTTPException(status_code=409, detail="Bot não está executando.")

    return {"success": True, "status": "paused"}


@router.post("/api/bot/resume")
def resume_bot():
    if not app_state.resume_bot():
        raise HTTPException(status_code=409, detail="Bot não está executando.")

    return {"success": True, "status": "running"}


@router.post("/api/campaigns/preview")
async def preview_contacts(file: UploadFile = File(...)):
    content = await file.read()

    try:
        contacts, errors = parse_contacts(file.filename or "", content)
    except ContactFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "success": True,
        "total": len(contacts),
        "contacts": [contact.to_dict() for contact in contacts[:100]],
        "preview_truncated": len(contacts) > 100,
        "warnings": errors[:20],
    }


@router.post("/api/campaigns")
async def create_campaign(
    message_type: str = Form(...),
    text: str = Form(""),
    confirmed: bool = Form(...),
    contacts_file: UploadFile = File(...),
    media_file: Optional[UploadFile] = File(None),
):
    if not confirmed:
        raise HTTPException(
            status_code=400,
            detail="Confirme que os destinatários podem receber esta comunicação.",
        )

    message_type = (message_type or "").strip().lower()
    if message_type not in {"text", "image", "video", "document"}:
        raise HTTPException(status_code=400, detail="Tipo de mensagem inválido.")

    if len(text) > 4096:
        raise HTTPException(status_code=400, detail="Texto excede 4096 caracteres.")

    if message_type == "text" and not text.strip():
        raise HTTPException(status_code=400, detail="Digite uma mensagem.")

    contact_bytes = await contacts_file.read()
    try:
        contacts, warnings = parse_contacts(
            contacts_file.filename or "",
            contact_bytes,
        )
    except ContactFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    media_path = None
    media_name = None

    if message_type != "text":
        if media_file is None or not media_file.filename:
            raise HTTPException(
                status_code=400,
                detail="Selecione o arquivo de mídia/documento.",
            )

        media_bytes = await media_file.read()
        if not media_bytes:
            raise HTTPException(status_code=400, detail="Arquivo de mídia vazio.")
        if len(media_bytes) > MAX_MEDIA_BYTES:
            raise HTTPException(
                status_code=400,
                detail="Arquivo excede o limite Alpha de 50 MB.",
            )

        suffix = Path(media_file.filename).suffix.lower()
        allowed = {
            "image": {".jpg", ".jpeg", ".png", ".webp"},
            "video": {".mp4", ".mov", ".webm"},
            "document": {".pdf", ".doc", ".docx", ".txt", ".xlsx", ".csv"},
        }
        if suffix not in allowed[message_type]:
            raise HTTPException(
                status_code=400,
                detail=f"Formato {suffix or '(sem extensão)'} não permitido para {message_type}.",
            )

        media_name = _safe_filename(media_file.filename)

    campaign_id = campaign_repository.create_campaign(
        message_type=message_type,
        text=text,
        contacts=contacts,
        media_path=None,
        media_name=media_name,
    )

    if message_type != "text":
        folder = UPLOAD_ROOT / campaign_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / media_name
        path.write_bytes(media_bytes)
        media_path = str(path.resolve())

        campaign_repository.set_media_path(campaign_id, media_path)

    app_state.add_event(
        "INFO",
        "campaign_created",
        f"Campanha {campaign_id[:8]} criada com {len(contacts)} destinatário(s).",
    )

    return {
        "success": True,
        "campaign": campaign_repository.get_campaign(campaign_id),
        "warnings": warnings[:20],
    }


@router.get("/api/campaigns/latest")
def latest_campaign():
    return {"campaign": campaign_repository.get_latest()}


@router.get("/api/campaigns/{campaign_id}")
def get_campaign(campaign_id: str):
    campaign = campaign_repository.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campanha não encontrada.")
    return {"campaign": campaign}


@router.post("/api/campaigns/{campaign_id}/cancel")
def cancel_campaign(campaign_id: str):
    if not campaign_repository.cancel_campaign(campaign_id):
        raise HTTPException(status_code=404, detail="Campanha não encontrada.")

    app_state.add_event(
        "WARN",
        "campaign_cancelled",
        f"Campanha {campaign_id[:8]} cancelada.",
    )
    return {"success": True, "campaign": campaign_repository.get_campaign(campaign_id)}


@router.get("/api/queue")
def queue_overview():
    return {"queue": campaign_repository.get_queue_overview()}


@router.post("/api/queue/pause")
def pause_queue():
    paused = campaign_repository.set_queue_paused(True)
    app_state.add_event("WARN", "queue_paused", "Fila de disparos pausada.")
    return {"success": True, "paused": paused, "queue": campaign_repository.get_queue_overview()}


@router.post("/api/queue/resume")
def resume_queue():
    paused = campaign_repository.set_queue_paused(False)
    app_state.add_event("INFO", "queue_resumed", "Fila de disparos retomada.")
    return {"success": True, "paused": paused, "queue": campaign_repository.get_queue_overview()}


@router.post("/api/queue/retry-failed")
def retry_all_failed():
    count = campaign_repository.retry_all_failed()
    app_state.add_event("INFO", "queue_retry_all", f"{count} falha(s) retornaram para a fila.")
    return {"success": True, "retried": count, "queue": campaign_repository.get_queue_overview()}


@router.delete("/api/queue/pending")
def clear_pending_queue():
    count = campaign_repository.clear_pending_queue()
    app_state.add_event("WARN", "queue_cleared", f"{count} item(ns) pendente(s) foram removidos da fila.")
    return {"success": True, "removed": count, "queue": campaign_repository.get_queue_overview()}


@router.post("/api/queue/items/{recipient_id}/retry")
def retry_queue_item(recipient_id: int):
    if not campaign_repository.retry_recipient(recipient_id):
        raise HTTPException(status_code=409, detail="Item não encontrado ou não está em estado de erro.")
    app_state.add_event("INFO", "queue_item_retry", f"Item {recipient_id} reenfileirado.")
    return {"success": True, "queue": campaign_repository.get_queue_overview()}


@router.delete("/api/queue/items/{recipient_id}")
def remove_queue_item(recipient_id: int):
    success, reason = campaign_repository.remove_queue_item(recipient_id)
    if not success:
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="Item da fila não encontrado.")
        if reason == "sending":
            raise HTTPException(status_code=409, detail="O item já está sendo enviado e não pode ser removido agora.")
        raise HTTPException(status_code=409, detail="O item não pode ser removido no estado atual.")
    app_state.add_event("WARN", "queue_item_removed", f"Item {recipient_id} removido da fila.")
    return {"success": True, "queue": campaign_repository.get_queue_overview()}
