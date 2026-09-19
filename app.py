from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from pathlib import Path
from typing import List
from collections import defaultdict, deque
from html import escape, unescape
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError, URLError
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid

import stripe

from papermint_v28_engine import (
    V28JobManager,
    V28Policy,
    V28Error,
    V28Rejected,
    V28QueueFull,
)
from papermint_merge_engine import MergeError, merge_pdfs
from papermint_split_engine import SplitError, split_pdf
from papermint_compress_engine import CompressError, compress_pdf
from papermint_word_pdf_engine import WordPdfError, word_to_pdf
from papermint_rotate_engine import RotateError, rotate_pdf
from papermint_organize_engine import OrganizeError, organize_pdf
from papermint_job_queue import (
    QueueCapacityReached,
    QueueUnavailable,
    enqueue_tool_job,
    fetch_job,
    public_job_status,
)
from papermint_extra_engines import OCR_LANGUAGES
from papermint_auth import AuthError, AuthStore, SESSION_SECONDS
from papermint_analytics import AnalyticsStore
from papermint_limits import (
    FREE_UPLOAD_BYTES,
    FREE_UPLOAD_MB,
    FreeLimitReached,
    FreeLimitUnavailable,
    account_quota_key,
    anonymous_quota_key,
    release_free_task,
    reserve_free_task,
)
from papermint_ai import (
    AskOctoError,
    AskOctoSessions,
    ai_is_configured,
    answer_question,
    extract_pdf_text,
    summarize_document,
)


# ============================================================
# PAPERMINT WEB + V28 PDF -> WORD ENGINE
# ============================================================

BASE = Path(__file__).parent
TMP = BASE / "tmp"
TMP.mkdir(exist_ok=True)

AUTH_COOKIE = "papermint_session"
AUTH_STORE = AuthStore(
    database_url=os.getenv("DATABASE_URL"),
    sqlite_path=BASE / "data" / "papermint.sqlite3",
)
ANALYTICS_STORE = AnalyticsStore(
    database_url=os.getenv("DATABASE_URL"),
    sqlite_path=BASE / "data" / "papermint.sqlite3",
)
ANALYTICS_ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.getenv("PAPERMINT_ADMIN_EMAILS", "pdfaspect@gmail.com").split(",")
    if email.strip()
}

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICE_IDS = {
    "monthly": os.getenv("STRIPE_MONTHLY_PRICE_ID", "").strip(),
    "yearly": os.getenv("STRIPE_YEARLY_PRICE_ID", "").strip(),
}
stripe.api_key = STRIPE_SECRET_KEY or None

AI_DOCUMENT_LIMIT = max(1, int(os.getenv("PAPERMINT_AI_DOCUMENT_LIMIT", "30")))
AI_QUESTION_LIMIT = max(1, int(os.getenv("PAPERMINT_AI_QUESTION_LIMIT", "90")))
AI_QUESTIONS_PER_DOCUMENT = max(
    1,
    int(os.getenv("PAPERMINT_AI_QUESTIONS_PER_DOCUMENT", "3")),
)
AI_SESSIONS = AskOctoSessions(
    ttl_seconds=int(os.getenv("PAPERMINT_AI_SESSION_SECONDS", "1800")),
    questions_per_document=AI_QUESTIONS_PER_DOCUMENT,
)
AI_RESPONSE_LANGUAGES = {
    "en": "English",
    "cs": "Czech",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "zh": "Chinese",
    "hi": "Hindi",
    "ja": "Japanese",
}

MAX_UPLOAD_MB = max(1, int(os.getenv("PAPERMINT_MAX_UPLOAD_MB", "50")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_REQUEST_MB = max(MAX_UPLOAD_MB, int(os.getenv("PAPERMINT_MAX_REQUEST_MB", "100")))
MAX_REQUEST_BYTES = MAX_REQUEST_MB * 1024 * 1024
TEMP_RETENTION_SECONDS = max(
    3600,
    int(os.getenv("PAPERMINT_TEMP_RETENTION_SECONDS", "7200")),
)
RESULT_RETENTION_SECONDS = max(
    300,
    int(os.getenv("PAPERMINT_RESULT_RETENTION_SECONDS", "1800")),
)
JANITOR_INTERVAL_SECONDS = max(
    30,
    int(os.getenv("PAPERMINT_JANITOR_INTERVAL_SECONDS", "60")),
)
RESULT_FILE_PREFIXES = (
    "merged-",
    "split-",
    "compressed-",
    "word-pdf-",
    "rotated-",
    "organized-",
    "protected-",
    "unlocked-",
    "signed-",
    "watermarked-",
    "numbered-",
    "pdf-ppt-",
    "pdf-excel-",
    "pdf-jpg-",
    "ppt-pdf-",
    "excel-pdf-",
    "image-pdf-",
    "html-pdf-",
    "pdfa-",
    "repaired-",
    "ocr-",
    "comparison-",
    "redacted-",
    "cropped-",
)

PDF_WORD_OUTPUTS = TMP / "pdf-word-results"
PDF_WORD_OUTPUTS.mkdir(exist_ok=True)

app = FastAPI(title="PDFaspect PDF Toolbox")

_RATE_LIMITS: dict[str, deque[float]] = defaultdict(deque)
_RATE_LIMIT_LOCK = threading.Lock()


def _client_address(request: Request) -> str:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request, bucket: str, limit: int, window: int) -> None:
    now = time.monotonic()
    key = f"{bucket}:{_client_address(request)}"
    with _RATE_LIMIT_LOCK:
        attempts = _RATE_LIMITS[key]
        while attempts and attempts[0] <= now - window:
            attempts.popleft()
        if len(attempts) >= limit:
            retry_after = max(1, int(window - (now - attempts[0])))
            raise HTTPException(
                429,
                "Too many requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        attempts.append(now)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self' https://checkout.stripe.com; img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self'",
    )
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    if request.url.scheme == "https" or forwarded_proto == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

app.mount(
    "/static",
    StaticFiles(directory=BASE / "static"),
    name="static",
)


TOOLS = [
    ("merge", "Merge PDF", "Combine PDFs in the order you want."),
    ("split", "Split PDF", "Split a PDF into separate files or ranges."),
    ("compress", "Compress PDF", "Reduce PDF file size while preserving quality."),
    ("pdf-word", "PDF to Word", "Convert PDF to an editable Word document."),
    ("pdf-ppt", "PDF to PowerPoint", "Convert each PDF page to a PowerPoint slide."),
    ("pdf-excel", "PDF to Excel", "Extract detected tables into an XLSX workbook."),
    ("pdf-jpg", "PDF to JPG", "Render PDF pages as JPG images."),
    ("word-pdf", "Word to PDF", "Convert DOC/DOCX to PDF."),
    ("ppt-pdf", "PowerPoint to PDF", "Convert PPT/PPTX to PDF."),
    ("excel-pdf", "Excel to PDF", "Convert XLS/XLSX to PDF."),
    ("jpg-pdf", "JPG to PDF", "Combine images into a PDF."),
    ("sign", "Sign PDF", "Add a simple text signature to a PDF page."),
    ("watermark", "Watermark", "Add text watermark to every page."),
    ("rotate", "Rotate PDF", "Rotate every page by 90, 180 or 270 degrees."),
    ("html-pdf", "HTML to PDF", "Convert uploaded HTML into PDF."),
    ("unlock", "Unlock PDF", "Remove password protection when you know the password."),
    ("protect", "Protect PDF", "Encrypt a PDF with a password."),
    ("organize", "Organize PDF", "Reorder pages using a page list such as 3,1,2."),
    ("pdfa", "PDF to PDF/A", "Create a standards-oriented PDF/A-2b archival copy."),
    ("repair", "Repair PDF", "Rewrite a damaged/readable PDF into a fresh file."),
    ("page-numbers", "Page numbers", "Add page numbers to every page."),
    ("scan-pdf", "Scan to PDF", "Convert phone scans/images into a PDF."),
    ("ocr", "OCR PDF", "Recognize text in multiple languages from scanned PDF pages."),
    ("compare", "Compare PDF", "Create a text difference report for two PDFs."),
    ("redact", "Redact PDF", "Search and permanently redact specified text."),
    ("crop", "Crop PDF", "Crop all pages by margins in millimeters."),
    ("ask-octo", "Ask Octo AI", "Summarize a PDF and ask three cited questions."),
]

TOOLS_CS = {
    "merge": ("Sloučit PDF", "Spojte PDF soubory v požadovaném pořadí."),
    "split": ("Rozdělit PDF", "Rozdělte PDF na samostatné soubory nebo rozsahy stran."),
    "compress": ("Komprimovat PDF", "Zmenšete velikost PDF při zachování kvality."),
    "pdf-word": ("PDF do Wordu", "Převeďte PDF na upravitelný dokument Word."),
    "pdf-ppt": ("PDF do PowerPointu", "Převeďte každou stránku PDF na snímek PowerPointu."),
    "pdf-excel": ("PDF do Excelu", "Převeďte rozpoznané tabulky do sešitu XLSX."),
    "pdf-jpg": ("PDF do JPG", "Převeďte stránky PDF na obrázky JPG."),
    "word-pdf": ("Word do PDF", "Převeďte dokument DOC nebo DOCX do PDF."),
    "ppt-pdf": ("PowerPoint do PDF", "Převeďte prezentaci PPT nebo PPTX do PDF."),
    "excel-pdf": ("Excel do PDF", "Převeďte tabulku XLS nebo XLSX do PDF."),
    "jpg-pdf": ("JPG do PDF", "Spojte obrázky do jednoho PDF."),
    "sign": ("Podepsat PDF", "Přidejte na stránku PDF jednoduchý textový podpis."),
    "watermark": ("Vodoznak", "Přidejte textový vodoznak na každou stránku."),
    "rotate": ("Otočit PDF", "Otočte všechny stránky o 90, 180 nebo 270 stupňů."),
    "html-pdf": ("HTML do PDF", "Převeďte nahraný soubor HTML do PDF."),
    "unlock": ("Odemknout PDF", "Odstraňte ochranu PDF, pokud znáte heslo."),
    "protect": ("Chránit PDF", "Zašifrujte PDF pomocí hesla."),
    "organize": ("Uspořádat PDF", "Změňte pořadí stran například pomocí seznamu 3,1,2."),
    "pdfa": ("PDF do PDF/A", "Vytvořte standardizovanou archivní kopii PDF/A-2b."),
    "repair": ("Opravit PDF", "Přepište poškozené, ale čitelné PDF do nového souboru."),
    "page-numbers": ("Čísla stránek", "Přidejte čísla na každou stránku PDF."),
    "scan-pdf": ("Sken do PDF", "Převeďte fotografie nebo skeny z telefonu do PDF."),
    "ocr": ("OCR PDF", "Rozpoznejte text z naskenovaných PDF ve více jazycích."),
    "compare": ("Porovnat PDF", "Vytvořte přehled textových rozdílů mezi dvěma PDF."),
    "redact": ("Začernit PDF", "Vyhledejte a trvale začerněte zadaný text."),
    "crop": ("Oříznout PDF", "Ořízněte okraje všech stránek v milimetrech."),
    "ask-octo": ("Ask Octo AI", "Shrňte PDF a položte tři otázky s odkazy na stránky."),
}

CS_HOME_REPLACEMENTS = {
    '<html lang="en">': '<html lang="cs" data-default-language="cs">',
    '<meta name="description" content="Convert, merge, split, compress, sign, protect and OCR PDF files online with PDFaspect."/>': '<meta name="description" content="Převádějte, slučujte, rozdělujte, komprimujte, podepisujte, chraňte a rozpoznávejte PDF online s PDFaspect."/>',
    '<link rel="canonical" href="https://pdfaspect.com/"/>': '<link rel="canonical" href="https://pdfaspect.com/cs/"/>',
    '<meta property="og:type" content="website"/><meta property="og:url" content="https://pdfaspect.com/"/>': '<meta property="og:type" content="website"/><meta property="og:url" content="https://pdfaspect.com/cs/"/>',
    '<meta property="og:title" content="PDFaspect — online PDF tools"/>': '<meta property="og:title" content="PDFaspect — online PDF nástroje"/>',
    '<meta property="og:description" content="27 simple PDF tools for conversion, editing, OCR and document management."/>': '<meta property="og:description" content="27 jednoduchých PDF nástrojů pro převod, úpravy, OCR a správu dokumentů."/>',
    '<title>PDFaspect — PDF tools</title>': '<title>PDFaspect — PDF nástroje</title>',
    '>PDF tools</a>': '>PDF nástroje</a>',
    '>Pricing</a>': '>Ceník</a>',
    '>Privacy</a>': '>Soukromí</a>',
    '>Language</span>': '>Jazyk</span>',
    '>Sign in</button>': '>Přihlásit se</button>',
    '>Fast • private • simple</div>': '>Rychlé • soukromé • jednoduché</div>',
    '>Every PDF tool you need.</span>': '>Všechny PDF nástroje, které potřebujete.</span>',
    '>Simple and secure.</span>': '>Snadno a bezpečně.</span>',
    '>Convert, organize, compress, sign, protect, and OCR documents directly in your browser.</p>': '>Převádějte, organizujte, komprimujte, podepisujte, chraňte a rozpoznávejte dokumenty přímo v prohlížeči.</p>',
    '>27 useful tools</span>': '>27 užitečných nástrojů</span>',
    '>Files deleted automatically</span>': '>Soubory mažeme automaticky</span>',
    '>No account for Free</span>': '>Zdarma bez účtu</span>',
    '>All PDF tools</h2>': '>Všechny PDF nástroje</h2>',
    '>Every tool is listed here. Pick one and process your document.</p>': '>Všechny nástroje najdete zde. Vyberte si a zpracujte dokument.</p>',
    'placeholder="Search all 27 tools…"': 'placeholder="Hledat ve 27 nástrojích…"',
}


def _render_home(language: str = "en") -> str:
    page = (BASE / "static" / "index.html").read_text(encoding="utf-8")
    if language == "cs":
        for source, replacement in CS_HOME_REPLACEMENTS.items():
            page = page.replace(source, replacement)
    cards = "".join(
        f'<a class="card" href="/tools/{escape(tool_id)}"><div class="icon">PDF</div>'
        f'<h3>{escape(TOOLS_CS.get(tool_id, (name, description))[0] if language == "cs" else name)}</h3>'
        f'<p>{escape(TOOLS_CS.get(tool_id, (name, description))[1] if language == "cs" else description)}</p></a>'
        for tool_id, name, description in TOOLS
    )
    return page.replace("<!-- TOOL_CARDS -->", cards)


@app.get("/", response_class=HTMLResponse)
def home():
    return _render_home()


@app.get("/cs/", response_class=HTMLResponse)
def home_cs():
    return _render_home("cs")


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return (BASE / "static" / "terms.html").read_text(encoding="utf-8")


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return (BASE / "static" / "privacy.html").read_text(encoding="utf-8")


@app.get("/analytics", response_class=HTMLResponse)
def analytics_dashboard():
    return (BASE / "static" / "analytics-dashboard.html").read_text(encoding="utf-8")


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nAllow: /\nSitemap: https://pdfaspect.com/sitemap.xml\n"


@app.get("/sitemap.xml")
def sitemap():
    paths = ["", "cs/", "terms", "privacy", *(f"tools/{tool_id}" for tool_id, _, _ in TOOLS)]
    urls = "".join(
        f"<url><loc>https://pdfaspect.com/{path}</loc><lastmod>2026-09-19</lastmod></url>"
        for path in paths
    )
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
        media_type="application/xml",
    )


@app.get("/tools/{tool_id}", response_class=HTMLResponse)
def tool_page(tool_id: str):
    tool = next((item for item in TOOLS if item[0] == tool_id), None)
    if not tool:
        raise HTTPException(404, "PDF tool not found.")
    _, name, description = tool
    title = escape(f"{name} online — PDFaspect")
    summary = escape(description)
    canonical = f"https://pdfaspect.com/tools/{escape(tool_id)}"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<meta name="description" content="{summary}"><link rel="canonical" href="{canonical}">
<link rel="icon" href="/static/assets/octopus-logo.png"><link rel="stylesheet" href="/static/style.css"></head>
<body class="legal-page"><header class="topbar legal-topbar"><a class="brand" href="/">PDF<span>aspect</span></a>
<a class="ghost" href="/#tools">All PDF tools</a></header><main class="legal-shell"><div class="legal-hero">
<div class="pill">Online PDF tool</div><h1>{escape(name)}</h1><p>{summary}</p>
<a class="primary tool-page-action" href="/?tool={escape(tool_id)}#tools">Open {escape(name)}</a></div></main>
<footer><span>© 2026 PDFaspect</span><span class="footer-links"><a href="/terms">Terms</a><a href="/privacy">Privacy</a></span></footer>
<script src="/static/analytics.js"></script></body></html>"""


@app.get("/api/tools")
def tools():
    return [
        {
            "id": tool_id,
            "name": name,
            "description": description,
            "pro": tool_id == "ask-octo",
        }
        for tool_id, name, description in TOOLS
    ]


class AuthCredentials(BaseModel):
    email: str
    password: str


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirm(BaseModel):
    token: str
    password: str


class CheckoutPlan(BaseModel):
    plan: str
    accepted_terms: bool = False


class AnalyticsEvent(BaseModel):
    event: str
    path: str = "/"
    referrer: str = ""
    tool: str = ""


def _analytics_source(referrer: str) -> str:
    try:
        hostname = (urlparse(referrer).hostname or "").lower()
    except ValueError:
        hostname = ""
    if not hostname:
        return "direct"
    if hostname == "pdfaspect.com" or hostname.endswith(".pdfaspect.com"):
        return "internal"
    sources = {
        "google": ("google.",),
        "bing": ("bing.com",),
        "seznam": ("seznam.cz",),
        "duckduckgo": ("duckduckgo.com",),
        "yahoo": ("yahoo.",),
        "facebook": ("facebook.com", "m.me"),
        "instagram": ("instagram.com",),
        "tiktok": ("tiktok.com",),
        "youtube": ("youtube.com", "youtu.be"),
        "reddit": ("reddit.com",),
        "x": ("x.com", "twitter.com"),
        "linkedin": ("linkedin.com",),
    }
    for source, fragments in sources.items():
        if any(fragment in hostname for fragment in fragments):
            return source
    return hostname[:80]


def _analytics_visitor_hash(request: Request) -> str:
    day = ANALYTICS_STORE.day()
    opaque_network_key = anonymous_quota_key(
        f"{_client_address(request)}|{request.headers.get('user-agent', '')[:300]}"
    )
    return hashlib.sha256(f"{day}|{opaque_network_key}".encode("utf-8")).hexdigest()


def _record_system_event(event: str, tool_id: str = "") -> None:
    try:
        ANALYTICS_STORE.record(event, tool_id=tool_id)
    except Exception:
        pass


@app.post("/api/analytics/event", status_code=204)
def record_analytics_event(payload: AnalyticsEvent, request: Request):
    _rate_limit(request, "analytics", 120, 60)
    allowed_events = {"page_view", "tool_open", "tool_submit"}
    if payload.event not in allowed_events:
        raise HTTPException(400, "Unknown analytics event.")
    tool_ids = {tool_id for tool_id, _, _ in TOOLS}
    tool_id = payload.tool.strip().lower() if payload.tool.strip().lower() in tool_ids else ""
    path = "/" + payload.path.lstrip("/").split("?", 1)[0][:180]
    try:
        ANALYTICS_STORE.record(
            payload.event,
            visitor_hash=_analytics_visitor_hash(request),
            path=path,
            source=_analytics_source(payload.referrer),
            tool_id=tool_id,
        )
    except Exception:
        return Response(status_code=204)
    return Response(status_code=204)


@app.get("/api/analytics/summary")
def analytics_summary(request: Request, days: int = 30):
    user = AUTH_STORE.user_for_session(request.cookies.get(AUTH_COOKIE))
    if not user:
        raise HTTPException(401, "Sign in to view analytics.")
    if user.email.lower() not in ANALYTICS_ADMIN_EMAILS:
        raise HTTPException(403, "This account cannot view analytics.")
    return ANALYTICS_STORE.summary(days)


def _auth_response(user, token: str, request: Request, background=None) -> JSONResponse:
    billing = AUTH_STORE.billing_for_user(user.id)
    response = JSONResponse(
        {
            "authenticated": True,
            "email": user.email,
            "plan": AUTH_STORE.plan_for_user(user),
            "billing_managed": bool(billing and billing.stripe_customer_id),
            "email_verified": AUTH_STORE.email_is_verified(user.id),
        },
        background=background,
    )
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    secure = request.url.scheme == "https" or forwarded_proto.split(",")[0].strip() == "https"
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return response


def _password_reset_email_configured() -> bool:
    api_key = (
        os.getenv("RESEND_API_KEY", "").strip()
        or os.getenv("PAPERMINT_SMTP_PASSWORD", "").strip()
    )
    sender = os.getenv("PAPERMINT_SMTP_FROM", "").strip()
    return bool(api_key and sender)


def _send_email_message(recipient: str, subject: str, body: str) -> None:
    api_key = (
        os.getenv("RESEND_API_KEY", "").strip()
        or os.getenv("PAPERMINT_SMTP_PASSWORD", "").strip()
    )
    sender = os.getenv("PAPERMINT_SMTP_FROM", "").strip()
    if not api_key or not sender:
        raise RuntimeError("Transactional email is not configured.")

    payload = json.dumps(
        {
            "from": sender,
            "to": [recipient],
            "subject": subject,
            "text": body,
        }
    ).encode("utf-8")
    request = UrlRequest(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "PDFaspect/1.0",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Resend returned HTTP {response.status}.")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Resend returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Resend: {exc.reason}") from exc


def _safe_send_email(recipient: str, subject: str, body: str) -> None:
    try:
        _send_email_message(recipient, subject, body)
    except Exception as exc:
        print(f"Transactional email delivery failed: {type(exc).__name__}: {exc}", flush=True)


def _send_password_reset_email(recipient: str, reset_url: str) -> None:
    _safe_send_email(
        recipient,
        "Reset your PDFaspect password",
        "We received a request to reset your PDFaspect password.\n\n"
        f"Open this link within 60 minutes:\n{reset_url}\n\n"
        "If you did not request a password reset, you can ignore this email.",
    )


def _send_verification_email(recipient: str, verify_url: str) -> None:
    _safe_send_email(
        recipient,
        "Verify your PDFaspect email",
        "Welcome to PDFaspect.\n\n"
        "Please verify that this email address belongs to you by opening this link:\n"
        f"{verify_url}\n\n"
        "This link expires in 24 hours. If you did not create a PDFaspect account, "
        "you can ignore this email.",
    )


def _subscription_terms_text() -> str:
    """Return the current Terms page as readable plain text for durable email confirmation."""
    try:
        html_text = (BASE / "static" / "terms.html").read_text(encoding="utf-8")
        match = re.search(
            r'<article class="legal-content">(.*?)</article>',
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        body = match.group(1) if match else html_text
        body = re.sub(r"<br\s*/?>", "\n", body, flags=re.IGNORECASE)
        body = re.sub(r"</(?:p|li|h1|h2|h3|section|ol|ul|div)>", "\n", body, flags=re.IGNORECASE)
        body = re.sub(r"<[^>]+>", "", body)
        body = unescape(body)
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n\s*\n+", "\n\n", body)
        return body.strip()
    except Exception:
        return "Current Terms & Conditions: https://pdfaspect.com/terms"


def _format_checkout_amount(amount_total, currency: str) -> str:
    if amount_total is None:
        return "See your Stripe receipt for the final charged amount."
    try:
        amount = int(amount_total) / 100
    except (TypeError, ValueError):
        return "See your Stripe receipt for the final charged amount."
    code = (currency or "eur").upper()
    symbol = "€" if code == "EUR" else f"{code} "
    return f"{symbol}{amount:.2f}"


def _send_subscription_confirmation_email(
    recipient: str,
    *,
    plan: str,
    amount_total=None,
    currency: str = "eur",
    terms_version: str = "2026-09-15",
) -> None:
    plan_label = "Monthly — €7 / month" if plan == "monthly" else "Yearly — €60 / year"
    amount_label = _format_checkout_amount(amount_total, currency)
    terms_text = _subscription_terms_text()
    _safe_send_email(
        recipient,
        "Your PDFaspect PRO subscription is active",
        "Thank you for subscribing to PDFaspect PRO.\n\n"
        f"Plan: {plan_label}\n"
        f"Amount charged today: {amount_label}\n"
        "Renewal: automatic for the same billing period until you cancel renewal.\n"
        "You can manage or cancel renewal from your PDFaspect account.\n\n"
        "CONSENT CONFIRMATION\n"
        "During checkout you actively accepted the Terms & Conditions, requested "
        "immediate access to the paid digital service after payment, and acknowledged "
        "the withdrawal information shown at checkout. This email confirms that choice; "
        "it does not ask you to consent again.\n\n"
        f"Terms version accepted: {terms_version}\n"
        "Online copy: https://pdfaspect.com/terms\n\n"
        "TERMS & CONDITIONS IN FORCE AT PURCHASE\n"
        "---------------------------------------\n"
        f"{terms_text}\n\n"
        "Support: pdfaspect@gmail.com",
    )


@app.post("/api/auth/password-reset/request")
def request_password_reset(payload: PasswordResetRequest, request: Request):
    _rate_limit(request, "password-reset-request", 5, 60 * 60)
    if not _password_reset_email_configured():
        raise HTTPException(503, "Password reset email is not configured yet.")

    try:
        token = AUTH_STORE.create_password_reset(payload.email, ttl_seconds=60 * 60)
    except AuthError:
        token = None

    background = None
    if token:
        reset_url = f"{_public_url(request)}/?reset_token={token}#account"
        background = BackgroundTask(
            _send_password_reset_email,
            payload.email.strip().lower(),
            reset_url,
        )

    return JSONResponse(
        {
            "ok": True,
            "message": "If an account exists for that email, a reset link has been sent.",
        },
        background=background,
    )


@app.post("/api/auth/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, request: Request):
    _rate_limit(request, "password-reset-confirm", 10, 60 * 60)
    try:
        AUTH_STORE.reset_password(payload.token, payload.password)
    except AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "message": "Your password has been changed. You can sign in now."}


@app.post("/api/auth/register")
def register_account(credentials: AuthCredentials, request: Request):
    _rate_limit(request, "register", 5, 15 * 60)
    try:
        user = AUTH_STORE.register(credentials.email, credentials.password)
        token = AUTH_STORE.create_session(user.id)
        _record_system_event("registration")
    except AuthError as exc:
        raise HTTPException(400, str(exc)) from exc

    background = None
    if _password_reset_email_configured():
        try:
            verify_token = AUTH_STORE.create_email_verification(
                user.id, ttl_seconds=24 * 60 * 60
            )
            verify_url = f"{_public_url(request)}/api/auth/verify-email?token={verify_token}"
            background = BackgroundTask(_send_verification_email, user.email, verify_url)
        except Exception as exc:
            print(f"Could not prepare verification email: {type(exc).__name__}: {exc}", flush=True)

    return _auth_response(user, token, request, background=background)


@app.get("/api/auth/verify-email", response_class=HTMLResponse)
def verify_email(token: str):
    try:
        user = AUTH_STORE.verify_email(token)
    except AuthError as exc:
        return HTMLResponse(
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Email verification</title></head><body>"
            "<h1>Verification link is invalid or expired</h1>"
            "<p>Please return to PDFaspect and request a new verification email.</p>"
            "<p><a href='/#account'>Back to PDFaspect</a></p>"
            "</body></html>",
            status_code=400,
        )
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Email verified</title></head><body>"
        "<h1>Email verified</h1>"
        f"<p>{escape(user.email)} has been verified successfully.</p>"
        "<p><a href='/#account'>Continue to PDFaspect</a></p>"
        "</body></html>"
    )


@app.post("/api/auth/login")
def login_account(credentials: AuthCredentials, request: Request):
    _rate_limit(request, "login", 10, 15 * 60)
    try:
        user = AUTH_STORE.authenticate(credentials.email, credentials.password)
        token = AUTH_STORE.create_session(user.id)
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    return _auth_response(user, token, request)


@app.get("/api/auth/me")
def current_account(request: Request):
    user = AUTH_STORE.user_for_session(request.cookies.get(AUTH_COOKIE))
    if not user:
        return {"authenticated": False}
    billing = AUTH_STORE.billing_for_user(user.id)
    return {
        "authenticated": True,
        "email": user.email,
        "plan": AUTH_STORE.plan_for_user(user),
        "billing_managed": bool(billing and billing.stripe_customer_id),
        "email_verified": AUTH_STORE.email_is_verified(user.id),
    }


@app.post("/api/auth/logout")
def logout_account(request: Request):
    AUTH_STORE.delete_session(request.cookies.get(AUTH_COOKIE))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(AUTH_COOKIE, path="/", samesite="lax")
    return response


def _signed_in_user(request: Request):
    user = AUTH_STORE.user_for_session(request.cookies.get(AUTH_COOKIE))
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    return user


@app.post("/api/auth/verify-email/resend")
def resend_verification_email(request: Request):
    _rate_limit(request, "verify-email-resend", 5, 60 * 60)
    user = _signed_in_user(request)
    if AUTH_STORE.email_is_verified(user.id):
        return {"ok": True, "message": "Your email is already verified."}
    if not _password_reset_email_configured():
        raise HTTPException(503, "Verification email is not configured yet.")

    verify_token = AUTH_STORE.create_email_verification(
        user.id, ttl_seconds=24 * 60 * 60
    )
    verify_url = f"{_public_url(request)}/api/auth/verify-email?token={verify_token}"
    return JSONResponse(
        {"ok": True, "message": "Verification email sent."},
        background=BackgroundTask(_send_verification_email, user.email, verify_url),
    )


def _public_url(request: Request) -> str:
    configured = os.getenv("PAPERMINT_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        if not configured.startswith(("https://", "http://")):
            raise HTTPException(500, "The public site URL is not configured correctly.")
        return configured
    return str(request.base_url).rstrip("/")


def _stripe_configured(plan: str | None = None) -> bool:
    if not STRIPE_SECRET_KEY or not AUTH_STORE.postgres:
        return False
    return bool(STRIPE_PRICE_IDS.get(plan, "")) if plan else all(STRIPE_PRICE_IDS.values())


def _stripe_value(value, key: str, default=None):
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _stripe_metadata(value) -> dict:
    metadata = _stripe_value(value, "metadata", {}) or {}
    return dict(metadata)


def _subscription_plan(subscription) -> str:
    """Resolve the plan from Stripe's Price ID, with metadata as fallback."""
    items = _stripe_value(_stripe_value(subscription, "items", {}), "data", []) or []
    if items:
        price_id = str(_stripe_value(_stripe_value(items[0], "price", {}), "id", "") or "")
        for plan, configured_price_id in STRIPE_PRICE_IDS.items():
            if configured_price_id and price_id == configured_price_id:
                return plan
    metadata_plan = str(_stripe_metadata(subscription).get("plan", "") or "")
    return metadata_plan if metadata_plan in {"monthly", "yearly"} else "free"


def _sync_stripe_subscription(subscription) -> None:
    subscription_id = str(_stripe_value(subscription, "id", "") or "")
    customer_id = str(_stripe_value(subscription, "customer", "") or "")
    metadata = _stripe_metadata(subscription)
    user_id = metadata.get("user_id") or AUTH_STORE.user_id_for_billing(
        customer_id=customer_id or None,
        subscription_id=subscription_id or None,
    )
    if not user_id:
        return

    status = str(_stripe_value(subscription, "status", "") or "")
    requested_plan = _subscription_plan(subscription)
    plan = requested_plan if status in {"active", "trialing"} else "free"
    period_end = _stripe_value(subscription, "current_period_end")
    AUTH_STORE.set_billing_customer(
        user_id,
        customer_id=customer_id or None,
        subscription_id=subscription_id or None,
    )
    AUTH_STORE.set_subscription(
        user_id,
        plan,
        int(period_end) if period_end is not None else None,
    )


@app.get("/api/billing/status")
def billing_status(request: Request):
    user = AUTH_STORE.user_for_session(request.cookies.get(AUTH_COOKIE))
    return {
        "configured": _stripe_configured(),
        "authenticated": bool(user),
        "plan": AUTH_STORE.plan_for_user(user) if user else "free",
        "portal_available": bool(user and AUTH_STORE.billing_for_user(user.id)),
    }


@app.post("/api/billing/checkout")
async def create_checkout(payload: CheckoutPlan, request: Request):
    _rate_limit(request, "checkout", 10, 10 * 60)
    user = _signed_in_user(request)
    plan = (payload.plan or "").strip().lower()
    if plan not in STRIPE_PRICE_IDS:
        raise HTTPException(400, "Choose a monthly or yearly plan.")
    if not payload.accepted_terms:
        raise HTTPException(400, "Accept the Terms & Conditions to continue.")
    if not _stripe_configured(plan):
        raise HTTPException(503, "Payments are not activated yet.")

    public_url = _public_url(request)
    billing = AUTH_STORE.billing_for_user(user.id)
    if AUTH_STORE.plan_for_user(user) == "pro" and billing and billing.stripe_customer_id:
        raise HTTPException(409, "This account already has a paid subscription. Manage it from your account.")
    accepted_at = str(int(time.time()))
    checkout_metadata = {
        "user_id": user.id,
        "plan": plan,
        "terms_version": "2026-09-15",
        "terms_accepted_at": accepted_at,
    }
    parameters = {
        "mode": "subscription",
        "line_items": [{"price": STRIPE_PRICE_IDS[plan], "quantity": 1}],
        "client_reference_id": user.id,
        "metadata": checkout_metadata,
        "subscription_data": {"metadata": checkout_metadata},
        "success_url": f"{public_url}/?checkout=success#pricing",
        "cancel_url": f"{public_url}/?checkout=cancelled#pricing",
        "allow_promotion_codes": True,
    }
    if billing and billing.stripe_customer_id:
        parameters["customer"] = billing.stripe_customer_id
    else:
        parameters["customer_email"] = user.email

    try:
        checkout = await run_in_threadpool(stripe.checkout.Session.create, **parameters)
    except stripe.StripeError as exc:
        raise HTTPException(502, "The secure payment page could not be opened.") from exc
    checkout_url = _stripe_value(checkout, "url")
    if not checkout_url:
        raise HTTPException(502, "The secure payment page did not return a link.")
    _record_system_event("checkout_started")
    return {"url": checkout_url}


@app.post("/api/billing/portal")
async def create_billing_portal(request: Request):
    user = _signed_in_user(request)
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Subscription management is not activated yet.")
    billing = AUTH_STORE.billing_for_user(user.id)
    if not billing or not billing.stripe_customer_id:
        raise HTTPException(404, "No paid subscription was found for this account.")
    try:
        portal = await run_in_threadpool(
            stripe.billing_portal.Session.create,
            customer=billing.stripe_customer_id,
            return_url=_public_url(request),
        )
    except stripe.StripeError as exc:
        raise HTTPException(502, "Subscription management could not be opened.") from exc
    return {"url": _stripe_value(portal, "url")}


@app.post("/api/billing/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "Stripe webhook is not configured.")
    signature = request.headers.get("stripe-signature")
    if not signature:
        raise HTTPException(400, "Missing Stripe signature.")
    try:
        event = stripe.Webhook.construct_event(
            await request.body(),
            signature,
            STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(400, "Invalid Stripe webhook.") from exc

    event_type = _stripe_value(event, "type", "")
    event_id = str(_stripe_value(event, "id", "") or "")
    if event_id and AUTH_STORE.stripe_event_processed(event_id):
        return {"received": True, "duplicate": True}
    event_data = _stripe_value(_stripe_value(event, "data", {}), "object", {})
    if event_type == "checkout.session.completed":
        metadata = _stripe_metadata(event_data)
        user_id = metadata.get("user_id") or _stripe_value(event_data, "client_reference_id")
        customer_id = _stripe_value(event_data, "customer")
        subscription_id = _stripe_value(event_data, "subscription")
        if user_id:
            AUTH_STORE.set_billing_customer(
                str(user_id),
                customer_id=str(customer_id) if customer_id else None,
                subscription_id=str(subscription_id) if subscription_id else None,
            )
        if subscription_id:
            try:
                subscription = await run_in_threadpool(
                    stripe.Subscription.retrieve,
                    str(subscription_id),
                )
                _sync_stripe_subscription(subscription)
                _record_system_event("subscription_started")
            except stripe.StripeError:
                # Stripe retries webhook deliveries; do not grant access without
                # confirmed subscription state.
                raise HTTPException(502, "Subscription state could not be confirmed.")

        if user_id:
            user = AUTH_STORE.user_for_id(str(user_id))
            if user:
                plan = metadata.get("plan") if metadata.get("plan") in {"monthly", "yearly"} else "monthly"
                terms_version = str(metadata.get("terms_version") or "2026-09-15")
                _send_subscription_confirmation_email(
                    user.email,
                    plan=plan,
                    amount_total=_stripe_value(event_data, "amount_total"),
                    currency=str(_stripe_value(event_data, "currency", "eur") or "eur"),
                    terms_version=terms_version,
                )
    elif event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:
        subscription_id = str(_stripe_value(event_data, "id", "") or "")
        try:
            current = await run_in_threadpool(stripe.Subscription.retrieve, subscription_id)
            _sync_stripe_subscription(current)
        except stripe.StripeError as exc:
            if event_type == "customer.subscription.deleted":
                _sync_stripe_subscription(event_data)
            else:
                raise HTTPException(502, "Subscription state could not be confirmed.") from exc
    if event_id:
        AUTH_STORE.mark_stripe_event_processed(event_id, str(event_type))
    return {"received": True}


class AskOctoQuestion(BaseModel):
    session_id: str
    question: str
    response_language: str = "en"


def _ask_octo_user(request: Request):
    user = AUTH_STORE.user_for_session(request.cookies.get(AUTH_COOKIE))
    if not user:
        raise HTTPException(401, "Sign in to use Ask Octo.")
    if AUTH_STORE.plan_for_user(user) != "pro":
        raise HTTPException(403, "Ask Octo is included with a paid plan.")
    return user


def _ai_language(code: str) -> str:
    language = AI_RESPONSE_LANGUAGES.get((code or "").strip().lower())
    if not language:
        raise HTTPException(400, "Choose a supported answer language.")
    return language


def _ai_usage_payload(user_id: str) -> dict:
    usage = AUTH_STORE.ai_usage(user_id)
    return {
        "period": usage.period,
        "documents_used": usage.documents,
        "documents_limit": AI_DOCUMENT_LIMIT,
        "questions_used": usage.questions,
        "questions_limit": AI_QUESTION_LIMIT,
    }


@app.get("/api/ai/status")
def ask_octo_status(request: Request):
    user = AUTH_STORE.user_for_session(request.cookies.get(AUTH_COOKIE))
    if not user:
        return {
            "authenticated": False,
            "plan": "free",
            "configured": ai_is_configured(),
        }
    return {
        "authenticated": True,
        "plan": AUTH_STORE.plan_for_user(user),
        "configured": ai_is_configured(),
        "usage": _ai_usage_payload(user.id),
    }


@app.post("/api/ai/document")
async def ask_octo_document(
    request: Request,
    file: UploadFile = File(...),
    ocr_language: str = Form("eng"),
    response_language: str = Form("en"),
):
    _rate_limit(request, "ai-document", 20, 60)
    user = _ask_octo_user(request)
    answer_language = _ai_language(response_language)
    if not ai_is_configured():
        raise HTTPException(503, "Ask Octo is not activated yet.")
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(400, "Ask Octo accepts one PDF file.")
    if ocr_language not in OCR_LANGUAGES:
        raise HTTPException(400, "Choose a supported document language.")

    source = save_upload(file)
    reserved = False
    try:
        extracted = await run_in_threadpool(extract_pdf_text, source, ocr_language)
        try:
            AUTH_STORE.consume_ai_usage(
                user.id,
                documents=1,
                document_limit=AI_DOCUMENT_LIMIT,
                question_limit=AI_QUESTION_LIMIT,
            )
            reserved = True
        except AuthError as exc:
            raise HTTPException(429, str(exc)) from exc

        summary = await run_in_threadpool(
            summarize_document,
            extracted.pages,
            answer_language,
        )
        session_id = AI_SESSIONS.create(user.id, extracted.pages)
        return {
            "session_id": session_id,
            "summary": summary,
            "pages": len(extracted.pages),
            "ocr_pages": extracted.ocr_pages,
            "questions_remaining": AI_QUESTIONS_PER_DOCUMENT,
            "usage": _ai_usage_payload(user.id),
        }
    except HTTPException:
        if reserved:
            AUTH_STORE.refund_ai_usage(user.id, documents=1)
        raise
    except AskOctoError as exc:
        if reserved:
            AUTH_STORE.refund_ai_usage(user.id, documents=1)
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        if reserved:
            AUTH_STORE.refund_ai_usage(user.id, documents=1)
        raise HTTPException(500, "Ask Octo could not process this PDF.") from exc
    finally:
        source.unlink(missing_ok=True)


@app.post("/api/ai/question")
async def ask_octo_question(payload: AskOctoQuestion, request: Request):
    _rate_limit(request, "ai-question", 30, 60)
    user = _ask_octo_user(request)
    answer_language = _ai_language(payload.response_language)
    if not ai_is_configured():
        raise HTTPException(503, "Ask Octo is not activated yet.")

    try:
        session = AI_SESSIONS.get(payload.session_id, user.id)
        question_number = AI_SESSIONS.reserve_question(payload.session_id, user.id)
    except AskOctoError as exc:
        raise HTTPException(400, str(exc)) from exc

    quota_reserved = False
    try:
        try:
            AUTH_STORE.consume_ai_usage(
                user.id,
                questions=1,
                document_limit=AI_DOCUMENT_LIMIT,
                question_limit=AI_QUESTION_LIMIT,
            )
            quota_reserved = True
        except AuthError as exc:
            raise HTTPException(429, str(exc)) from exc

        answer, source_pages = await run_in_threadpool(
            answer_question,
            session["pages"],
            payload.question,
            answer_language,
        )
        return {
            "answer": answer,
            "source_pages": source_pages,
            "questions_remaining": max(
                0,
                AI_QUESTIONS_PER_DOCUMENT - question_number,
            ),
            "usage": _ai_usage_payload(user.id),
        }
    except HTTPException:
        AI_SESSIONS.release_question(payload.session_id, user.id)
        if quota_reserved:
            AUTH_STORE.refund_ai_usage(user.id, questions=1)
        raise
    except AskOctoError as exc:
        AI_SESSIONS.release_question(payload.session_id, user.id)
        if quota_reserved:
            AUTH_STORE.refund_ai_usage(user.id, questions=1)
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        AI_SESSIONS.release_question(payload.session_id, user.id)
        if quota_reserved:
            AUTH_STORE.refund_ai_usage(user.id, questions=1)
        raise HTTPException(500, "Ask Octo could not answer this question.") from exc


# ============================================================
# V28 RUNTIME
# ============================================================

# These defaults can later be overridden in Render environment variables:
# PAPERMINT_WORKERS=1
# PAPERMINT_QUEUE_SIZE=6
# PAPERMINT_JOB_TIMEOUT=240
# PAPERMINT_MAX_INPUT_MB=50
# PAPERMINT_MAX_PAGES=300
# PAPERMINT_RETENTION_SECONDS=1800
PDF_WORD_POLICY = V28Policy.from_env()
PDF_WORD_MANAGER = V28JobManager(
    output_dir=PDF_WORD_OUTPUTS,
    policy=PDF_WORD_POLICY,
)

# We keep only lightweight web metadata here. The actual queue/status/output
# lifecycle lives inside V28JobManager.
PDF_WORD_META = {}
PDF_WORD_META_LOCK = threading.RLock()
JANITOR_STOP = threading.Event()
JANITOR_THREAD = None


def save_upload(
    upload: UploadFile,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    max_mb: int = MAX_UPLOAD_MB,
) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    path = TMP / f"upload-{threading.get_ident()}-{id(upload)}{suffix}"

    # Avoid an accidental name collision if the same request object id is reused.
    counter = 1
    while path.exists():
        path = TMP / f"upload-{threading.get_ident()}-{id(upload)}-{counter}{suffix}"
        counter += 1

    written = 0
    try:
        with path.open("wb") as stream:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        413,
                        f"Each uploaded file can be up to {max_mb} MB on your plan.",
                    )
                stream.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    return path


def _request_network_identifier(request: Request) -> str:
    # Render supplies the original visitor first, followed by its proxy hops.
    # The last address can change between requests and would reset Free usage.
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def _conversion_access(request: Request) -> tuple[str | None, int, int]:
    """Return quota key and upload limits; PRO users are not Free-limited."""
    user = AUTH_STORE.user_for_session(request.cookies.get(AUTH_COOKIE))
    if user and AUTH_STORE.plan_for_user(user) == "pro":
        return None, MAX_UPLOAD_BYTES, MAX_REQUEST_BYTES
    key = (
        account_quota_key(user.id)
        if user
        else anonymous_quota_key(_request_network_identifier(request))
    )
    return key, FREE_UPLOAD_BYTES, FREE_UPLOAD_BYTES


def _refund_free_task(quota_key: str | None) -> None:
    if not quota_key:
        return
    try:
        release_free_task(quota_key)
    except FreeLimitUnavailable:
        # Do not hide the original conversion/queue error from the user.
        pass


def _delete_paths(paths) -> None:
    for path in paths:
        if not path:
            continue
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


def cleanup_stale_temp_files() -> None:
    now = time.time()
    try:
        candidates = list(TMP.iterdir())
    except OSError:
        return

    for path in candidates:
        if not path.is_file():
            continue
        try:
            retention = (
                RESULT_RETENTION_SECONDS
                if path.name.startswith(RESULT_FILE_PREFIXES)
                else TEMP_RETENTION_SECONDS
            )
            if path.stat().st_mtime < now - retention:
                path.unlink(missing_ok=True)
        except OSError:
            pass


# ============================================================
# MERGE PDF API
# ============================================================


@app.post("/api/convert")
async def convert_tool(
    request: Request,
    tool: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    pages: str = Form(""),
    rotation: int = Form(90),
    password: str = Form(""),
    password_confirm: str = Form(""),
    text: str = Form(""),
    signature_page: int = Form(1),
    signature_x: float = Form(30),
    signature_y: float = Form(30),
    page_number_start: int = Form(1),
    page_number_position: str = Form("bottom-center"),
    page_number_format: str = Form("number"),
    page_number_skip_first: bool = Form(False),
    margin: float = Form(10.0),
    ocr_language: str = Form("eng"),
):
    _rate_limit(request, "convert", 30, 60)
    cleanup_stale_temp_files()

    if tool not in {
        "merge", "split", "compress", "word-pdf", "rotate", "organize",
        "protect", "unlock", "sign", "watermark", "page-numbers", "pdf-ppt",
        "pdf-excel", "pdf-jpg", "ppt-pdf", "excel-pdf", "jpg-pdf",
        "html-pdf", "pdfa", "repair", "scan-pdf", "ocr", "compare",
        "redact", "crop",
    }:
        raise HTTPException(400, "This tool is not available yet.")

    if tool == "merge":
        if len(files) < 2:
            raise HTTPException(400, "Please upload at least two PDF files.")
        if len(files) > 25:
            raise HTTPException(400, "You can merge up to 25 PDF files at once.")
    elif tool == "compare":
        if len(files) != 2:
            raise HTTPException(400, "Please upload exactly two PDF files to compare.")
    elif tool in {"jpg-pdf", "scan-pdf"}:
        if not files:
            raise HTTPException(400, "Please upload at least one image.")
        if len(files) > 100:
            raise HTTPException(400, "You can combine up to 100 images at once.")
    elif len(files) != 1:
        action = {
            "split": "split",
            "compress": "compress",
            "word-pdf": "convert",
            "rotate": "rotate",
            "organize": "organize",
            "protect": "protect",
            "unlock": "unlock",
            "sign": "sign",
            "watermark": "watermark",
            "page-numbers": "number",
            "pdf-ppt": "convert",
            "pdf-excel": "convert",
            "pdf-jpg": "convert",
            "ppt-pdf": "convert",
            "excel-pdf": "convert",
            "html-pdf": "convert",
            "pdfa": "convert",
            "repair": "repair",
            "ocr": "recognize",
            "redact": "redact",
            "crop": "crop",
        }[tool]
        file_kind = {
            "word-pdf": "Word file",
            "ppt-pdf": "PowerPoint file",
            "excel-pdf": "Excel file",
            "html-pdf": "HTML file",
        }.get(tool, "PDF file")
        raise HTTPException(400, f"Please upload exactly one {file_kind} to {action}.")

    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        if tool == "word-pdf":
            if suffix not in {".doc", ".docx"}:
                raise HTTPException(400, "Word to PDF accepts DOC and DOCX files only.")
        elif tool == "ppt-pdf":
            if suffix not in {".ppt", ".pptx"}:
                raise HTTPException(400, "PowerPoint to PDF accepts PPT and PPTX files only.")
        elif tool == "excel-pdf":
            if suffix not in {".xls", ".xlsx"}:
                raise HTTPException(400, "Excel to PDF accepts XLS and XLSX files only.")
        elif tool in {"jpg-pdf", "scan-pdf"}:
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}:
                raise HTTPException(400, "This tool accepts JPG, PNG, WebP and TIFF images.")
        elif tool == "html-pdf":
            if suffix not in {".html", ".htm"}:
                raise HTTPException(400, "HTML to PDF accepts HTML and HTM files only.")
        elif suffix != ".pdf":
            raise HTTPException(400, "This tool accepts PDF files only.")

    if tool == "protect":
        if password != password_confirm:
            raise HTTPException(400, "Passwords do not match.")
        if len(password) < 6:
            raise HTTPException(400, "Password must contain at least 6 characters.")
        if len(password) > 128:
            raise HTTPException(400, "Password can contain at most 128 characters.")
    elif tool == "unlock":
        if not password:
            raise HTTPException(400, "Please enter the PDF password.")
        if len(password) > 128:
            raise HTTPException(400, "Password can contain at most 128 characters.")
    elif tool == "sign":
        if not text.strip():
            raise HTTPException(400, "Please enter the signature text.")
        if len(text.strip()) > 200:
            raise HTTPException(400, "Signature text can contain at most 200 characters.")
        if signature_page < 1:
            raise HTTPException(400, "Page number must be at least 1.")
        if signature_x < 0 or signature_y < 0:
            raise HTTPException(400, "Signature position cannot be negative.")
    elif tool == "watermark":
        if not text.strip():
            raise HTTPException(400, "Please enter the watermark text.")
        if len(text.strip()) > 100:
            raise HTTPException(400, "Watermark text can contain at most 100 characters.")
    elif tool == "page-numbers":
        if page_number_start < 0 or page_number_start > 1_000_000:
            raise HTTPException(400, "Start number must be between 0 and 1,000,000.")
        if page_number_position not in {
            "top-left", "top-center", "top-right",
            "bottom-left", "bottom-center", "bottom-right",
        }:
            raise HTTPException(400, "Choose a valid page-number position.")
        if page_number_format not in {"number", "page", "page-total"}:
            raise HTTPException(400, "Choose a valid page-number format.")
    elif tool == "redact":
        if not text.strip():
            raise HTTPException(400, "Please enter the text to redact.")
        if len(text.strip()) > 200:
            raise HTTPException(400, "Redaction text can contain at most 200 characters.")
    elif tool == "crop":
        if margin < 0 or margin > 100:
            raise HTTPException(400, "Crop margin must be between 0 and 100 mm.")
    elif tool == "ocr":
        if ocr_language not in OCR_LANGUAGES:
            raise HTTPException(400, "Choose a supported OCR language.")

    quota_key, upload_limit_bytes, request_limit_bytes = _conversion_access(request)
    upload_limit_mb = FREE_UPLOAD_MB if quota_key else MAX_UPLOAD_MB
    request_limit_mb = FREE_UPLOAD_MB if quota_key else MAX_REQUEST_MB
    sources: List[Path] = []
    quota_reserved = False
    if tool == "merge":
        output = TMP / f"merged-{uuid.uuid4().hex}.pdf"
    elif tool == "split":
        output = TMP / f"split-{uuid.uuid4().hex}.zip"
    elif tool == "compress":
        output = TMP / f"compressed-{uuid.uuid4().hex}.pdf"
    elif tool == "rotate":
        output = TMP / f"rotated-{uuid.uuid4().hex}.pdf"
    elif tool == "organize":
        output = TMP / f"organized-{uuid.uuid4().hex}.pdf"
    elif tool == "protect":
        output = TMP / f"protected-{uuid.uuid4().hex}.pdf"
    elif tool == "unlock":
        output = TMP / f"unlocked-{uuid.uuid4().hex}.pdf"
    elif tool == "sign":
        output = TMP / f"signed-{uuid.uuid4().hex}.pdf"
    elif tool == "watermark":
        output = TMP / f"watermarked-{uuid.uuid4().hex}.pdf"
    elif tool == "page-numbers":
        output = TMP / f"numbered-{uuid.uuid4().hex}.pdf"
    elif tool == "pdf-ppt":
        output = TMP / f"pdf-ppt-{uuid.uuid4().hex}.pptx"
    elif tool == "pdf-excel":
        output = TMP / f"pdf-excel-{uuid.uuid4().hex}.xlsx"
    elif tool == "pdf-jpg":
        output = TMP / f"pdf-jpg-{uuid.uuid4().hex}.zip"
    elif tool == "ppt-pdf":
        output = TMP / f"ppt-pdf-{uuid.uuid4().hex}.pdf"
    elif tool == "excel-pdf":
        output = TMP / f"excel-pdf-{uuid.uuid4().hex}.pdf"
    elif tool in {"jpg-pdf", "scan-pdf"}:
        output = TMP / f"image-pdf-{uuid.uuid4().hex}.pdf"
    elif tool == "html-pdf":
        output = TMP / f"html-pdf-{uuid.uuid4().hex}.pdf"
    elif tool == "pdfa":
        output = TMP / f"pdfa-{uuid.uuid4().hex}.pdf"
    elif tool == "repair":
        output = TMP / f"repaired-{uuid.uuid4().hex}.pdf"
    elif tool == "ocr":
        output = TMP / f"ocr-{uuid.uuid4().hex}.docx"
    elif tool == "compare":
        output = TMP / f"comparison-{uuid.uuid4().hex}.txt"
    elif tool == "redact":
        output = TMP / f"redacted-{uuid.uuid4().hex}.pdf"
    elif tool == "crop":
        output = TMP / f"cropped-{uuid.uuid4().hex}.pdf"
    else:
        output = TMP / f"word-pdf-{uuid.uuid4().hex}.pdf"

    try:
        total_upload_bytes = 0
        for upload in files:
            source = save_upload(
                upload,
                max_bytes=upload_limit_bytes,
                max_mb=upload_limit_mb,
            )
            sources.append(source)
            total_upload_bytes += source.stat().st_size
            if total_upload_bytes > request_limit_bytes:
                raise HTTPException(
                    413,
                    f"The combined upload can be up to {request_limit_mb} MB on your plan.",
                )

        free_usage = None
        if quota_key:
            free_usage = await run_in_threadpool(reserve_free_task, quota_key)
            quota_reserved = True

        queued = await run_in_threadpool(
            enqueue_tool_job,
            tool,
            sources,
            output,
            pages=pages,
            rotation=rotation,
            password=password,
            signature_text=text,
            signature_page=signature_page,
            signature_x=signature_x,
            signature_y=signature_y,
            watermark_text=text,
            page_number_start=page_number_start,
            page_number_position=page_number_position,
            page_number_format=page_number_format,
            page_number_skip_first=page_number_skip_first,
            redaction_text=text,
            crop_margin=margin,
            ocr_language=ocr_language,
            free_quota_key=quota_key,
        )
        if free_usage:
            queued["free_usage"] = {
                "used": free_usage["used"],
                "limit": free_usage["limit"],
                "remaining": free_usage["remaining"],
            }
    except FreeLimitReached as exc:
        _delete_paths([*sources, output])
        raise HTTPException(429, str(exc))
    except FreeLimitUnavailable as exc:
        _delete_paths([*sources, output])
        raise HTTPException(503, str(exc))
    except QueueCapacityReached as exc:
        if quota_reserved:
            _refund_free_task(quota_key)
        _delete_paths([*sources, output])
        raise HTTPException(429, str(exc))
    except QueueUnavailable as exc:
        if quota_reserved:
            _refund_free_task(quota_key)
        _delete_paths([*sources, output])
        raise HTTPException(503, str(exc))
    except HTTPException:
        if quota_reserved:
            _refund_free_task(quota_key)
        _delete_paths([*sources, output])
        raise
    except Exception as exc:
        if quota_reserved:
            _refund_free_task(quota_key)
        _delete_paths([*sources, output])
        raise HTTPException(500, f"Could not queue the PDF operation: {exc}")

    return queued


@app.get("/api/jobs/status/{job_id}")
def queued_job_status(job_id: str):
    try:
        job = fetch_job(job_id)
        status = public_job_status(job)
    except KeyError:
        raise HTTPException(404, "Processing job not found.")
    except QueueUnavailable as exc:
        raise HTTPException(503, str(exc))

    if status["status"] == "error":
        kwargs = job.kwargs or {}
        _delete_paths([*(kwargs.get("sources") or []), kwargs.get("output")])

    return status


@app.get("/api/jobs/download/{job_id}")
def queued_job_download(job_id: str):
    try:
        job = fetch_job(job_id)
        status = public_job_status(job)
    except KeyError:
        raise HTTPException(404, "Processing job not found.")
    except QueueUnavailable as exc:
        raise HTTPException(503, str(exc))

    if status["status"] == "error":
        raise HTTPException(400, status.get("error") or "Processing failed.")
    if status["status"] != "done":
        raise HTTPException(409, "The document is not ready yet.")

    result = job.return_value(refresh=True)
    if not isinstance(result, dict) or not result.get("ok"):
        raise HTTPException(500, "The processing result is missing.")

    output = Path(result.get("output") or "")
    if not output.is_file():
        raise HTTPException(404, "The processed file has expired.")

    cleanup_paths = [*(result.get("sources") or []), output]
    return FileResponse(
        path=str(output),
        media_type=result.get("media_type") or "application/octet-stream",
        filename=result.get("download_name") or output.name,
        background=BackgroundTask(_delete_paths, cleanup_paths),
    )


def _public_status(internal_status: str) -> str:
    """Keep compatibility with the frontend we already had before V28."""
    mapping = {
        "queued": "queued",
        "running": "processing",
        "completed": "done",
        "failed": "error",
    }
    return mapping.get(internal_status, internal_status)


def _error_message(error) -> str | None:
    if not error:
        return None
    if isinstance(error, dict):
        return error.get("message") or error.get("error") or str(error)
    return str(error)


def _delete_source_for_job(job_id: str) -> None:
    with PDF_WORD_META_LOCK:
        meta = PDF_WORD_META.get(job_id)
        if not meta:
            return
        source = meta.get("source")
        if source:
            try:
                Path(source).unlink(missing_ok=True)
            except Exception:
                pass
            meta["source"] = None


def _release_pdf_word_quota(job_id: str) -> None:
    quota_key = None
    with PDF_WORD_META_LOCK:
        meta = PDF_WORD_META.get(job_id)
        if not meta or meta.get("free_quota_released"):
            return
        quota_key = meta.get("free_quota_key")
        if not quota_key:
            return
        meta["free_quota_released"] = True
    try:
        release_free_task(quota_key)
    except FreeLimitUnavailable:
        # Let the janitor retry if Redis was briefly unavailable.
        with PDF_WORD_META_LOCK:
            meta = PDF_WORD_META.get(job_id)
            if meta:
                meta["free_quota_released"] = False


def cleanup_pdf_word_jobs() -> None:
    """Clean finished outputs in V28 and remove no-longer-needed input PDFs."""
    try:
        PDF_WORD_MANAGER.cleanup_expired()
    except Exception:
        pass

    with PDF_WORD_META_LOCK:
        job_ids = list(PDF_WORD_META.keys())

    for job_id in job_ids:
        try:
            status = PDF_WORD_MANAGER.status(job_id)
        except KeyError:
            # V28 already expired this job. Remove any remaining upload metadata/file.
            _delete_source_for_job(job_id)
            with PDF_WORD_META_LOCK:
                PDF_WORD_META.pop(job_id, None)
            continue
        except Exception:
            continue

        if status.get("status") == "failed":
            _release_pdf_word_quota(job_id)
        if status.get("status") in {"completed", "failed"}:
            _delete_source_for_job(job_id)


def cleanup_janitor_loop() -> None:
    while not JANITOR_STOP.wait(JANITOR_INTERVAL_SECONDS):
        cleanup_stale_temp_files()
        cleanup_pdf_word_jobs()


@app.on_event("startup")
def start_cleanup_janitor():
    global JANITOR_THREAD
    cleanup_stale_temp_files()
    cleanup_pdf_word_jobs()
    JANITOR_STOP.clear()
    if JANITOR_THREAD is None or not JANITOR_THREAD.is_alive():
        JANITOR_THREAD = threading.Thread(
            target=cleanup_janitor_loop,
            name="papermint-file-janitor",
            daemon=True,
        )
        JANITOR_THREAD.start()


# ============================================================
# ASYNC PDF -> WORD API
# ============================================================


@app.post("/api/pdf-word/start")
async def pdf_word_start(request: Request, file: UploadFile = File(...)):
    _rate_limit(request, "pdf-word", 30, 60)
    cleanup_stale_temp_files()
    cleanup_pdf_word_jobs()

    if not file.filename:
        raise HTTPException(400, "No file uploaded.")

    if Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(400, "Please upload a PDF file.")

    quota_key, upload_limit_bytes, _ = _conversion_access(request)
    upload_limit_mb = FREE_UPLOAD_MB if quota_key else MAX_UPLOAD_MB
    source = save_upload(
        file,
        max_bytes=upload_limit_bytes,
        max_mb=upload_limit_mb,
    )
    quota_reserved = False
    free_usage = None

    try:
        if quota_key:
            free_usage = await run_in_threadpool(reserve_free_task, quota_key)
            quota_reserved = True
        # Production web requests use qa=False. V28 still performs preflight,
        # timeout protection, atomic publication and its final integrity check.
        job = PDF_WORD_MANAGER.submit(source, qa=False)

    except V28QueueFull as exc:
        if quota_reserved:
            _refund_free_task(quota_key)
        source.unlink(missing_ok=True)
        raise HTTPException(429, exc.message)

    except V28Rejected as exc:
        if quota_reserved:
            _refund_free_task(quota_key)
        source.unlink(missing_ok=True)
        raise HTTPException(400, exc.message)

    except V28Error as exc:
        if quota_reserved:
            _refund_free_task(quota_key)
        source.unlink(missing_ok=True)
        raise HTTPException(500, exc.message)

    except FreeLimitReached as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(429, str(exc))

    except FreeLimitUnavailable as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(503, str(exc))

    except Exception as exc:
        if quota_reserved:
            _refund_free_task(quota_key)
        source.unlink(missing_ok=True)
        raise HTTPException(500, f"Could not start conversion: {exc}")

    job_id = job["job_id"]
    original_stem = Path(file.filename).stem.strip() or "converted"

    with PDF_WORD_META_LOCK:
        PDF_WORD_META[job_id] = {
            "source": str(source),
            "download_name": f"{original_stem}.docx",
            "free_quota_key": quota_key,
            "free_quota_released": False,
        }

    response = {
        "job_id": job_id,
        "status": "queued",
        "queue_depth": job.get("queue_depth", 0),
        "pages": (job.get("preflight") or {}).get("pages"),
    }
    if free_usage:
        response["free_usage"] = {
            "used": free_usage["used"],
            "limit": free_usage["limit"],
            "remaining": free_usage["remaining"],
        }
    return response


@app.get("/api/pdf-word/status/{job_id}")
def pdf_word_status(job_id: str):
    cleanup_pdf_word_jobs()

    try:
        job = PDF_WORD_MANAGER.status(job_id)
    except KeyError:
        raise HTTPException(404, "Conversion job not found.")

    internal_status = job.get("status", "unknown")

    if internal_status in {"completed", "failed"}:
        if internal_status == "failed":
            _release_pdf_word_quota(job_id)
        _delete_source_for_job(job_id)

    return {
        "job_id": job_id,
        "status": _public_status(internal_status),
        "internal_status": internal_status,
        "error": _error_message(job.get("error")),
        "queue_depth": job.get("queue_depth", 0),
        "pages": (job.get("preflight") or {}).get("pages"),
    }


@app.get("/api/pdf-word/download/{job_id}")
def pdf_word_download(job_id: str):
    cleanup_pdf_word_jobs()

    try:
        status = PDF_WORD_MANAGER.status(job_id)
    except KeyError:
        raise HTTPException(404, "Conversion job not found.")

    internal_status = status.get("status")

    if internal_status == "failed":
        raise HTTPException(
            500,
            _error_message(status.get("error")) or "Conversion failed.",
        )

    if internal_status != "completed":
        raise HTTPException(409, "Conversion is not finished yet.")

    try:
        full_job = PDF_WORD_MANAGER.result(job_id)
    except KeyError:
        raise HTTPException(404, "Conversion job not found.")

    output = full_job.get("output")

    if not output:
        raise HTTPException(500, "Converted DOCX path is missing.")

    output_path = Path(output)

    if not output_path.exists():
        raise HTTPException(404, "Converted DOCX file was not found.")

    _delete_source_for_job(job_id)

    with PDF_WORD_META_LOCK:
        meta = PDF_WORD_META.get(job_id, {})
        download_name = meta.get("download_name") or "converted.docx"

    return FileResponse(
        path=str(output_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=download_name,
        background=BackgroundTask(_delete_paths, [output_path]),
    )


@app.on_event("shutdown")
def shutdown_pdf_word_manager():
    JANITOR_STOP.set()
    if JANITOR_THREAD is not None:
        JANITOR_THREAD.join(timeout=2.0)
    try:
        PDF_WORD_MANAGER.shutdown()
    except Exception:
        pass
