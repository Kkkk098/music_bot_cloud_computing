# server.py
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleRequest
import os, json, pathlib, secrets, base64, email.utils
import re

# ---------- (Optional) APScheduler for hourly tasks ----------
ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "true").lower() == "true"
try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    ENABLE_SCHEDULER = False

# ---------- Config ----------
APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]
DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(exist_ok=True)
CLIENT_SECRET_FILE = "client_secret.json"  # скачанный OAuth JSON

app = FastAPI(title="Gmail Tool Server", version="0.2.0")
last_messages_cache = []
# ---------- Models for OpenAPI ----------
class GmailHeader(BaseModel):
    name: str
    value: str

class GmailMessage(BaseModel):
    id: str
    snippet: Optional[str] = None
    headers: Optional[List[GmailHeader]] = None

class GmailMessagesResponse(BaseModel):
    messages: List[GmailMessage]

class SendEmailRequest(BaseModel):
    to: str
    subject: str
    body: str
    is_html: Optional[bool] = False
    sender: Optional[str] = None

# ---------- Token store helpers ----------
def creds_path(user_id: str) -> pathlib.Path:
    return DATA_DIR / f"{user_id}.json"

def save_creds(creds: Credentials, user_id: str):
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    creds_path(user_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def load_creds(user_id: str) -> Optional[Credentials]:
    p = creds_path(user_id)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return Credentials.from_authorized_user_info(info=data, scopes=data.get("scopes", SCOPES))

# ---------- Gmail service ----------
def gmail_service(user_id: str = "me"):
    creds = load_creds(user_id)
    if not creds:
        raise HTTPException(status_code=401, detail="Not authorized. Open /auth/login.")
    if not creds.valid and creds.refresh_token:
        creds.refresh(GoogleRequest())
        save_creds(creds, user_id)
    return build("gmail", "v1", credentials=creds)

# ---------- Auth ----------
@app.get("/auth/login", summary="Auth Login", operation_id="auth_login")
def auth_login():
    if not os.path.exists(CLIENT_SECRET_FILE):
        raise HTTPException(status_code=500, detail="client_secret.json not found")
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_FILE,
        scopes=SCOPES,
        redirect_uri=f"{APP_URL}/oauth2/callback",
    )
    state = secrets.token_urlsafe(16)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    (DATA_DIR / "state.txt").write_text(state, encoding="utf-8")
    return RedirectResponse(auth_url)

@app.get("/oauth2/callback", summary="Oauth2 Callback", operation_id="oauth2_callback")
def oauth2_callback(state: str = "", code: str = ""):
    saved_state = ""
    st_path = DATA_DIR / "state.txt"
    if st_path.exists():
        saved_state = st_path.read_text(encoding="utf-8")
    if state and saved_state and state != saved_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_FILE,
        scopes=SCOPES,
        redirect_uri=f"{APP_URL}/oauth2/callback",
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    save_creds(creds, "me")
    return JSONResponse({"status": "ok"})

# ---------- Helpers for message parsing ----------
def _decode_b64(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
    except Exception:
        return ""

def _html_to_text(html: str) -> str:
    if not html:
        return ""
    # Удаляем скрипты/стили и теги, переводим br/p в переносы строк
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _walk_parts(part: Dict[str, Any], out: Dict[str, Any]):
    mime = part.get("mimeType", "")
    body = part.get("body", {}) or {}
    data = body.get("data")
    filename = part.get("filename")
    if data and mime.startswith("text/"):
        if mime.startswith("text/plain"):
            out["text_plain"] += _decode_b64(data)
        elif mime.startswith("text/html"):
            out["text_html"] += _decode_b64(data)
    if filename:
        out["attachments"].append({
            "filename": filename,
            "mimeType": mime,
            "size": body.get("size", 0),
            "attachmentId": body.get("attachmentId")
        })
    for p in part.get("parts", []) or []:
        _walk_parts(p, out)

# ---------- Messages: list ----------
@app.get(
    "/messages",
    summary="List Messages",
    operation_id="gmail_list_messages"
)
def list_messages(q: str = "is:unread", maxResults: int = 3):
    """
    Возвращает последние письма с заголовками, сниппетом и распарсенным текстом.
    Также кладёт их в кэш для обращения по позиции.
    """
    global last_messages_cache

    service = gmail_service()
    res = service.users().messages().list(userId="me", q=q, maxResults=maxResults).execute()
    ids = [m["id"] for m in res.get("messages", [])]
    out = []

    for mid in ids:
        msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
        payload = msg.get("payload", {}) or {}
        headers_list = payload.get("headers", []) or []
        headers = [{"name": h["name"], "value": h["value"]} for h in headers_list]

        parsed = {"text_plain": "", "text_html": "", "attachments": []}

        # top-level body
        body = payload.get("body", {}) or {}
        data = body.get("data")
        mime = payload.get("mimeType", "")
        if data and mime.startswith("text/"):
            if mime.startswith("text/plain"):
                parsed["text_plain"] += _decode_b64(data)
            elif mime.startswith("text/html"):
                parsed["text_html"] += _decode_b64(data)

        # parts
        for p in payload.get("parts", []) or []:
            _walk_parts(p, parsed)

        out.append({
            "id": msg.get("id"),
            "threadId": msg.get("threadId"),
            "snippet": msg.get("snippet"),
            "headers": headers,
            "text_plain": (parsed["text_plain"] or "").strip(),
            "text_html": (parsed["text_html"] or "").strip(),
            "attachments": parsed["attachments"],
        })

    last_messages_cache = out[:10]
    return out

# ---------- Messages: get by position ----------
@app.get(
    "/messages/position/{position}",
    summary="Get message by position in last list",
    operation_id="gmail_get_message_by_position",
)
def get_message_by_position(position: int):
    """
    Get message by position (1-indexed) from the last /messages call.
    Example: position=1 returns first message, position=2 returns second, etc.
    """
    global last_messages_cache
    
    if not last_messages_cache:
        raise HTTPException(
            status_code=400, 
            detail="No messages in cache. Call /messages first to populate cache."
        )
    
    if position < 1 or position > len(last_messages_cache):
        raise HTTPException(
            status_code=400,
            detail=f"Position must be between 1 and {len(last_messages_cache)}"
        )
    
    # Возвращаем письмо (position - 1, т.к. список 0-indexed)
    return last_messages_cache[position - 1]


# ---------- Messages: get (parsed/full) ----------
@app.get(
    "/messages/{id}",
    summary="Get message by id (parsed)",
    operation_id="gmail_get_message",
)
def get_message(id: str, fmt: str = "full"):
    """
    Get a message by id. Default fmt='full' and returns parsed fields:
    headers, snippet, text_plain, text_html, attachments.
    If fmt in ['raw','metadata'], raw Gmail response is returned.
    """
    service = gmail_service()

    if fmt in ("raw", "metadata"):
        msg = service.users().messages().get(userId="me", id=id, format=fmt).execute()
        return msg

    # full + parsed output
    msg = service.users().messages().get(userId="me", id=id, format="full").execute()
    payload = msg.get("payload", {}) or {}
    headers_list = payload.get("headers", []) or []
    headers = {h["name"]: h["value"] for h in headers_list}

    parsed = {"text_plain": "", "text_html": "", "attachments": []}

    # top-level body
    body = payload.get("body", {}) or {}
    data = body.get("data")
    mime = payload.get("mimeType", "")
    if data and mime.startswith("text/"):
        if mime.startswith("text/plain"):
            parsed["text_plain"] += _decode_b64(data)
        elif mime.startswith("text/html"):
            parsed["text_html"] += _decode_b64(data)

    # walk parts
    for p in payload.get("parts", []) or []:
        _walk_parts(p, parsed)

    return {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "snippet": msg.get("snippet"),
        "headers": headers,
        "text_plain": (parsed["text_plain"] or "").strip(),
        "text_html": (parsed["text_html"] or "").strip(),
        "attachments": parsed["attachments"],
    }

# ---------- Messages: mark read (two paths for convenience) ----------
@app.post(
    "/messages/{id}/mark_read",
    summary="Mark message as read (legacy path)",
    operation_id="gmail_mark_read",
)
def mark_read_legacy(id: str):
    service = gmail_service()
    body = {"removeLabelIds": ["UNREAD"]}
    res = service.users().messages().modify(userId="me", id=id, body=body).execute()
    return {"status": "ok", "id": res.get("id")}

@app.post(
    "/messages/{id}/read",
    summary="Mark message as read",
    operation_id="gmail_read",
)
def mark_read(id: str):
    return mark_read_legacy(id)

# ---------- Send ----------
def _create_raw_email(to: str, subject: str, body: str, sender: Optional[str] = None, is_html: bool = False) -> str:
    subtype = "html" if is_html else "plain"
    from email.mime.text import MIMEText
    msg = MIMEText(body, _subtype=subtype, _charset="utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if sender:
        msg["From"] = sender
    msg["Date"] = email.utils.formatdate(localtime=True)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return raw

@app.post(
    "/send",
    summary="Send email",
    operation_id="gmail_send",
)
def send_email(req: SendEmailRequest):
    service = gmail_service()
    raw = _create_raw_email(req.to, req.subject, req.body, req.sender, req.is_html or False)
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"status": "sent", "id": sent.get("id")}

# ---------- Presets ----------
@app.get("/presets/last24", summary="Last 24 hours", operation_id="gmail_last24")
def last24(maxResults: int = 10):
    service = gmail_service()
    q = "newer_than:1d"
    data = service.users().messages().list(userId="me", q=q, maxResults=maxResults).execute()
    return data

@app.get("/presets/attachments", summary="With attachments", operation_id="gmail_with_attachments")
def with_attachments(maxResults: int = 10):
    service = gmail_service()
    q = "has:attachment"
    data = service.users().messages().list(userId="me", q=q, maxResults=maxResults).execute()
    return data

@app.get("/presets/domain", summary="From domain", operation_id="gmail_from_domain")
def from_domain(domain: str = Query(..., description="example.com"), maxResults: int = 10):
    service = gmail_service()
    q = f"from:{domain}"
    data = service.users().messages().list(userId="me", q=q, maxResults=maxResults).execute()
    return data

# ---------- Search keywords ----------
KEYWORDS_QUERY = '("ППР" OR "АВР" OR "Работы" OR "Аварийные" OR "Плановые")'

@app.get("/search/keywords", summary="Search by given russian keywords", operation_id="gmail_search_keywords")
def search_keywords(maxResults: int = 50):
    service = gmail_service()
    q = KEYWORDS_QUERY
    data = service.users().messages().list(userId="me", q=q, maxResults=maxResults).execute()
    return data

# ---------- Hourly automation (optional) ----------
def hourly_check():
    try:
        service = gmail_service()
        q = KEYWORDS_QUERY + " newer_than:1h"
        data = service.users().messages().list(userId="me", q=q, maxResults=50).execute()
        count = len(data.get("messages", []))
        # TODO: при необходимости — сохранить/логировать/слать webhook
        print(f"[hourly] found {count} matching messages in last hour.")
    except Exception as e:
        print("[hourly] error:", e)

# Глобальная переменная планировщика
scheduler = None

if ENABLE_SCHEDULER:
    @app.on_event("startup")
    def _start_jobs():
        global scheduler
        try:
            scheduler = BackgroundScheduler()
            scheduler.add_job(hourly_check, "interval", hours=1, id="hourly_check", max_instances=1, coalesce=True)
            scheduler.start()
            print("[scheduler] started (hourly_check)")
        except Exception as e:
            print("[scheduler] failed:", e)

    @app.on_event("shutdown")
    def _stop_jobs():
        global scheduler
        try:
            if scheduler:
                scheduler.shutdown(wait=False)
                print("[scheduler] stopped")
        except Exception:
            pass