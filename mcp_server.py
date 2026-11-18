#!/usr/bin/env python3
"""
Server MCP per OpenWebUI (LLM locale) - versione 2.0
=====================================================

Espone tool via protocollo MCP "Streamable HTTP" sulla porta 8000.
L'endpoint e' montato sulla root "/" (non "/mcp") per evitare il bug di
redirect 307 -> 404 di OpenWebUI che rimuove lo slash finale dagli URL.

    URL da inserire in OpenWebUI:  http://<IP_CONTAINER>:8000/
    (se imposti MCP_API_KEY, in OpenWebUI scegli auth "Bearer" con la stessa chiave)

Gruppi di strumenti
-------------------
  * Generali     : data/ora, calcolatrice, meteo (per nome localita'), cambio valuta, memoria,
                   domande all'utente con opzioni o risposta libera
  * Web          : ricerca (Google -> DuckDuckGo -> Bing), notizie, lettura pagine/PDF,
                   ricerca immagini (con anteprime visibili al modello), screenshot, download
  * Workspace    : terminale (anche in background), lettura/scrittura/modifica/ricerca file, python
  * Home Assistant: ricerca entita', stato, storico, servizi, controllo con regole di sicurezza
  * Timer        : timer multipli e sveglie persistenti, con notifiche su Home Assistant
  * Musica       : riproduzione da YouTube stile Alexa (continua con brani simili),
                   su un media_player di Home Assistant o in locale con mpv
  * Calendario   : Google Calendar (lettura, creazione, modifica, eliminazione eventi)
  * Invii        : rubrica contatti, email (SMTP) e WhatsApp (OpenWA, WAHA o CallMeBot), sempre con conferma

Configurazione (variabili d'ambiente, oppure file ".env" accanto allo script)
------------------------------------------------------------------------------
  Generale
    MCP_HOST=0.0.0.0  MCP_PORT=8000
    MCP_API_KEY=                     # consigliato: protegge l'endpoint con "Authorization: Bearer <chiave>"
    ENABLE_ELICITATION=true          # domande con modulo interattivo se il client lo supporta (false = solo testo)
    DATA_DIR=./data                  # dati persistenti (timer, memoria, workspace, log)
    TIMEZONE=Europe/Rome
    DISABLED_TOOLS=                  # elenco separato da virgole di tool da nascondere
    ENABLE_TERMINAL=true  ENABLE_MUSIC=true  ENABLE_HASS=true  ENABLE_CALENDAR=true

  Web
    WEB_ALLOW_PRIVATE_URLS=false     # true = permette di leggere URL della rete locale (rischio SSRF)
    CHROMIUM_PATH=                   # opzionale: eseguibile Chromium da usare con Playwright
    BROWSER_PROXY=                   # opzionale: proxy per il browser (default: HTTPS_PROXY)
    METEO_DEFAULT_LOCALITA=          # es. "Priolo Gargallo"

  Home Assistant
    HASS_URL=http://192.168.178.48:8123
    HASS_TOKEN=<long-lived access token>     # MAI scriverlo nel codice
    HASS_READ_ONLY=false             # true = solo lettura
    HASS_ALLOWED_DOMAINS=            # domini controllabili (default: lista sicura interna)
    HASS_CONFIRM_DOMAINS=lock,alarm_control_panel   # richiedono conferma esplicita
    HASS_CONFIRM_ENTITIES=           # pattern (es. cover.garage*,switch.caldaia) che richiedono conferma
    HASS_BLOCKED_ENTITIES=           # pattern di entita' che l'agente non puo' controllare
    HASS_ALLOWED_ENTITIES=           # se valorizzato, solo queste entita' sono controllabili
    HASS_VERIFY_SSL=true

  Timer
    TIMER_NOTIFY_SERVICE=            # es. notify.mobile_app_pixel_8
    TIMER_TTS_ENTITY=                # es. tts.google_translate_it_it   (annuncio vocale)
    TIMER_TTS_PLAYER=                # es. media_player.cucina
    TIMER_HASS_SCRIPT=               # es. script.timer_scaduto (riceve variabili: messaggio, etichetta)
    TIMER_PERSISTENT_NOTIFICATION=true
    TIMER_LOCAL_SOUND=false          # suono locale (mpv/ffplay/paplay/aplay) alla scadenza

  Musica
    MUSIC_DEFAULT_DEVICE=locale      # "locale" (mpv) oppure entity_id, es. media_player.soggiorno
    MUSIC_MPV_AUDIO_DEVICE=          # opzionale, passato a mpv --audio-device
    MUSIC_MPV_AO=                    # opzionale, passato a mpv --ao (es. pulse, alsa)
    MUSIC_DEFAULT_VOLUME=60

  Email (SMTP; per Gmail: smtp.gmail.com, porta 587, "password per le app")
    SMTP_HOST=  SMTP_PORT=587  SMTP_USER=  SMTP_PASSWORD=  SMTP_FROM=  SMTP_NOME=Assistente

  WhatsApp
    WHATSAPP_BACKEND=                # "openwa" o "waha" (gateway self-hosted) oppure "callmebot" (solo al tuo numero)
    OPENWA_URL=http://IP:2785  OPENWA_API_KEY=  OPENWA_SESSION_ID=<id della sessione>
    WAHA_URL=http://IP:3000  WAHA_API_KEY=  WAHA_SESSION=default
    CALLMEBOT_APIKEY=  CALLMEBOT_PHONE=
    WHATSAPP_PREFISSO=39             # prefisso internazionale aggiunto ai numeri senza "+"

  Google Calendar (uno dei due metodi)
    GOOGLE_SERVICE_ACCOUNT_FILE=/percorso/service_account.json
        -> condividi il tuo calendario con l'email del service account ("Apporta modifiche agli eventi")
        -> GOOGLE_CALENDAR_IDS=tuoindirizzo@gmail.com
    GOOGLE_OAUTH_TOKEN_FILE=./data/google_token.json
        -> generalo con:  python mcp_server.py --google-auth client_secret.json
    GOOGLE_CALENDAR_IDS=primary      # uno o piu' ID separati da virgola (il primo e' quello di default)
    GOOGLE_DELEGATED_USER=           # solo Google Workspace con delega a livello di dominio

Dipendenze (pip):
    "mcp[cli]>=1.10,<2" uvicorn requests trafilatura playwright ddgs pillow pypdf
    yt-dlp ytmusicapi google-api-python-client google-auth google-auth-oauthlib python-dotenv
  poi:  playwright install --with-deps chromium
  musica in locale: apt install mpv ; YouTube richiede un runtime JS per yt-dlp (es. deno)
"""


import ast
import asyncio
import base64
import contextlib
import fnmatch
import functools
import hmac
import io
import ipaddress
import json
import logging
import math
import operator
import os
import re
import secrets
import mimetypes
import shutil
import signal
import smtplib
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import wave
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any, Literal, Optional, Union
from urllib.parse import quote, quote_plus, urljoin, urlparse, parse_qs
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ImageContent, TextContent, ToolAnnotations

# --- Dipendenze opzionali (il server parte anche se mancano) ----------------
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
except ImportError:  # pragma: no cover
    async_playwright = None

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

try:
    from PIL import Image as PILImage
except ImportError:  # pragma: no cover
    PILImage = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

try:
    from ytmusicapi import YTMusic
except ImportError:  # pragma: no cover
    YTMusic = None


# =============================================================================
# CONFIGURAZIONE
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "si", "sì", "on")


def _env_list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in _env(name, default).split(",") if x.strip()]


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


MCP_HOST = _env("MCP_HOST", "0.0.0.0")
MCP_PORT = _env_int("MCP_PORT", 8000)
MCP_API_KEY = _env("MCP_API_KEY")
# Moduli interattivi MCP ("elicitation"): richiedono un server con sessione e un client compatibile.
ENABLE_ELICITATION = _env_bool("ENABLE_ELICITATION", True)
ELICITATION_TIMEOUT = _env_int("ELICITATION_TIMEOUT", 300)

DATA_DIR = Path(_env("DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
WORKSPACE_DIR = Path(_env("WORKSPACE_DIR", str(DATA_DIR / "workspace"))).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

TZ_NAME = _env("TIMEZONE", "Europe/Rome")
try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:  # pragma: no cover - tzdata mancante
    TZ = timezone(timedelta(hours=1))

DISABLED_TOOLS = set(_env_list("DISABLED_TOOLS"))

# --- Web ---
WEB_ALLOW_PRIVATE_URLS = _env_bool("WEB_ALLOW_PRIVATE_URLS", False)
CHROMIUM_PATH = _env("CHROMIUM_PATH")
BROWSER_PROXY = _env("BROWSER_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
BROWSER_MAX_PAGES = _env_int("BROWSER_MAX_PAGES", 3)
WEB_CHUNK_CHARS = _env_int("WEB_CHUNK_CHARS", 8000)
METEO_DEFAULT_LOCALITA = _env("METEO_DEFAULT_LOCALITA")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FRANKFURTER_URLS = ["https://api.frankfurter.dev/v1", "https://api.frankfurter.app"]
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

# --- Home Assistant ---
HASS_URL = _env("HASS_URL", "http://192.168.178.48:8123").rstrip("/")
HASS_TOKEN = _env("HASS_TOKEN")
HASS_READ_ONLY = _env_bool("HASS_READ_ONLY", False)
HASS_VERIFY_SSL = _env_bool("HASS_VERIFY_SSL", True)
HASS_CACHE_TTL = _env_int("HASS_CACHE_TTL", 10)
_DEFAULT_HASS_DOMAINS = (
    "light,switch,fan,cover,climate,media_player,scene,script,input_boolean,input_number,"
    "input_select,input_text,input_button,input_datetime,button,vacuum,humidifier,water_heater,"
    "number,select,automation,notify,tts,persistent_notification,timer,counter,siren,valve,"
    "lawn_mower,todo,weather,lock,alarm_control_panel,remote,homeassistant,calendar"
)
HASS_ALLOWED_DOMAINS = set(_env_list("HASS_ALLOWED_DOMAINS", _DEFAULT_HASS_DOMAINS))
HASS_CONFIRM_DOMAINS = set(_env_list("HASS_CONFIRM_DOMAINS", "lock,alarm_control_panel"))
HASS_CONFIRM_ENTITIES = _env_list("HASS_CONFIRM_ENTITIES")
HASS_BLOCKED_ENTITIES = _env_list("HASS_BLOCKED_ENTITIES")
HASS_ALLOWED_ENTITIES = _env_list("HASS_ALLOWED_ENTITIES")

# --- Timer ---
TIMER_NOTIFY_SERVICE = _env("TIMER_NOTIFY_SERVICE")
TIMER_TTS_ENTITY = _env("TIMER_TTS_ENTITY")
TIMER_TTS_PLAYER = _env("TIMER_TTS_PLAYER")
TIMER_HASS_SCRIPT = _env("TIMER_HASS_SCRIPT")
TIMER_PERSISTENT_NOTIFICATION = _env_bool("TIMER_PERSISTENT_NOTIFICATION", True)
TIMER_LOCAL_SOUND = _env_bool("TIMER_LOCAL_SOUND", False)

# --- Musica ---
MUSIC_DEFAULT_DEVICE = _env("MUSIC_DEFAULT_DEVICE", "locale")
MUSIC_MPV_AUDIO_DEVICE = _env("MUSIC_MPV_AUDIO_DEVICE")
MUSIC_MPV_AO = _env("MUSIC_MPV_AO")
MUSIC_DEFAULT_VOLUME = _env_int("MUSIC_DEFAULT_VOLUME", 60)
MUSIC_START_TIMEOUT = _env_int("MUSIC_START_TIMEOUT", 45)

# --- Terminale ---
TERMINAL_MAX_OUTPUT = _env_int("TERMINAL_MAX_OUTPUT", 12000)
TERMINAL_MAX_TIMEOUT = _env_int("TERMINAL_MAX_TIMEOUT", 900)
DOWNLOAD_MAX_MB = _env_int("DOWNLOAD_MAX_MB", 200)

# --- Google Calendar ---
GOOGLE_SA_FILE = _env("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_TOKEN_FILE = _env("GOOGLE_OAUTH_TOKEN_FILE", str(DATA_DIR / "google_token.json"))
GOOGLE_CALENDAR_IDS = _env_list("GOOGLE_CALENDAR_IDS", "primary")
GOOGLE_DELEGATED_USER = _env("GOOGLE_DELEGATED_USER")
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# --- Email (SMTP) ---
SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = _env_int("SMTP_PORT", 587)
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
SMTP_FROM = _env("SMTP_FROM") or SMTP_USER
SMTP_NOME = _env("SMTP_NOME", "Assistente")
SMTP_STARTTLS = _env_bool("SMTP_STARTTLS", True)

# --- WhatsApp ---
WHATSAPP_BACKEND = _env("WHATSAPP_BACKEND").lower()
OPENWA_URL = _env("OPENWA_URL").rstrip("/")
OPENWA_API_KEY = _env("OPENWA_API_KEY")
OPENWA_SESSION_ID = _env("OPENWA_SESSION_ID")
WAHA_URL = _env("WAHA_URL").rstrip("/")
WAHA_API_KEY = _env("WAHA_API_KEY")
WAHA_SESSION = _env("WAHA_SESSION", "default")
CALLMEBOT_APIKEY = _env("CALLMEBOT_APIKEY")
CALLMEBOT_PHONE = _env("CALLMEBOT_PHONE")
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
WHATSAPP_PREFISSO = _env("WHATSAPP_PREFISSO", "39").lstrip("+")
ALLEGATI_MAX_MB = _env_int("ALLEGATI_MAX_MB", 20)

FEATURES = {
    "web": async_playwright is not None,
    "terminal": _env_bool("ENABLE_TERMINAL", True),
    "hass": _env_bool("ENABLE_HASS", True) and bool(HASS_TOKEN),
    "music": _env_bool("ENABLE_MUSIC", True) and yt_dlp is not None,
    "calendar": _env_bool("ENABLE_CALENDAR", True)
    and (bool(GOOGLE_SA_FILE and Path(GOOGLE_SA_FILE).is_file()) or Path(GOOGLE_TOKEN_FILE).is_file()),
    "email": _env_bool("ENABLE_EMAIL", True) and bool(SMTP_HOST and SMTP_FROM),
    "whatsapp": _env_bool("ENABLE_WHATSAPP", True) and (
        (WHATSAPP_BACKEND == "openwa" and bool(OPENWA_URL and OPENWA_SESSION_ID))
        or (WHATSAPP_BACKEND == "waha" and bool(WAHA_URL))
        or (WHATSAPP_BACKEND == "callmebot" and bool(CALLMEBOT_APIKEY and CALLMEBOT_PHONE))),
}

# =============================================================================
# LOGGING
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
for _n in ("uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "mcp.server.streamable_http_manager",
           "mcp.server.streamable_http", "mcp.server.lowlevel.server"):
    logging.getLogger(_n).setLevel(logging.WARNING)
logger = logging.getLogger("mcp-server")


class _FiltroChiusuraSessione(logging.Filter):
    """Nasconde l'errore innocuo che la libreria MCP registra quando un client chiude una sessione."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not (record.getMessage().startswith("Error in standalone SSE writer")
                    and type(exc).__name__ == "ClosedResourceError")


logging.getLogger("mcp.server.streamable_http").addFilter(_FiltroChiusuraSessione())


# =============================================================================
# SERVER MCP
# =============================================================================
ISTRUZIONI = """Sei un assistente con accesso a strumenti reali.
- Per informazioni attuali o incerte usa cerca_online e poi leggi_pagina_web sulle fonti migliori; cita le fonti.
- Per mostrare un'immagine all'utente scrivi nella risposta il markdown ![descrizione](url_immagine).
- Per Home Assistant: trova prima l'entity_id con hass_cerca_entita, poi leggi o controlla.
  Per serrature, allarmi, garage o cancelli chiedi SEMPRE conferma esplicita all'utente prima di usare conferma=true.
- Il workspace (terminale e file) e' una cartella dedicata: lavora li' dentro.
- Salva nella memoria le preferenze stabili dell'utente quando le comunica.
- Rispondi sempre in italiano, senza inserire parole di altre lingue."""

mcp = FastMCP(
    name="mcp-server",
    instructions=ISTRUZIONI,
    host=MCP_HOST,
    port=MCP_PORT,
    streamable_http_path="/",
    # Con elicitation (default) servono sessione e risposte in streaming, perche' il server deve poter
    # inviare una richiesta al client durante l'esecuzione di un tool. Con ENABLE_ELICITATION=false
    # ogni richiesta e' autonoma e la risposta e' un JSON semplice (modalita' piu' conservativa).
    stateless_http=not ENABLE_ELICITATION,
    json_response=not ENABLE_ELICITATION,
)

REGISTERED_TOOLS: list[str] = []


class UserError(Exception):
    """Errore 'previsto' da mostrare al modello senza traceback."""


def tool(
    name: str,
    description: str,
    *,
    title: Optional[str] = None,
    group: Optional[str] = None,
    read_only: Optional[bool] = None,
    destructive: Optional[bool] = None,
):
    """Registra un tool MCP con gestione uniforme degli errori e feature-flag."""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            try:
                return await fn(*args, **kwargs)
            except UserError as e:
                logger.info(f"[{name}] {e}")
                return f"⚠️ {e}"
            except Exception as e:
                logger.error(f"[{name}] errore: {e}\n{traceback.format_exc()}")
                return f"Errore durante l'esecuzione di '{name}': {type(e).__name__}: {e}"
            finally:
                logger.info(f"[{name}] completato in {time.monotonic() - t0:.2f}s")

        if name in DISABLED_TOOLS:
            logger.info(f"Tool '{name}' disabilitato da DISABLED_TOOLS")
            return wrapper
        if group and not FEATURES.get(group, True):
            return wrapper
        mcp.tool(
            name=name,
            title=title,
            description=description,
            annotations=ToolAnnotations(title=title, readOnlyHint=read_only, destructiveHint=destructive),
            structured_output=False,
        )(wrapper)
        REGISTERED_TOOLS.append(name)
        return wrapper

    return deco


# =============================================================================
# UTILITA' COMUNI
# =============================================================================
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
GIORNI_BREVI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def now() -> datetime:
    return datetime.now(TZ)


def tronca(testo: str, limite: int, coda: int = 0) -> str:
    """Tronca mantenendo inizio (e opzionalmente fine) del testo."""
    if len(testo) <= limite:
        return testo
    if coda:
        testa = limite - coda
        omessi = len(testo) - testa - coda
        return f"{testo[:testa]}\n\n[... {omessi} caratteri omessi ...]\n\n{testo[-coda:]}"
    return testo[:limite] + f"\n[... troncato, {len(testo) - limite} caratteri omessi ...]"


def senza_accenti(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def normalizza(s: str) -> str:
    """minuscolo, senza accenti, separatori -> spazio."""
    return re.sub(r"[\s_.\-/]+", " ", senza_accenti(s)).strip()


def fmt_durata(secondi: float) -> str:
    secondi = int(round(max(0, secondi)))
    g, r = divmod(secondi, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parti = []
    if g:
        parti.append(f"{g} g")
    if h:
        parti.append(f"{h} h")
    if m:
        parti.append(f"{m} min")
    if s or not parti:
        parti.append(f"{s} s")
    return " ".join(parti)


def fmt_dt(dt: datetime, con_giorno: bool = True) -> str:
    dt = dt.astimezone(TZ)
    base = dt.strftime("%d/%m/%Y %H:%M")
    return f"{GIORNI[dt.weekday()]} {base}" if con_giorno else base


def fa_quanto(dt: datetime) -> str:
    delta = (now() - dt).total_seconds()
    if delta < 0:
        return f"tra {fmt_durata(-delta)}"
    if delta < 60:
        return "pochi secondi fa"
    return f"{fmt_durata(delta - delta % 60)} fa"


class JsonStore:
    """Piccolo archivio JSON con scrittura atomica e thread-safe."""

    def __init__(self, path: Path, default: Any):
        self.path = path
        self.default = default
        self.lock = threading.RLock()

    def load(self) -> Any:
        with self.lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return json.loads(json.dumps(self.default))
            except Exception as e:
                logger.error(f"File {self.path} corrotto ({e}): ne salvo una copia e riparto vuoto")
                with contextlib.suppress(Exception):
                    self.path.rename(self.path.with_suffix(f".corrotto-{int(time.time())}"))
                return json.loads(json.dumps(self.default))

    def save(self, data: Any) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)


# --- Parsing di date/ore e durate in italiano --------------------------------
_RE_ORA = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?(?:[:.](\d{2}))?$")


def _parse_ora(s: str) -> Optional[tuple[int, int, int]]:
    s = s.strip().lower().replace("alle ", "").replace("ore ", "").strip()
    m = _RE_ORA.match(s)
    if not m:
        return None
    h, mi, se = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
    if h > 23 or mi > 59 or se > 59:
        return None
    return h, mi, se


def parse_data_ora(testo: str, *, riferimento: Optional[datetime] = None) -> tuple[datetime, bool]:
    """
    Interpreta date/ore in formati comuni. Ritorna (datetime aware, ha_orario).
    Esempi: "2026-11-18 15:30", "2026-11-18T15:30:00+01:00", "18/11/2026 15:30", "18/11",
            "oggi", "domani 9:00", "dopodomani alle 18", "venerdì 20:30", "15:00".
    """
    if not testo or not str(testo).strip():
        raise UserError("Data/ora mancante.")
    rif = (riferimento or now()).astimezone(TZ)
    s = str(testo).strip()

    # ISO 8601
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        ha_ora = bool(re.search(r"\d[T ]\d", s))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ), ha_ora
    except ValueError:
        pass

    low = senza_accenti(s).replace(",", " ").replace("'", " ")
    low = re.sub(r"\b(alle|all|ore|dalle|h)\b", " ", low)
    low = re.sub(r"\s+", " ", low).strip()

    giorno: Optional[date] = None
    resto = low
    relativi = {"oggi": 0, "stasera": 0, "stanotte": 0, "domani": 1, "dopodomani": 2, "ieri": -1}
    for parola, off in sorted(relativi.items(), key=lambda x: -len(x[0])):
        if resto.startswith(parola):
            giorno = rif.date() + timedelta(days=off)
            resto = resto[len(parola):].strip()
            break
    if giorno is None:
        nomi = [normalizza(g) for g in GIORNI]
        for i, nome in enumerate(nomi):
            m = re.match(rf"^(prossimo |prossima )?{nome[:3]}[a-z]*\b", resto)
            if m:
                diff = (i - rif.weekday()) % 7
                if diff == 0 and m.group(1):
                    diff = 7
                giorno = rif.date() + timedelta(days=diff)
                resto = resto[m.end():].strip()
                break
    if giorno is None:
        m = re.match(r"^(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b", resto)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else rif.year
            if y < 100:
                y += 2000
            try:
                giorno = date(y, mo, d)
            except ValueError as e:
                raise UserError(f"Data non valida '{testo}': {e}")
            if not m.group(3) and giorno < rif.date() - timedelta(days=1):
                giorno = giorno.replace(year=y + 1)
            resto = resto[m.end():].strip()

    ora = _parse_ora(resto) if resto else None
    if resto and ora is None:
        raise UserError(
            f"Non riesco a interpretare '{testo}'. Usa ad es. '2026-11-18 15:30', '18/11/2026 15:30', 'domani 9:00'."
        )
    if giorno is None and ora is None:
        raise UserError(f"Non riesco a interpretare '{testo}'.")
    if giorno is None:
        giorno = rif.date()
    h, mi, se = ora if ora else (0, 0, 0)
    return datetime(giorno.year, giorno.month, giorno.day, h, mi, se, tzinfo=TZ), ora is not None


_RE_DURATA = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(giorni|giorno|gg|g|d|ore|ora|hours|hour|hrs|hr|h|minuti|minuto|mins|min|m|"
    r"secondi|secondo|secs|sec|s)(?![a-z])",
    re.I,
)
_UNITA = {
    "giorni": 86400, "giorno": 86400, "gg": 86400, "g": 86400, "d": 86400,
    "ore": 3600, "ora": 3600, "hours": 3600, "hour": 3600, "hrs": 3600, "hr": 3600, "h": 3600,
    "minuti": 60, "minuto": 60, "mins": 60, "min": 60, "m": 60,
    "secondi": 1, "secondo": 1, "secs": 1, "sec": 1, "s": 1,
}


def parse_durata(testo: str) -> int:
    """'10m', '1h30m', '1 ora e 15 minuti', '90s', '00:05:00', 'mezz'ora', '25' (=minuti) -> secondi."""
    s = re.sub(r"\s+", " ", senza_accenti(testo).replace("'", " ")).strip()
    s = s.replace("mezz ora", "30 minuti").replace("mezzora", "30 minuti").replace("un quarto d ora", "15 minuti")
    s = re.sub(r"\b(un|una) (ora|minuto|secondo|giorno)\b", r"1 \2", s)
    s = re.sub(r"\bmezzo\b", "", s).strip()
    if re.fullmatch(r"\d+(?:[.,]\d+)?", s):
        return int(float(s.replace(",", ".")) * 60)
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", s)
    if m:
        if m.group(3) is not None:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        return int(m.group(1)) * 60 + int(m.group(2))
    totale = 0.0
    for num, unita in _RE_DURATA.findall(s):
        totale += float(num.replace(",", ".")) * _UNITA[unita.lower()]
    resto = _RE_DURATA.sub("", s)
    resto = re.sub(r"\b(e|and|per|di|tra|fra)\b|[,+]", "", resto).strip()
    if totale <= 0 or resto:
        raise UserError(f"Durata non valida: '{testo}'. Esempi: '10m', '1h30m', '45 secondi', '2 ore'.")
    return int(round(totale))


# --- Rete: controllo URL (anti-SSRF) e download sicuri --------------------------------
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"})
HTTP.mount("https://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0.5,
                                                     status_forcelist=(502, 503, 504),
                                                     allowed_methods=("GET", "HEAD"))))


@functools.lru_cache(maxsize=1024)
def _host_privato(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False  # non risolvibile localmente (es. dietro proxy): verra' gestito dalla richiesta
    for info in infos:
        with contextlib.suppress(ValueError):
            if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
                return True
    return False


def url_consentito(url: str) -> tuple[bool, str]:
    try:
        p = urlparse(url)
    except Exception:
        return False, "URL non valido"
    if p.scheme not in ("http", "https"):
        return False, f"schema '{p.scheme}' non consentito (solo http/https)"
    if not p.hostname:
        return False, "host mancante"
    if not WEB_ALLOW_PRIVATE_URLS and _host_privato(p.hostname):
        return False, "indirizzi di rete locale non consentiti (imposta WEB_ALLOW_PRIVATE_URLS=true per abilitarli)"
    return True, ""


def controlla_url(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    ok, motivo = url_consentito(url)
    if not ok:
        raise UserError(f"URL rifiutato ({url}): {motivo}")
    return url


def http_get(url: str, *, max_bytes: int = 10 * 1024 * 1024, timeout: float = 15,
             headers: Optional[dict] = None, verso_file: Optional[Path] = None) -> tuple[bytes, dict, str]:
    """GET con controllo SSRF su ogni redirect e limite di dimensione."""
    for _ in range(6):
        url = controlla_url(url)
        r = HTTP.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=False)
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            nuovo = r.headers.get("location")
            r.close()
            if not nuovo:
                raise UserError("Redirect senza destinazione")
            url = urljoin(url, nuovo)
            continue
        try:
            r.raise_for_status()
            dichiarati = int(r.headers.get("content-length") or 0)
            if dichiarati and dichiarati > max_bytes:
                raise UserError(f"File troppo grande ({dichiarati / 1e6:.1f} MB, limite {max_bytes / 1e6:.0f} MB)")
            letti = 0
            buf = io.BytesIO()
            fh = open(verso_file, "wb") if verso_file else None
            try:
                for chunk in r.iter_content(64 * 1024):
                    letti += len(chunk)
                    if letti > max_bytes:
                        raise UserError(f"File troppo grande (limite {max_bytes / 1e6:.0f} MB)")
                    (fh or buf).write(chunk)
            finally:
                if fh:
                    fh.close()
            return buf.getvalue(), dict(r.headers), url
        finally:
            r.close()
    raise UserError("Troppi redirect")


def immagine_a_contenuto(dati: bytes, max_lato: int = 768) -> ImageContent:
    """Ridimensiona e converte un'immagine in ImageContent JPEG (base64)."""
    if PILImage is None:
        raise UserError("Pillow non installato: impossibile elaborare immagini")
    with PILImage.open(io.BytesIO(dati)) as img:
        img.seek(0)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            sfondo = PILImage.new("RGB", img.size, (255, 255, 255))
            sfondo.paste(img, mask=img.split()[-1])
            img = sfondo
        else:
            img = img.convert("RGB")
        img.thumbnail((max_lato, max_lato))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=80, optimize=True)
    return ImageContent(type="image", data=base64.b64encode(out.getvalue()).decode(), mimeType="image/jpeg")


# =============================================================================
# DATA E ORA
# =============================================================================
@tool(
    "data_ora_corrente",
    title="Data e ora",
    description=(
        "Restituisce data, ora, giorno della settimana e numero di settimana correnti. "
        "Usalo quando l'utente chiede che ore sono, che giorno e' oggi o per qualsiasi calcolo che "
        "dipende dalla data attuale. Parametro opzionale fuso_orario IANA (es. 'America/New_York')."
    ),
    read_only=True,
)
async def data_ora_corrente(fuso_orario: Optional[str] = None) -> str:
    tz = TZ
    if fuso_orario:
        try:
            tz = ZoneInfo(fuso_orario.strip())
        except Exception:
            raise UserError(f"Fuso orario sconosciuto: '{fuso_orario}'. Usa un nome IANA, es. 'Europe/London'.")
    d = datetime.now(tz)
    offset = d.strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}"
    iso_y, iso_w, _ = d.isocalendar()
    return (
        f"{GIORNI[d.weekday()]} {d.day} {MESI[d.month - 1]} {d.year}, ore {d.strftime('%H:%M:%S')}\n"
        f"Fuso orario: {getattr(tz, 'key', str(tz))} ({offset})\n"
        f"ISO: {d.isoformat(timespec='seconds')} · settimana {iso_w} · giorno dell'anno {d.timetuple().tm_yday}"
    )


# =============================================================================
# CALCOLATRICE SICURA
# =============================================================================
def _pow_sicura(a, b):
    if isinstance(b, (int, float)) and abs(b) > 10000:
        raise ValueError("esponente troppo grande")
    if isinstance(a, int) and isinstance(b, int) and b > 0 and a not in (0, 1, -1) and b * math.log2(abs(a)) > 100000:
        raise ValueError("risultato troppo grande")
    return operator.pow(a, b)


def _mul_sicura(a, b):
    if isinstance(a, int) and isinstance(b, int) and a and b and \
            (abs(a).bit_length() + abs(b).bit_length()) > 200000:
        raise ValueError("risultato troppo grande")
    return operator.mul(a, b)


def _fattoriale(n):
    if not float(n).is_integer() or n < 0 or n > 1000:
        raise ValueError("fattoriale ammesso solo per interi 0..1000")
    return math.factorial(int(n))


_OPERATORI = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: _mul_sicura, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: _pow_sicura,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNZIONI = {
    "sqrt": math.sqrt, "radice": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "atan2": math.atan2, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "log": math.log, "ln": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil, "trunc": math.trunc,
    "factorial": _fattoriale, "fattoriale": _fattoriale, "gcd": math.gcd, "lcm": math.lcm,
    "hypot": math.hypot, "degrees": math.degrees, "radians": math.radians,
    "min": min, "max": max, "sum": lambda *a: sum(a), "media": lambda *a: sum(a) / len(a),
    "comb": math.comb, "perm": math.perm,
}
_COSTANTI = {"pi": math.pi, "pigreco": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}


def _valuta(nodo):
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)) and not isinstance(nodo.value, bool):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERATORI:
        return _OPERATORI[type(nodo.op)](_valuta(nodo.left), _valuta(nodo.right))
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERATORI:
        return _OPERATORI[type(nodo.op)](_valuta(nodo.operand))
    if isinstance(nodo, ast.Name) and nodo.id in _COSTANTI:
        return _COSTANTI[nodo.id]
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and nodo.func.id in _FUNZIONI \
            and not nodo.keywords and len(nodo.args) <= 50:
        return _FUNZIONI[nodo.func.id](*[_valuta(a) for a in nodo.args])
    raise ValueError(f"elemento non consentito: {ast.dump(nodo)[:60]}")


def calcola_espressione(espressione: str):
    expr = espressione.strip()
    if len(expr) > 500:
        raise ValueError("espressione troppo lunga")
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace("√", "sqrt").replace("π", "pi")
    # Percentuali: "200 * 15%" -> "200 * (15/100)" (il % seguito da cifra/nome resta modulo)
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(a-zA-Z])", r"(\1/100)", expr)
    return _valuta(ast.parse(expr, mode="eval").body)


@tool(
    "calcola",
    title="Calcolatrice",
    description=(
        "Esegue calcoli matematici esatti da un'espressione testuale. Supporta + - * / // % ** (o ^), "
        "parentesi, percentuali (es. '250*15%'), costanti pi ed e e funzioni: sqrt, cbrt, sin, cos, tan, "
        "asin, acos, atan, log(x[,base]), ln, log10, log2, exp, abs, round(x,n), floor, ceil, factorial, "
        "gcd, lcm, hypot, degrees, radians, min, max, sum, media, comb, perm. Gli angoli sono in radianti. "
        "Usalo SEMPRE quando serve un risultato numerico preciso."
    ),
    read_only=True,
)
async def calcola(espressione: str) -> str:
    try:
        r = calcola_espressione(espressione)
    except ZeroDivisionError:
        raise UserError("Divisione per zero.")
    except (ValueError, TypeError, SyntaxError, OverflowError) as e:
        raise UserError(f"Espressione non valida: {e}")
    if isinstance(r, float):
        if r.is_integer() and abs(r) < 1e15:
            testo = str(int(r))
        else:
            testo = format(r, ".12g")
    else:
        try:
            testo = str(r)
        except ValueError:
            testo = f"(numero con circa {int(r.bit_length() * 0.30103)} cifre)"
    return f"{espressione} = {testo}"


# =============================================================================
# BROWSER (Playwright) - sessione persistente, pagine parallele, anti-SSRF
# =============================================================================
class BrowserManager:
    def __init__(self) -> None:
        self._pw: Optional["Playwright"] = None
        self._browser: Optional["Browser"] = None
        self._context: Optional["BrowserContext"] = None
        self._lock = asyncio.Lock()
        self._sem: Optional[asyncio.Semaphore] = None
        self._stato_file = DATA_DIR / "browser_state.json"

    async def _avvia(self) -> "BrowserContext":
        if async_playwright is None:
            raise UserError("Playwright non installato (pip install playwright && playwright install chromium)")
        logger.info("Avvio sessione browser Playwright...")
        self._pw = await async_playwright().start()
        opzioni: dict[str, Any] = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox",
                     "--disable-dev-shm-usage", "--disable-gpu"],
        }
        if CHROMIUM_PATH:
            opzioni["executable_path"] = CHROMIUM_PATH
        if BROWSER_PROXY:
            bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
            opzioni["proxy"] = {"server": BROWSER_PROXY, **({"bypass": bypass} if bypass else {})}
        self._browser = await self._pw.chromium.launch(**opzioni)
        ctx_opts: dict[str, Any] = dict(
            user_agent=USER_AGENT, viewport={"width": 1366, "height": 900}, locale="it-IT",
            timezone_id=TZ_NAME, accept_downloads=False,
            extra_http_headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
        )
        if self._stato_file.is_file():
            ctx_opts["storage_state"] = str(self._stato_file)
        try:
            self._context = await self._browser.new_context(**ctx_opts)
        except Exception:
            ctx_opts.pop("storage_state", None)
            self._context = await self._browser.new_context(**ctx_opts)
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        await self._context.route("**/*", self._filtro_richieste)
        logger.info("Sessione browser pronta.")
        return self._context

    @staticmethod
    async def _filtro_richieste(route) -> None:
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
            return
        ok, motivo = await asyncio.to_thread(url_consentito, url)
        if not ok:
            logger.warning(f"Richiesta browser bloccata: {url} ({motivo})")
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    async def contesto(self) -> "BrowserContext":
        async with self._lock:
            if self._context is not None and self._browser is not None and self._browser.is_connected():
                return self._context
            await self._chiudi_interno(salva=False)
            return await self._avvia()

    @contextlib.asynccontextmanager
    async def pagina(self, blocca_media: bool = True):
        if self._sem is None:
            self._sem = asyncio.Semaphore(BROWSER_MAX_PAGES)
        async with self._sem:
            ctx = await self.contesto()
            page = await ctx.new_page()
            if blocca_media:
                async def _blocca(route):
                    if route.request.resource_type in ("media", "font"):
                        await route.abort()
                    else:
                        await route.fallback()
                await page.route("**/*", _blocca)
            try:
                yield page
            finally:
                with contextlib.suppress(Exception):
                    await page.close()

    async def _chiudi_interno(self, salva: bool = True) -> None:
        try:
            if self._context is not None:
                if salva:
                    with contextlib.suppress(Exception):
                        await self._context.storage_state(path=str(self._stato_file))
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()
        except Exception as e:
            logger.warning(f"Errore chiusura browser: {e}")
        finally:
            self._context = self._browser = self._pw = None

    async def chiudi(self) -> None:
        async with self._lock:
            await self._chiudi_interno(salva=True)


BROWSER = BrowserManager()


async def _accetta_cookie(page: "Page") -> None:
    """Chiude i banner cookie piu' comuni (Google, Bing, generici)."""
    selettori = [
        "#L2AGLb", "button:has-text('Accetta tutto')", "button:has-text('Accetta tutti')",
        "button:has-text('Accept all')", "#bnp_btn_accept", "button:has-text('Accetta')",
        "button:has-text('Accetto')", "button#onetrust-accept-btn-handler",
    ]
    for frame in [page.main_frame, *page.frames[1:4]]:
        for sel in selettori:
            try:
                btn = frame.locator(sel).first
                if await btn.is_visible(timeout=400):
                    await btn.click(timeout=1500)
                    await page.wait_for_timeout(500)
                    logger.info(f"Banner cookie chiuso ({sel})")
                    return
            except Exception:
                continue


# =============================================================================
# WEB: RICERCA
# =============================================================================
_JS_GOOGLE = """
() => {
  const out = []; const seen = new Set();
  document.querySelectorAll('#rso a h3, #search a h3').forEach(h3 => {
    const a = h3.closest('a');
    if (!a || !a.href || seen.has(a.href)) return;
    if (a.closest('#tads, #tadsb, #bottomads, [data-text-ad]')) return;
    const box = a.closest('div[data-rpos], div.MjjYud, div.g, div[data-hveid]') || a.parentElement;
    if (box && box.querySelector('[data-initq]')) return;
    let snip = '';
    const s = box && box.querySelector('div[data-sncf], .VwiC3b, [data-content-feature="1"], .IsZvec');
    if (s) snip = s.innerText;
    seen.add(a.href);
    out.push({url: a.href, titolo: h3.innerText, snippet: snip});
  });
  return out;
}
"""

_JS_BING = """
() => Array.from(document.querySelectorAll('#b_results > li.b_algo')).map(li => {
  const a = li.querySelector('h2 a');
  const p = li.querySelector('.b_caption p, p.b_lineclamp2, p.b_lineclamp3, p.b_lineclamp4, .b_algoSlug');
  return a ? {url: a.href, titolo: a.innerText, snippet: p ? p.innerText : ''} : null;
}).filter(Boolean)
"""


def _pulisci_url_risultato(url: str) -> str:
    """Rimuove i redirect di Google (/url?q=) e Bing (/ck/a?u=a1<base64>)."""
    try:
        p = urlparse(url)
        if "google." in p.netloc and p.path == "/url":
            return parse_qs(p.query).get("q", parse_qs(p.query).get("url", [url]))[0]
        if "bing.com" in p.netloc and p.path.startswith("/ck/"):
            u = parse_qs(p.query).get("u", [""])[0]
            if u.startswith("a1"):
                b = u[2:] + "=" * (-len(u[2:]) % 4)
                return base64.urlsafe_b64decode(b).decode("utf-8", "ignore")
    except Exception:
        pass
    return url


def _filtra_risultati(risultati: list[dict], n: int) -> list[dict]:
    visti, puliti = set(), []
    for r in risultati:
        url = _pulisci_url_risultato(r.get("url", ""))
        if not url.startswith("http"):
            continue
        if any(x in url for x in ("google.com/aclk", "googleadservices", "google.it/aclk",
                                   "bing.com/aclk", "/search?", "accounts.google")):
            continue
        chiave = url.split("#")[0].rstrip("/")
        if chiave in visti:
            continue
        visti.add(chiave)
        puliti.append({"url": url, "titolo": (r.get("titolo") or "").strip(),
                       "snippet": re.sub(r"\s+", " ", r.get("snippet") or "").strip()})
        if len(puliti) >= n:
            break
    return puliti


async def _cerca_google(query: str) -> list[dict]:
    async with BROWSER.pagina() as page:
        url = f"https://www.google.it/search?q={quote_plus(query)}&hl=it&gl=it"
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if "consent." in page.url:
            await _accetta_cookie(page)
            with contextlib.suppress(Exception):
                await page.wait_for_url("**/search**", timeout=8000)
        else:
            await _accetta_cookie(page)
        if "/sorry/" in page.url or await page.locator("#captcha-form, form#captcha").count():
            raise RuntimeError("Google ha mostrato un captcha")
        with contextlib.suppress(Exception):
            await page.wait_for_selector("#search, #rso", timeout=10000)
        return await page.evaluate(_JS_GOOGLE)


async def _cerca_bing(query: str) -> list[dict]:
    async with BROWSER.pagina() as page:
        await page.goto(f"https://www.bing.com/search?q={quote_plus(query)}&setlang=it&cc=IT",
                        wait_until="domcontentloaded", timeout=20000)
        await _accetta_cookie(page)
        with contextlib.suppress(Exception):
            await page.wait_for_selector("#b_results", timeout=10000)
        return await page.evaluate(_JS_BING)


def _cerca_ddg(query: str, n: int) -> list[dict]:
    if DDGS is None:
        raise RuntimeError("ddgs non installato")
    res = DDGS().text(query, region="it-it", safesearch="moderate", max_results=n)
    return [{"url": r.get("href", ""), "titolo": r.get("title", ""), "snippet": r.get("body", "")} for r in res or []]


@tool(
    "cerca_online",
    title="Ricerca online",
    description=(
        "Cerca sul web (Google, con ripiego automatico su DuckDuckGo e Bing) e restituisce i primi risultati "
        "organici: titolo, URL e descrizione. Usalo quando non sei sicuro di un'informazione, per dati "
        "aggiornati o per verificare un'ipotesi; poi leggi le pagine piu' utili con leggi_pagina_web. "
        "Per notizie recenti preferisci cerca_notizie, per immagini cerca_immagini."
    ),
    read_only=True,
)
async def cerca_online(query: str, num_risultati: int = 10) -> str:
    query = (query or "").strip()
    if not query:
        raise UserError("Query vuota.")
    n = max(1, min(int(num_risultati or 10), 20))
    errori = []
    motori = []
    if FEATURES["web"]:
        motori.append(("Google", lambda: _cerca_google(query)))
    motori.append(("DuckDuckGo", lambda: asyncio.to_thread(_cerca_ddg, query, n)))
    if FEATURES["web"]:
        motori.append(("Bing", lambda: _cerca_bing(query)))
    for nome, fn in motori:
        try:
            risultati = _filtra_risultati(await asyncio.wait_for(fn(), timeout=35), n)
            if risultati:
                righe = [f"{i}. {r['titolo']}\n   {r['url']}\n   {r['snippet']}".rstrip()
                         for i, r in enumerate(risultati, 1)]
                return f"Risultati {nome} per '{query}':\n\n" + "\n\n".join(righe)
            errori.append(f"{nome}: nessun risultato")
        except Exception as e:
            logger.warning(f"Ricerca {nome} fallita: {e}")
            errori.append(f"{nome}: {type(e).__name__}: {str(e)[:120]}")
    return f"Nessun risultato per '{query}'. Dettagli: " + "; ".join(errori)


@tool(
    "cerca_notizie",
    title="Cerca notizie",
    description=(
        "Cerca le notizie piu' recenti su un argomento (Google News, in italiano) con titolo, fonte, data e link. "
        "Usalo per attualita', eventi recenti, risultati sportivi, ultime notizie."
    ),
    read_only=True,
)
async def cerca_notizie(argomento: str, num_risultati: int = 8) -> str:
    n = max(1, min(int(num_risultati or 8), 25))
    url = f"{GOOGLE_NEWS_RSS}?q={quote_plus(argomento)}&hl=it&gl=IT&ceid=IT:it"
    try:
        dati, _, _ = await asyncio.to_thread(http_get, url, max_bytes=5_000_000)
        root = ET.fromstring(dati)
        items = root.findall("./channel/item")[:n]
        righe = []
        for i, it in enumerate(items, 1):
            titolo = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            fonte = (it.findtext("source") or "").strip()
            data_txt = it.findtext("pubDate") or ""
            with contextlib.suppress(Exception):
                from email.utils import parsedate_to_datetime
                data_txt = fmt_dt(parsedate_to_datetime(data_txt))
            righe.append(f"{i}. {titolo}\n   Fonte: {fonte} · {data_txt}\n   {link}")
        if righe:
            return f"Notizie su '{argomento}':\n\n" + "\n\n".join(righe)
    except UserError:
        raise
    except Exception as e:
        logger.warning(f"Google News fallito: {e}")
    if DDGS is not None:
        res = await asyncio.to_thread(lambda: DDGS().news(argomento, region="it-it", max_results=n))
        righe = [f"{i}. {r.get('title')}\n   Fonte: {r.get('source')} · {r.get('date')}\n   {r.get('url')}"
                 for i, r in enumerate(res or [], 1)]
        if righe:
            return f"Notizie su '{argomento}':\n\n" + "\n\n".join(righe)
    return f"Nessuna notizia trovata per '{argomento}'."


# =============================================================================
# WEB: LETTURA PAGINE, PDF, SCREENSHOT, DOWNLOAD
# =============================================================================
_cache_pagine: dict[str, tuple[float, str]] = {}


def _testo_da_pdf(dati: bytes) -> str:
    if PdfReader is None:
        raise UserError("pypdf non installato: impossibile leggere PDF")
    reader = PdfReader(io.BytesIO(dati))
    parti = []
    for i, p in enumerate(reader.pages[:200], 1):
        with contextlib.suppress(Exception):
            t = (p.extract_text() or "").strip()
            if t:
                parti.append(f"--- Pagina {i} ---\n{t}")
    return "\n\n".join(parti)


def _dividi_in_parti(testo: str, dim: int) -> list[str]:
    parti, corrente = [], ""
    for para in re.split(r"(\n\s*\n)", testo):
        if len(corrente) + len(para) > dim and corrente:
            parti.append(corrente)
            corrente = ""
        while len(para) > dim:
            parti.append(para[:dim])
            para = para[dim:]
        corrente += para
    if corrente.strip():
        parti.append(corrente)
    return parti or [""]


async def _scarica_pagina(url: str) -> str:
    """Ritorna testo completo (con intestazione) di una pagina HTML o PDF. Cache 10 minuti."""
    cache = _cache_pagine.get(url)
    if cache and time.time() - cache[0] < 600:
        return cache[1]

    is_pdf = urlparse(url).path.lower().endswith(".pdf")
    if not is_pdf:
        with contextlib.suppress(Exception):
            h = await asyncio.to_thread(lambda: HTTP.head(url, timeout=8, allow_redirects=False))
            is_pdf = "application/pdf" in h.headers.get("content-type", "")

    if is_pdf:
        dati, _, finale = await asyncio.to_thread(http_get, url, max_bytes=40 * 1024 * 1024, timeout=40)
        testo = await asyncio.to_thread(_testo_da_pdf, dati)
        completo = f"Documento PDF: {finale}\n\n{testo or '(nessun testo estraibile, forse PDF scansionato)'}"
    else:
        if not FEATURES["web"]:
            dati, _, finale = await asyncio.to_thread(http_get, url)
            html, titolo, immagini = dati.decode("utf-8", "ignore"), "", []
        else:
            async with BROWSER.pagina() as page:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=5000)
                await _accetta_cookie(page)
                if resp is not None and resp.status >= 400:
                    raise UserError(f"La pagina ha risposto con HTTP {resp.status}")
                html = await page.content()
                titolo = await page.title()
                finale = page.url
                immagini = await page.evaluate(
                    """() => {
                        const out = [];
                        const og = document.querySelector('meta[property="og:image"]');
                        if (og && og.content) out.push(og.content);
                        document.querySelectorAll('article img, main img, img').forEach(i => {
                            const src = i.currentSrc || i.src;
                            if (src && src.startsWith('http') && (i.naturalWidth >= 300 || i.width >= 300)) out.push(src);
                        });
                        return [...new Set(out)].slice(0, 6);
                    }"""
                )
                fallback = await page.evaluate("() => document.body ? document.body.innerText : ''")
        testo = None
        if trafilatura is not None:
            testo = await asyncio.to_thread(
                trafilatura.extract, html, url=finale, include_tables=True, include_links=False,
                include_comments=False, output_format="markdown", favor_recall=True,
            )
        if not testo or len(testo) < 200:
            testo = (locals().get("fallback") or testo or "").strip()
        if not testo:
            raise UserError("Pagina caricata ma nessun contenuto testuale estraibile.")
        intest = f"Titolo: {titolo}\nURL: {finale}\n" if titolo else f"URL: {finale}\n"
        if immagini:
            intest += "Immagini principali (per mostrarle usa ![descrizione](url)):\n" + \
                      "\n".join(f"  - {u}" for u in immagini) + "\n"
        completo = intest + "\n" + re.sub(r"\n{3,}", "\n\n", testo)

    _cache_pagine[url] = (time.time(), completo)
    if len(_cache_pagine) > 50:
        for k in sorted(_cache_pagine, key=lambda k: _cache_pagine[k][0])[:10]:
            _cache_pagine.pop(k, None)
    return completo


@tool(
    "leggi_pagina_web",
    title="Leggi pagina web",
    description=(
        "Apre un URL (pagine web o PDF) con un browser reale ed estrae il testo pulito in markdown, "
        "con titolo e immagini principali. I contenuti lunghi sono divisi in parti: usa il parametro "
        "'parte' (2, 3, ...) per continuare. Usalo dopo una ricerca per leggere le fonti, o quando "
        "l'utente invia un link chiedendo analisi o riassunti."
    ),
    read_only=True,
)
async def leggi_pagina_web(url: str, parte: int = 1) -> str:
    url = await asyncio.to_thread(controlla_url, url)
    completo = await _scarica_pagina(url)
    parti = _dividi_in_parti(completo, WEB_CHUNK_CHARS)
    parte = max(1, int(parte or 1))
    if parte > len(parti):
        raise UserError(f"La pagina ha solo {len(parti)} parti.")
    testo = parti[parte - 1]
    if len(parti) > 1:
        testo = f"[Parte {parte} di {len(parti)}]\n\n{testo}"
        if parte < len(parti):
            testo += f"\n\n[Continua: chiama leggi_pagina_web con parte={parte + 1}]"
    return testo


@tool(
    "screenshot_pagina",
    title="Screenshot della pagina",
    description=(
        "Apre un URL e restituisce uno screenshot (immagine) della pagina, utile per vedere layout, grafici, "
        "mappe o contenuti visivi che il testo non descrive. Lo screenshot viene anche salvato nel workspace."
    ),
    read_only=True,
)
async def screenshot_pagina(url: str, pagina_intera: bool = False) -> list:
    if not FEATURES["web"]:
        raise UserError("Playwright non disponibile.")
    url = await asyncio.to_thread(controlla_url, url)
    async with BROWSER.pagina(blocca_media=False) as page:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=6000)
        await _accetta_cookie(page)
        titolo = await page.title()
        if pagina_intera:
            altezza = min(await page.evaluate("() => document.body.scrollHeight"), 8000)
            png = await page.screenshot(clip={"x": 0, "y": 0, "width": 1366, "height": altezza}, full_page=True)
        else:
            png = await page.screenshot()
    cartella = WORKSPACE_DIR / "screenshot"
    cartella.mkdir(exist_ok=True)
    nome = cartella / f"screenshot_{now():%Y%m%d_%H%M%S}.png"
    nome.write_bytes(png)
    img = await asyncio.to_thread(immagine_a_contenuto, png, 1366)
    return [TextContent(type="text", text=f"Screenshot di '{titolo}' ({url}) salvato in "
                                          f"{nome.relative_to(WORKSPACE_DIR)}"), img]


# =============================================================================
# WEB: IMMAGINI
# =============================================================================
async def _immagini_bing(query: str, n: int) -> list[dict]:
    async with BROWSER.pagina() as page:
        await page.goto(f"https://www.bing.com/images/search?q={quote_plus(query)}&setlang=it&cc=IT",
                        wait_until="domcontentloaded", timeout=20000)
        await _accetta_cookie(page)
        with contextlib.suppress(Exception):
            await page.wait_for_selector("a.iusc", timeout=8000)
        meta = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a.iusc')).slice(0, 40).map(a => a.getAttribute('m'))"
        )
    out = []
    for m in meta:
        with contextlib.suppress(Exception):
            d = json.loads(m)
            out.append({"immagine": d.get("murl"), "anteprima": d.get("turl"),
                        "titolo": d.get("t", ""), "pagina": d.get("purl", "")})
        if len(out) >= n:
            break
    return out


def _immagini_ddg(query: str, n: int) -> list[dict]:
    if DDGS is None:
        raise RuntimeError("ddgs non installato")
    res = DDGS().images(query, region="it-it", safesearch="moderate", max_results=n)
    return [{"immagine": r.get("image"), "anteprima": r.get("thumbnail"), "titolo": r.get("title", ""),
             "pagina": r.get("url", ""), "dimensioni": f"{r.get('width')}x{r.get('height')}"}
            for r in res or []]


def _scarica_anteprima(info: dict) -> Optional[ImageContent]:
    for chiave in ("anteprima", "immagine"):
        u = info.get(chiave)
        if not u:
            continue
        try:
            dati, headers, _ = http_get(u, max_bytes=6 * 1024 * 1024, timeout=10,
                                        headers={"Accept": "image/*"})
            return immagine_a_contenuto(dati, 512)
        except Exception as e:
            logger.info(f"Anteprima non scaricabile ({u}): {e}")
    return None


@tool(
    "cerca_immagini",
    title="Cerca immagini",
    description=(
        "Cerca immagini sul web e restituisce titolo, URL dell'immagine e pagina di origine; se "
        "mostra_anteprime=true allega anche le anteprime cosi' puoi VEDERLE e scegliere le migliori. "
        "Per mostrare un'immagine all'utente nella chat scrivi nella risposta: ![descrizione](URL_immagine)."
    ),
    read_only=True,
)
async def cerca_immagini(query: str, numero: int = 4, mostra_anteprime: bool = True) -> list:
    n = max(1, min(int(numero or 4), 10))
    risultati: list[dict] = []
    errori = []
    try:
        risultati = await asyncio.wait_for(asyncio.to_thread(_immagini_ddg, query, n + 4), timeout=25)
    except Exception as e:
        errori.append(f"DuckDuckGo: {e}")
    if not risultati and FEATURES["web"]:
        try:
            risultati = await asyncio.wait_for(_immagini_bing(query, n + 4), timeout=35)
        except Exception as e:
            errori.append(f"Bing: {e}")
    risultati = [r for r in risultati if r.get("immagine", "").startswith("http")
                 and url_consentito(r["immagine"])[0]]
    if not risultati:
        raise UserError(f"Nessuna immagine trovata per '{query}'. " + "; ".join(errori))

    anteprime: list[Optional[ImageContent]] = [None] * len(risultati)
    if mostra_anteprime:
        anteprime = list(await asyncio.gather(
            *[asyncio.to_thread(_scarica_anteprima, r) for r in risultati[: n + 2]]
        )) + [None] * max(0, len(risultati) - n - 2)

    contenuti: list = []
    righe = []
    scelti = 0
    for r, img in zip(risultati, anteprime):
        if scelti >= n:
            break
        if mostra_anteprime and img is None:
            continue  # immagine non raggiungibile: la scarto
        scelti += 1
        titolo = (r.get("titolo") or query).replace("[", "(").replace("]", ")")[:100]
        righe.append(
            f"{scelti}. {titolo}\n   Immagine: {r['immagine']}\n   Fonte: {r.get('pagina', '')}\n"
            f"   Markdown: ![{titolo}]({r['immagine']})"
        )
        if img is not None:
            contenuti.append(TextContent(type="text", text=f"Anteprima immagine {scelti}:"))
            contenuti.append(img)
    if not righe:
        raise UserError("Immagini trovate ma nessuna scaricabile.")
    intro = (f"Immagini per '{query}' ({len(righe)}). Per mostrarle all'utente includi nella risposta "
             f"il markdown indicato.\n\n" + "\n\n".join(righe))
    return [TextContent(type="text", text=intro), *contenuti]


@tool(
    "vedi_immagine",
    title="Guarda immagine",
    description=(
        "Scarica un'immagine da un URL (o da un file nel workspace) e te la mostra, cosi' puoi descriverla "
        "o analizzarla. Restituisce anche il markdown per mostrarla all'utente."
    ),
    read_only=True,
)
async def vedi_immagine(sorgente: str) -> list:
    sorgente = sorgente.strip()
    if sorgente.startswith(("http://", "https://")):
        dati, headers, finale = await asyncio.to_thread(http_get, sorgente, max_bytes=15 * 1024 * 1024)
        md = f"![immagine]({finale})"
    else:
        p = percorso_workspace(sorgente)
        if not p.is_file():
            raise UserError(f"File non trovato: {sorgente}")
        dati = p.read_bytes()
        md = f"(file locale {p.relative_to(WORKSPACE_DIR)})"
    try:
        img = await asyncio.to_thread(immagine_a_contenuto, dati, 1024)
    except UserError:
        raise
    except Exception as e:
        raise UserError(f"Il contenuto non e' un'immagine valida: {e}")
    return [TextContent(type="text", text=f"Immagine caricata. Per mostrarla all'utente: {md}"), img]


@tool(
    "scarica_file",
    title="Scarica file",
    description=(
        "Scarica un file da un URL http/https e lo salva nel workspace (es. dataset, archivi, PDF). "
        "Restituisce percorso e dimensione. Per i repository git usa invece terminale_esegui con 'git clone'."
    ),
    group="terminal",
)
async def scarica_file(url: str, percorso_destinazione: str = "") -> str:
    url = await asyncio.to_thread(controlla_url, url)
    nome = percorso_destinazione.strip() or (Path(urlparse(url).path).name or "download.bin")
    dest = percorso_workspace(nome)
    if dest.is_dir():
        dest = dest / (Path(urlparse(url).path).name or "download.bin")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        _, headers, finale = await asyncio.to_thread(
            http_get, url, max_bytes=DOWNLOAD_MAX_MB * 1024 * 1024, timeout=60, verso_file=tmp)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return (f"Scaricato {finale}\n→ {dest.relative_to(WORKSPACE_DIR)} "
            f"({dest.stat().st_size / 1024:.1f} KB, {headers.get('Content-Type', 'tipo sconosciuto')})")


# =============================================================================
# METEO
# =============================================================================
CODICI_METEO = {
    0: "sereno", 1: "prevalentemente sereno", 2: "parzialmente nuvoloso", 3: "coperto",
    45: "nebbia", 48: "nebbia con brina", 51: "pioviggine debole", 53: "pioviggine moderata",
    55: "pioviggine intensa", 56: "pioviggine gelata debole", 57: "pioviggine gelata intensa",
    61: "pioggia debole", 63: "pioggia moderata", 65: "pioggia forte", 66: "pioggia gelata debole",
    67: "pioggia gelata forte", 71: "neve debole", 73: "neve moderata", 75: "neve forte",
    77: "granelli di neve", 80: "rovesci deboli", 81: "rovesci moderati", 82: "rovesci violenti",
    85: "rovesci di neve deboli", 86: "rovesci di neve forti", 95: "temporale",
    96: "temporale con grandine debole", 99: "temporale con grandine forte",
}


def _direzione_vento(gradi: Optional[float]) -> str:
    if gradi is None:
        return ""
    nomi = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    return nomi[int((gradi % 360) / 45 + 0.5) % 8]


def _geocodifica(localita: str) -> dict:
    parti = [p.strip() for p in localita.split(",") if p.strip()]
    nome = parti[0] if parti else localita
    r = HTTP.get(OPEN_METEO_GEO_URL, params={"name": nome, "count": 10, "language": "it", "format": "json"},
                 timeout=10)
    r.raise_for_status()
    risultati = r.json().get("results") or []
    if not risultati:
        raise UserError(f"Localita' '{localita}' non trovata. Prova con il solo nome del comune.")
    if len(parti) > 1:
        extra = normalizza(" ".join(parti[1:]))
        for res in risultati:
            campi = normalizza(" ".join(str(res.get(k, "")) for k in ("admin1", "admin2", "admin3",
                                                                      "country", "country_code")))
            if any(tok in campi for tok in extra.split()):
                return res
    italiani = [x for x in risultati if x.get("country_code") == "IT"]
    return (italiani or risultati)[0]


def _meteo(lat: float, lon: float, giorni: int) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "timezone": TZ_NAME, "forecast_days": giorni,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
                   "cloud_cover,wind_speed_10m,wind_direction_10m,wind_gusts_10m,is_day,pressure_msl",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset,uv_index_max",
    }
    r = HTTP.get(OPEN_METEO_URL, params=params, timeout=12)
    r.raise_for_status()
    return r.json()


@tool(
    "meteo",
    title="Meteo e previsioni",
    description=(
        "Meteo attuale, prossime ore e previsioni giornaliere (fino a 16 giorni) per una localita'. "
        "Passa 'localita' come nome (es. 'Priolo Gargallo' o 'Roma, Lazio') OPPURE latitudine e longitudine. "
        "Utile per domande su temperatura, pioggia, vento, alba/tramonto, UV."
    ),
    read_only=True,
)
async def meteo(localita: Optional[str] = None, latitudine: Optional[float] = None,
                longitudine: Optional[float] = None, giorni: int = 3) -> str:
    giorni = max(1, min(int(giorni or 3), 16))
    nome = ""
    if latitudine is None or longitudine is None:
        localita = (localita or METEO_DEFAULT_LOCALITA).strip()
        if not localita:
            raise UserError("Indica una localita' oppure latitudine e longitudine.")
        geo = await asyncio.to_thread(_geocodifica, localita)
        latitudine, longitudine = geo["latitude"], geo["longitude"]
        nome = ", ".join(x for x in (geo.get("name"), geo.get("admin2") or geo.get("admin1"), geo.get("country")) if x)
    dati = await asyncio.to_thread(_meteo, latitudine, longitudine, giorni)
    c = dati.get("current", {})
    righe = [f"Meteo {nome or ''} ({latitudine:.3f}, {longitudine:.3f})".replace("  ", " ")]
    righe.append(
        f"ADESSO ({c.get('time', '').replace('T', ' ')}): {CODICI_METEO.get(c.get('weather_code'), 'n/d')}, "
        f"{c.get('temperature_2m')}°C (percepita {c.get('apparent_temperature')}°C), "
        f"umidità {c.get('relative_humidity_2m')}%, nuvolosità {c.get('cloud_cover')}%, "
        f"precipitazioni {c.get('precipitation')} mm, vento {c.get('wind_speed_10m')} km/h "
        f"{_direzione_vento(c.get('wind_direction_10m'))} (raffiche {c.get('wind_gusts_10m')} km/h), "
        f"pressione {c.get('pressure_msl')} hPa"
    )
    h = dati.get("hourly", {})
    if h.get("time"):
        adesso = now().strftime("%Y-%m-%dT%H:00")
        try:
            i0 = h["time"].index(adesso)
        except ValueError:
            i0 = 0
        prossime = []
        for i in range(i0 + 1, min(i0 + 13, len(h["time"])), 2):
            prossime.append(
                f"{h['time'][i][11:16]} {h['temperature_2m'][i]}°C {CODICI_METEO.get(h['weather_code'][i], '')}"
                f" pioggia {h['precipitation_probability'][i]}%"
            )
        righe.append("PROSSIME ORE: " + " | ".join(prossime))
    d = dati.get("daily", {})
    righe.append("PREVISIONI:")
    for i, giorno in enumerate(d.get("time", [])):
        gd = date.fromisoformat(giorno)
        righe.append(
            f"- {GIORNI_BREVI[gd.weekday()]} {gd:%d/%m}: {CODICI_METEO.get(d['weather_code'][i], 'n/d')}, "
            f"{d['temperature_2m_min'][i]}–{d['temperature_2m_max'][i]}°C, pioggia {d['precipitation_sum'][i]} mm "
            f"(prob. {d['precipitation_probability_max'][i]}%), vento max {d['wind_speed_10m_max'][i]} km/h, "
            f"UV {d['uv_index_max'][i]}, alba {d['sunrise'][i][11:16]}, tramonto {d['sunset'][i][11:16]}"
        )
    return "\n".join(righe)


# =============================================================================
# CAMBIO VALUTA
# =============================================================================
def _cambio(importo: float, da: str, a: str) -> str:
    ultimo_errore = None
    for base in FRANKFURTER_URLS:
        try:
            r = HTTP.get(f"{base}/latest", params={"base": da, "symbols": a}, timeout=10)
            if r.status_code == 404 or r.status_code == 422:
                raise UserError(f"Valuta non supportata ({da} o {a}). Usa codici ISO come EUR, USD, GBP, CHF.")
            r.raise_for_status()
            j = r.json()
            tasso = j["rates"][a]
            return (f"{importo:,.2f} {da} = {importo * tasso:,.2f} {a}\n"
                    f"Tasso: 1 {da} = {tasso} {a} (BCE, aggiornato al {j.get('date')})")
        except UserError:
            raise
        except Exception as e:
            ultimo_errore = e
    raise RuntimeError(f"Servizio cambi non raggiungibile: {ultimo_errore}")


@tool(
    "converti_valuta",
    title="Converti valuta",
    description="Converte un importo tra valute con i tassi ufficiali BCE aggiornati (codici ISO: EUR, USD, GBP, CHF, JPY...).",
    read_only=True,
)
async def converti_valuta(importo: float, da: str, a: str) -> str:
    da, a = da.strip().upper(), a.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", da) or not re.fullmatch(r"[A-Z]{3}", a):
        raise UserError("Usa codici valuta ISO di 3 lettere (es. EUR, USD).")
    if da == a:
        return f"{importo} {da} = {importo} {a}"
    return await asyncio.to_thread(_cambio, float(importo), da, a)


# =============================================================================
# DOMANDE ALL'UTENTE
# =============================================================================
def client_supporta_moduli(ctx: Optional[Context]) -> bool:
    """True se i moduli interattivi sono abilitati e il client dichiara il supporto all'elicitation."""
    if not ENABLE_ELICITATION or ctx is None:
        return False
    try:
        params = ctx.session.client_params
        return bool(params and params.capabilities and params.capabilities.elicitation is not None)
    except Exception:
        return False


def _lista_opzioni(opzioni: Union[list[str], str, None]) -> list[str]:
    if not opzioni:
        return []
    voci = opzioni if isinstance(opzioni, list) else re.split(r"\s*[|;\n]\s*", str(opzioni))
    pulite: list[str] = []
    for v in voci:
        v = str(v).strip()
        if v and v not in pulite:
            pulite.append(v[:100])
    return pulite[:10]


def _modulo_domanda(opzioni: list[str], libera: bool, multipla: bool):
    """Crea lo schema del modulo per l'elicitation MCP (solo tipi primitivi, come da specifica)."""
    from pydantic import Field, create_model
    campi: dict[str, Any] = {}
    if opzioni and multipla:
        campi["scelte"] = (list[str] if not libera else Optional[list[str]],
                           Field(default=... if not libera else None, title="Scelte",
                                 json_schema_extra={"items": {"type": "string", "enum": opzioni}}))
    elif opzioni:
        campi["scelta"] = (str if not libera else Optional[str],
                           Field(default=... if not libera else None, title="Scelta",
                                 json_schema_extra={"enum": opzioni}))
    if libera:
        campi["risposta"] = (str if not opzioni else Optional[str],
                             Field(default=... if not opzioni else None,
                                   title="Risposta" if not opzioni else "Altro (risposta libera)"))
    return create_model("Risposta", **campi)


def _frase_domanda(domanda: str, opzioni: list[str], libera: bool, multipla: bool) -> str:
    """Frase pronta da dire all'utente: naturale, senza elenchi ne' simboli (adatta alla voce)."""
    d = domanda.strip().rstrip(":;,. ").strip()
    if not opzioni:
        return d if d.endswith("?") else d + "?"
    d = d.rstrip("?").strip()
    if len(opzioni) == 1:
        elenco = opzioni[0]
    else:
        elenco = ", ".join(opzioni[:-1]) + (" e " if multipla else " oppure ") + opzioni[-1]
    frase = f"{d}: {elenco}?"
    if multipla:
        frase += " Puoi sceglierne anche più di una."
    if libera:
        frase += " Oppure dimmi tu cosa preferisci."
    return frase


def _testo_domanda(domanda: str, opzioni: list[str], libera: bool, multipla: bool) -> str:
    righe = [
        "Fai questa domanda all'utente e poi fermati ad aspettare la sua risposta.",
        "Frase da dire, in italiano (usala così com'è):",
        _frase_domanda(domanda, opzioni, libera, multipla),
    ]
    if opzioni:
        tipo = "una o più" if multipla else "una"
        vincolo = "oppure una risposta libera" if libera else "solo tra queste"
        righe.append(f"Risposte valide ({tipo}, {vincolo}): " + " | ".join(opzioni))
    righe.append(
        "Regole: scrivi solo in italiano, senza parole di altre lingue; niente elenchi né simboli; "
        "non chiamare altri strumenti e non rispondere al posto dell'utente. "
        "Quando risponde, accetta anche frasi come 'la seconda', 'l'ultima' o parafrasi."
    )
    return "\n".join(righe)


@tool(
    "chiedi_all_utente",
    title="Fai una domanda",
    description=(
        "Fa una domanda all'utente e ne attende la risposta, con opzioni prefissate e/o risposta libera. "
        "Usalo quando devi far scegliere l'utente tra alternative (es. quale brano, quale evento, quale "
        "dispositivo), quando ti manca un'informazione indispensabile o per una conferma prima di un'azione "
        "importante. 'opzioni': elenco di scelte (lista oppure testo separato da '|'), al massimo 10; "
        "risposta_libera=false obbliga a scegliere tra le opzioni; scelta_multipla=true permette piu' scelte. "
        "Non usarlo per domande banali che puoi risolvere da solo."
    ),
    read_only=True,
)
async def chiedi_all_utente(domanda: str, opzioni: Union[list[str], str, None] = None,
                            risposta_libera: bool = True, scelta_multipla: bool = False,
                            ctx: Optional[Context] = None) -> str:
    domanda = (domanda or "").strip()
    if not domanda:
        raise UserError("La domanda e' vuota.")
    voci = _lista_opzioni(opzioni)
    libera = risposta_libera or not voci

    # Modulo interattivo (elicitation MCP), solo se abilitato e supportato dal client
    if client_supporta_moduli(ctx):
        try:
            esito = await asyncio.wait_for(
                ctx.elicit(domanda, _modulo_domanda(voci, libera, scelta_multipla)), ELICITATION_TIMEOUT)
        except asyncio.TimeoutError:
            return f"L'utente non ha risposto entro {fmt_durata(ELICITATION_TIMEOUT)} alla domanda: {domanda}"
        except Exception as e:
            logger.warning(f"Elicitation non riuscita, uso la domanda in chat: {e}")
        else:
            if esito.action != "accept":
                return ("L'utente ha preferito non rispondere alla domanda." if esito.action == "decline"
                        else "L'utente ha annullato la domanda.") + f" (Domanda: {domanda})"
            dati = esito.data.model_dump()
            parti = []
            if dati.get("scelte"):
                parti.append("ha scelto: " + ", ".join(dati["scelte"]))
            if dati.get("scelta"):
                parti.append(f"ha scelto: {dati['scelta']}")
            if dati.get("risposta"):
                parti.append(f"ha risposto: {dati['risposta']}")
            fuori_elenco = [x for x in ([dati.get("scelta")] + list(dati.get("scelte") or []))
                            if x and voci and x not in voci]
            if fuori_elenco:
                parti.append("(valori non presenti tra le opzioni: " + ", ".join(fuori_elenco) + ")")
            return f"Risposta dell'utente alla domanda '{domanda}': " + ("; ".join(parti) or "nessun valore")

    # Modalita' conversazionale: funziona con qualsiasi client e con la voce
    logger.info("[chiedi_all_utente] " + ("client senza supporto ai moduli interattivi: domanda in chat"
                                          if ENABLE_ELICITATION else "moduli disattivati: domanda in chat"))
    return _testo_domanda(domanda, voci, libera, scelta_multipla)


# =============================================================================
# MEMORIA PERSISTENTE
# =============================================================================
MEMORIA = JsonStore(DATA_DIR / "memoria.json", [])


@tool(
    "memoria_salva",
    title="Salva in memoria",
    description=(
        "Salva in modo permanente un'informazione utile sull'utente o sul contesto (preferenze, nomi, "
        "abitudini, dati ricorrenti) per ricordarla nelle conversazioni future. Usa testi brevi e autonomi."
    ),
)
async def memoria_salva(testo: str, categoria: str = "generale") -> str:
    testo = testo.strip()
    if not testo:
        raise UserError("Testo vuoto.")
    if len(testo) > 2000:
        raise UserError("Testo troppo lungo (max 2000 caratteri).")
    dati = MEMORIA.load()
    for m in dati:
        if normalizza(m["testo"]) == normalizza(testo):
            return f"Gia' presente in memoria (id {m['id']})."
    voce = {"id": secrets.token_hex(3), "testo": testo, "categoria": categoria.strip().lower() or "generale",
            "creato": now().isoformat(timespec="seconds")}
    dati.append(voce)
    MEMORIA.save(dati)
    return f"Salvato in memoria (id {voce['id']}, categoria {voce['categoria']})."


@tool(
    "memoria_leggi",
    title="Leggi memoria",
    description=(
        "Legge le informazioni salvate in memoria. Senza parametri restituisce tutto; con 'cerca' filtra "
        "per parole chiave o categoria. Consultala all'inizio delle conversazioni personali."
    ),
    read_only=True,
)
async def memoria_leggi(cerca: str = "") -> str:
    dati = MEMORIA.load()
    if cerca.strip():
        toks = normalizza(cerca).split()
        dati = [m for m in dati if all(t in normalizza(m["testo"] + " " + m["categoria"]) for t in toks)]
    if not dati:
        return "Nessuna informazione in memoria" + (f" per '{cerca}'." if cerca else ".")
    righe = [f"[{m['id']}] ({m['categoria']}) {m['testo']}" for m in dati]
    return f"{len(dati)} voci in memoria:\n" + "\n".join(righe)


@tool(
    "memoria_elimina",
    title="Elimina dalla memoria",
    description="Elimina una voce dalla memoria tramite il suo id (vedi memoria_leggi).",
    destructive=True,
)
async def memoria_elimina(id: str) -> str:
    dati = MEMORIA.load()
    nuovi = [m for m in dati if m["id"] != id.strip()]
    if len(nuovi) == len(dati):
        raise UserError(f"Nessuna voce con id '{id}'.")
    MEMORIA.save(nuovi)
    return f"Voce {id} eliminata."


# =============================================================================
# RUBRICA, EMAIL E WHATSAPP (invio sempre con conferma dell'utente)
# =============================================================================
RUBRICA = JsonStore(DATA_DIR / "rubrica.json", [])
_ALIAS_IO = {"io", "me", "a me", "mio", "mia", "mie", "miei", "me stesso", "me stessa", "utente", "l utente",
             "il mio", "la mia", "mia email", "mio numero", "my", "myself"}
_RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_RE_TELEFONO = re.compile(r"^[+\d][\d\s\-.()/]{5,}$")


def chiave_contatto(nome: str) -> str:
    n = normalizza(nome)
    return "io" if n in _ALIAS_IO else n


def nome_leggibile(chiave: str) -> str:
    return "te" if chiave == "io" else chiave


def normalizza_telefono(numero: str) -> str:
    """'340 343 4334' -> '+393403434334' (aggiunge WHATSAPP_PREFISSO se manca il prefisso)."""
    s = re.sub(r"[\s\-.()/]", "", numero or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        if s.startswith(WHATSAPP_PREFISSO) and len(s) > 10 + len(WHATSAPP_PREFISSO) - 1:
            s = "+" + s
        else:
            s = "+" + WHATSAPP_PREFISSO + s
    if not re.fullmatch(r"\+\d{8,15}", s):
        raise UserError(f"Numero di telefono non valido: '{numero}'.")
    return s


def telefono_leggibile(numero: str) -> str:
    pref = "+" + WHATSAPP_PREFISSO
    return numero[len(pref):] if numero.startswith(pref) else numero


def registra_invio(testo: str) -> None:
    with contextlib.suppress(Exception):
        with open(DATA_DIR / "invii.log", "a", encoding="utf-8") as fh:
            fh.write(f"{now().isoformat(timespec='seconds')} {testo}\n")


@tool(
    "rubrica_salva",
    title="Salva contatto",
    description=(
        "Salva o aggiorna un contatto nella rubrica (email e/o numero di telefono WhatsApp). Per i recapiti "
        "dell'utente stesso usa nome='io'. Usalo SUBITO quando l'utente ti comunica un indirizzo email o un "
        "numero, cosi' la prossima volta non dovrai chiederlo. I campi non indicati restano invariati."
    ),
)
async def rubrica_salva(nome: str, email: str = "", telefono: str = "", note: str = "") -> str:
    chiave = chiave_contatto(nome)
    if not chiave:
        raise UserError("Indica il nome del contatto ('io' per l'utente).")
    email = email.strip()
    if email and not _RE_EMAIL.match(email):
        raise UserError(f"Indirizzo email non valido: '{email}'.")
    tel = normalizza_telefono(telefono) if telefono.strip() else ""
    if not email and not tel and not note.strip():
        raise UserError("Indica almeno un'email o un numero di telefono.")
    contatti = RUBRICA.load()
    c = next((x for x in contatti if x["chiave"] == chiave), None)
    if c is None:
        c = {"chiave": chiave, "nome": "io" if chiave == "io" else nome.strip(), "email": "", "telefono": "",
             "note": "", "creato": now().isoformat(timespec="seconds")}
        contatti.append(c)
    if email:
        c["email"] = email
    if tel:
        c["telefono"] = tel
    if note.strip():
        c["note"] = note.strip()[:300]
    RUBRICA.save(contatti)
    return f"Contatto salvato: {_descr_contatto(c)}"


def _descr_contatto(c: dict) -> str:
    parti = [("io (l'utente)" if c["chiave"] == "io" else c["nome"])]
    if c.get("email"):
        parti.append(f"email {c['email']}")
    if c.get("telefono"):
        parti.append(f"telefono {telefono_leggibile(c['telefono'])}")
    if c.get("note"):
        parti.append(f"note: {c['note']}")
    return " · ".join(parti)


@tool(
    "rubrica_cerca",
    title="Cerca contatti",
    description="Cerca nella rubrica (nome, email, telefono, note). Senza testo elenca tutti i contatti. 'io' indica l'utente stesso.",
    read_only=True,
)
async def rubrica_cerca(testo: str = "") -> str:
    contatti = RUBRICA.load()
    if testo.strip():
        toks = normalizza(testo).split()
        contatti = [c for c in contatti if all(t in normalizza(" ".join(
            str(c.get(k, "")) for k in ("chiave", "nome", "email", "telefono", "note"))) for t in toks)
            or (chiave_contatto(testo) == "io" and c["chiave"] == "io")]
    if not contatti:
        return "Nessun contatto" + (f" per '{testo}'." if testo.strip() else " in rubrica.")
    return f"{len(contatti)} contatti:\n" + "\n".join(f"- {_descr_contatto(c)}" for c in contatti)


@tool(
    "rubrica_elimina",
    title="Elimina contatto",
    description="Elimina un contatto dalla rubrica, oppure solo la sua email o il suo telefono (campo='email' o 'telefono').",
    destructive=True,
)
async def rubrica_elimina(nome: str, campo: Literal["tutto", "email", "telefono"] = "tutto") -> str:
    chiave = chiave_contatto(nome)
    contatti = RUBRICA.load()
    c = next((x for x in contatti if x["chiave"] == chiave), None)
    if c is None:
        raise UserError(f"Nessun contatto '{nome}' in rubrica.")
    if campo == "tutto":
        contatti.remove(c)
        esito = f"Contatto '{nome_leggibile(chiave)}' eliminato."
    else:
        c[campo] = ""
        esito = f"Rimosso il campo {campo} di '{nome_leggibile(chiave)}'."
    RUBRICA.save(contatti)
    return esito


def risolvi_destinatario(destinatario: str, campo: Literal["email", "telefono"]) -> tuple[Optional[str], str, str]:
    """
    Ritorna (recapito, chiave_contatto, messaggio). Se il recapito manca, il messaggio spiega al modello
    cosa chiedere all'utente e come salvare la risposta in rubrica.
    """
    d = (destinatario or "").strip() or "io"
    if campo == "email" and _RE_EMAIL.match(d):
        return d, d, ""
    if campo == "telefono" and _RE_TELEFONO.match(d):
        return normalizza_telefono(d), d, ""
    chiave = chiave_contatto(d)
    contatti = RUBRICA.load()
    trovati = [c for c in contatti if c["chiave"] == chiave]
    if not trovati and chiave != "io":
        toks = chiave.split()
        trovati = [c for c in contatti if c["chiave"] != "io" and all(t in c["chiave"] for t in toks)]
    if len(trovati) > 1:
        nomi = ", ".join(c["nome"] for c in trovati)
        return None, chiave, (f"Ci sono piu' contatti che corrispondono a '{d}': {nomi}. Chiedi all'utente quale "
                              f"intende e poi richiama lo strumento con il nome esatto.")
    nome_tool = "invia_email" if campo == "email" else "invia_whatsapp"
    if trovati and trovati[0].get(campo):
        return trovati[0][campo], trovati[0]["chiave"], ""
    if trovati:
        chiave = trovati[0]["chiave"]
    cosa = "indirizzo email" if campo == "email" else "numero WhatsApp"
    if chiave == "io":
        frase = ("A quale indirizzo email te la mando?" if campo == "email"
                 else "A quale numero WhatsApp te lo mando?")
    else:
        frase = f"Qual è l'{cosa} di {trovati[0]['nome'] if trovati else d}?"
    nome_salva = "io" if chiave == "io" else (trovati[0]["nome"] if trovati else d)
    return None, chiave, (
        f"Non ho un {cosa} per {nome_leggibile(chiave)}: l'invio non e' stato preparato.\n"
        f"Frase da dire, in italiano (usala così com'è):\n{frase}\n"
        f"Quando l'utente risponde: 1) salva il recapito con rubrica_salva(nome='{nome_salva}', "
        f"{campo}='<recapito>'); 2) richiama {nome_tool} con destinatario='{nome_salva}' e gli stessi contenuti."
    )


_IN_ATTESA: dict[str, dict] = {}
_ATTESA_SECONDI = 900


def _crea_attesa(tipo: str, dati: dict) -> str:
    adesso = time.time()
    for k in [k for k, v in _IN_ATTESA.items() if v["scade"] < adesso]:
        _IN_ATTESA.pop(k, None)
    codice = secrets.token_hex(3).upper()
    _IN_ATTESA[codice] = {"tipo": tipo, "dati": dati, "scade": adesso + _ATTESA_SECONDI}
    return codice


def _prendi_attesa(codice: str, tipo: str) -> dict:
    voce = _IN_ATTESA.get(codice.strip().upper())
    if not voce or voce["tipo"] != tipo or voce["scade"] < time.time():
        raise UserError("Codice di conferma non valido, gia' usato o scaduto: prepara di nuovo l'invio "
                        "e chiedi conferma all'utente.")
    _IN_ATTESA.pop(codice.strip().upper(), None)
    return voce["dati"]


async def conferma_interattiva(ctx: Optional[Context], messaggio: str) -> Optional[bool]:
    """Chiede conferma con un modulo (elicitation). None = modulo non disponibile."""
    if not client_supporta_moduli(ctx):
        return None
    from pydantic import Field, create_model
    modello = create_model("Conferma", conferma=(bool, Field(title="Confermi l'invio?")))
    try:
        esito = await asyncio.wait_for(ctx.elicit(messaggio, modello), ELICITATION_TIMEOUT)
    except asyncio.TimeoutError:
        return False
    except Exception as e:
        logger.warning(f"Conferma con modulo non riuscita, uso la conferma in chat: {e}")
        return None
    return esito.action == "accept" and bool(getattr(esito.data, "conferma", False))


async def _chiedi_conferma(ctx: Optional[Context], tipo: str, dati: dict, frase: str, anteprima: str,
                           nome_tool: str) -> Optional[str]:
    """None = confermato (procedi con l'invio); altrimenti il testo da restituire al modello."""
    esito = await conferma_interattiva(ctx, f"{frase}\n\n{anteprima}")
    if esito is True:
        return None
    if esito is False:
        return "L'utente non ha confermato: invio annullato."
    codice = _crea_attesa(tipo, dati)
    return (
        "Invio NON ancora effettuato: serve la conferma esplicita dell'utente.\n"
        f"Frase da dire, in italiano (usala così com'è):\n{frase}\n\n"
        f"Anteprima (per te, non leggerla tutta):\n{anteprima}\n\n"
        f"Solo se l'utente conferma (es. 'sì', 'ok', 'manda'), richiama {nome_tool} con "
        f"codice_conferma='{codice}' e nessun altro parametro. Se rifiuta o chiede modifiche, non usare il codice. "
        f"Non dire il codice all'utente. Il codice vale una sola volta e scade tra {_ATTESA_SECONDI // 60} minuti."
    )


def _controlla_allegati(allegati: list[str]) -> list[str]:
    puliti = []
    for a in allegati[:10]:
        a = str(a).strip()
        if not a:
            continue
        if a.startswith(("http://", "https://")):
            controlla_url(a)
        else:
            p = percorso_workspace(a)
            if not p.is_file():
                raise UserError(f"Allegato non trovato nel workspace: {a}")
        puliti.append(a)
    return puliti


def _carica_allegato(sorgente: str, max_bytes: int) -> tuple[bytes, str, str]:
    """Ritorna (contenuto, nome_file, mime) di un URL o di un file del workspace."""
    if sorgente.startswith(("http://", "https://")):
        dati, headers, finale = http_get(sorgente, max_bytes=max_bytes, timeout=20)
        nome = Path(urlparse(finale).path).name or "allegato"
        mime = (headers.get("Content-Type") or headers.get("content-type") or "").split(";")[0].strip()
    else:
        p = percorso_workspace(sorgente)
        if p.stat().st_size > max_bytes:
            raise UserError(f"Allegato troppo grande: {sorgente}")
        dati, nome, mime = p.read_bytes(), p.name, ""
    if not mime or mime == "application/octet-stream":
        mime = mimetypes.guess_type(nome)[0] or "application/octet-stream"
    if "." not in nome:
        nome += mimetypes.guess_extension(mime) or ""
    return dati, nome, mime


def _lista_testi(valore: Union[list[str], str, None]) -> list[str]:
    if not valore:
        return []
    if isinstance(valore, list):
        return [str(v) for v in valore]
    return [v for v in re.split(r"[\s,]+", str(valore)) if v]


# --- Email --------------------------------------------------------------------
def _invia_email_sync(dati: dict) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr((SMTP_NOME, SMTP_FROM))
    msg["To"] = dati["a"]
    msg["Subject"] = dati["oggetto"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(dati["testo"])
    residuo = ALLEGATI_MAX_MB * 1024 * 1024
    for sorgente in dati["allegati"]:
        contenuto, nome, mime = _carica_allegato(sorgente, residuo)
        residuo -= len(contenuto)
        principale, _, secondario = mime.partition("/")
        msg.add_attachment(contenuto, maintype=principale, subtype=secondario or "octet-stream", filename=nome)
    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
            if SMTP_STARTTLS:
                server.starttls(context=ssl.create_default_context())
        with server:
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise UserError("Credenziali SMTP rifiutate. Con Gmail serve una 'password per le app' (account con verifica in due passaggi).")
    except smtplib.SMTPRecipientsRefused:
        raise UserError(f"Il server di posta ha rifiutato il destinatario {dati['a']}.")
    except (smtplib.SMTPException, OSError) as e:
        raise UserError(f"Invio email non riuscito: {type(e).__name__}: {e}")


@tool(
    "invia_email",
    title="Invia email",
    description=(
        "Invia un'email (testo semplice, con eventuali allegati). destinatario: 'io' per l'utente stesso "
        "(es. 'mandamela per email'), il nome di un contatto della rubrica (es. 'amministratore condominio') "
        "oppure un indirizzo email. Se il recapito non e' in rubrica lo strumento ti dice cosa chiedere "
        "all'utente e di salvarlo con rubrica_salva. L'invio richiede SEMPRE la conferma dell'utente: la prima "
        "chiamata prepara l'email e ti restituisce la domanda di conferma; solo dopo il si' dell'utente "
        "richiami lo strumento con il codice_conferma ricevuto. allegati: URL di immagini/file o percorsi del workspace."
    ),
    group="email",
    destructive=True,
)
async def invia_email(destinatario: str = "io", oggetto: str = "", testo: str = "",
                      allegati: Union[list[str], str, None] = None, codice_conferma: str = "",
                      ctx: Optional[Context] = None) -> str:
    if codice_conferma.strip():
        dati = _prendi_attesa(codice_conferma, "email")
    else:
        if not testo.strip():
            raise UserError("Il testo dell'email e' vuoto.")
        indirizzo, chiave, messaggio = await asyncio.to_thread(risolvi_destinatario, destinatario, "email")
        if not indirizzo:
            return messaggio
        dati = {"a": indirizzo, "chiave": chiave, "oggetto": (oggetto.strip() or "Messaggio dal tuo assistente")[:200],
                "testo": testo.strip(), "allegati": await asyncio.to_thread(_controlla_allegati, _lista_testi(allegati))}
        frase = (f"Mando la mail a {indirizzo}?" if chiave in ("io", indirizzo)
                 else f"Mando la mail a {chiave} all'indirizzo {indirizzo}?")
        anteprima = (f"A: {indirizzo}\nOggetto: {dati['oggetto']}\nTesto:\n{tronca(dati['testo'], 1500)}"
                     + (f"\nAllegati: {len(dati['allegati'])}" if dati["allegati"] else ""))
        attesa = await _chiedi_conferma(ctx, "email", dati, frase, anteprima, "invia_email")
        if attesa is not None:
            return attesa
    await asyncio.to_thread(_invia_email_sync, dati)
    registra_invio(f"EMAIL a={dati['a']} oggetto={dati['oggetto']!r} allegati={len(dati['allegati'])}")
    return (f"Email inviata a {dati['a']} con oggetto '{dati['oggetto']}'"
            + (f" e {len(dati['allegati'])} allegati." if dati["allegati"] else "."))


# --- WhatsApp -----------------------------------------------------------------
def _gateway_post(url: str, corpo: dict, chiave: str, nome: str, var_chiave: str, sessione: str) -> None:
    """POST verso un gateway WhatsApp self-hosted (OpenWA o WAHA), con errori comprensibili."""
    headers = {"X-API-Key": chiave} if chiave else {}
    try:
        r = requests.post(url, json=corpo, headers=headers, timeout=60)
    except requests.exceptions.RequestException as e:
        raise UserError(f"Gateway WhatsApp ({nome}) non raggiungibile: {type(e).__name__}")
    if r.status_code in (401, 403):
        raise UserError(f"Chiave del gateway WhatsApp non valida o senza permessi ({var_chiave}).")
    if r.status_code == 404:
        raise UserError(f"Il gateway {nome} non trova la sessione '{sessione}': controlla l'ID della sessione.")
    if r.status_code >= 400:
        raise UserError(f"Il gateway {nome} ha risposto HTTP {r.status_code}: {r.text[:200]} "
                        f"(la sessione '{sessione}' e' collegata al telefono?)")


def _gateway_invia(tipo: Literal["testo", "immagine"], chat: str, testo: str = "",
                   contenuto: bytes = b"", mime: str = "", nome_file: str = "") -> None:
    if WHATSAPP_BACKEND == "openwa":
        base = f"{OPENWA_URL}/api/sessions/{quote(OPENWA_SESSION_ID, safe='')}/messages"
        if tipo == "testo":
            corpo = {"chatId": chat, "text": testo}
        else:
            corpo = {"chatId": chat, "base64": base64.b64encode(contenuto).decode(), "mimetype": mime,
                     "filename": nome_file, "caption": ""}
        url = f"{base}/send-text" if tipo == "testo" else f"{base}/send-image"
        _gateway_post(url, corpo, OPENWA_API_KEY, "OpenWA", "OPENWA_API_KEY", OPENWA_SESSION_ID)
        return
    if tipo == "testo":
        corpo = {"session": WAHA_SESSION, "chatId": chat, "text": testo}
    else:
        corpo = {"session": WAHA_SESSION, "chatId": chat, "caption": "",
                 "file": {"mimetype": mime, "filename": nome_file, "data": base64.b64encode(contenuto).decode()}}
    url = f"{WAHA_URL}/api/sendText" if tipo == "testo" else f"{WAHA_URL}/api/sendImage"
    _gateway_post(url, corpo, WAHA_API_KEY, "WAHA", "WAHA_API_KEY", WAHA_SESSION)


def _invia_whatsapp_sync(dati: dict) -> None:
    numero, testo, immagini = dati["numero"], dati["testo"], dati["immagini"]
    if WHATSAPP_BACKEND == "callmebot":
        if numero != normalizza_telefono(CALLMEBOT_PHONE):
            raise UserError("Con CallMeBot si possono inviare messaggi solo al proprio numero "
                            f"({telefono_leggibile(normalizza_telefono(CALLMEBOT_PHONE))}).")
        locali = [i for i in immagini if not i.startswith(("http://", "https://"))]
        if locali:
            raise UserError("CallMeBot non puo' inviare file: solo link a immagini online.")
        corpo = "\n".join([testo] + immagini).strip()
        try:
            r = requests.get(CALLMEBOT_URL, params={"phone": numero, "text": corpo, "apikey": CALLMEBOT_APIKEY},
                             timeout=30)
        except requests.exceptions.RequestException as e:
            raise UserError(f"CallMeBot non raggiungibile: {type(e).__name__}")
        if r.status_code >= 400 or "error" in r.text.lower() or "invalid" in r.text.lower():
            raise UserError(f"CallMeBot ha rifiutato il messaggio: {re.sub(r'<[^>]+>', ' ', r.text)[:200].strip()}")
        return
    chat = numero.lstrip("+") + "@c.us"
    if testo:
        _gateway_invia("testo", chat, testo=testo)
    residuo = ALLEGATI_MAX_MB * 1024 * 1024
    for sorgente in immagini:
        contenuto, nome, mime = _carica_allegato(sorgente, residuo)
        residuo -= len(contenuto)
        if not mime.startswith("image/"):
            raise UserError(f"'{sorgente}' non e' un'immagine ({mime}).")
        _gateway_invia("immagine", chat, contenuto=contenuto, mime=mime, nome_file=nome)


@tool(
    "invia_whatsapp",
    title="Invia messaggio WhatsApp",
    description=(
        "Invia un messaggio WhatsApp con testo e/o immagini (es. 'cerca immagini di X e mandamele su WhatsApp': "
        "passa gli URL trovati con cerca_immagini in 'immagini'). destinatario: 'io' per l'utente stesso, il nome "
        "di un contatto della rubrica oppure un numero. Se il numero non e' in rubrica lo strumento ti dice cosa "
        "chiedere all'utente e di salvarlo con rubrica_salva. L'invio richiede SEMPRE la conferma dell'utente: "
        "la prima chiamata prepara il messaggio e ti restituisce la domanda di conferma; solo dopo il si' "
        "dell'utente richiami lo strumento con il codice_conferma ricevuto."
    ),
    group="whatsapp",
    destructive=True,
)
async def invia_whatsapp(destinatario: str = "io", testo: str = "", immagini: Union[list[str], str, None] = None,
                         codice_conferma: str = "", ctx: Optional[Context] = None) -> str:
    if codice_conferma.strip():
        dati = _prendi_attesa(codice_conferma, "whatsapp")
    else:
        lista = await asyncio.to_thread(_controlla_allegati, _lista_testi(immagini))
        if not testo.strip() and not lista:
            raise UserError("Indica il testo del messaggio e/o le immagini da inviare.")
        numero, chiave, messaggio = await asyncio.to_thread(risolvi_destinatario, destinatario, "telefono")
        if not numero:
            return messaggio
        dati = {"numero": numero, "chiave": chiave, "testo": testo.strip()[:4000], "immagini": lista}
        chi = "" if chiave == "io" or _RE_TELEFONO.match(chiave) else f"a {chiave} "
        cosa = "il messaggio" + (f" con {len(lista)} immagini" if len(lista) > 1 else " con un'immagine" if lista else "")
        frase = f"Mando {cosa} {chi}al numero {telefono_leggibile(numero)}?"
        anteprima = (f"A: {numero}\nTesto: {tronca(dati['testo'], 800) or '(nessuno)'}"
                     + (f"\nImmagini: {len(lista)}" if lista else ""))
        attesa = await _chiedi_conferma(ctx, "whatsapp", dati, frase, anteprima, "invia_whatsapp")
        if attesa is not None:
            return attesa
    await asyncio.to_thread(_invia_whatsapp_sync, dati)
    registra_invio(f"WHATSAPP a={dati['numero']} immagini={len(dati['immagini'])} backend={WHATSAPP_BACKEND}")
    return (f"Messaggio WhatsApp inviato al numero {telefono_leggibile(dati['numero'])}"
            + (f" con {len(dati['immagini'])} immagini." if dati["immagini"] else "."))


# =============================================================================
# WORKSPACE: TERMINALE E FILE
# =============================================================================
_CHIAVI_SENSIBILI = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CREDENTIAL|PRIVATE_KEY|"
                               r"^HASS_|^GOOGLE_|^MCP_|^AWS_|^OPENAI|^ANTHROPIC)", re.I)


def env_sicuro() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _CHIAVI_SENSIBILI.search(k)}
    env.update({"PYTHONUNBUFFERED": "1", "GIT_TERMINAL_PROMPT": "0", "TERM": "dumb",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PAGER": "cat", "GIT_PAGER": "cat",
                "WORKSPACE": str(WORKSPACE_DIR)})
    venv_bin = str(Path(sys.executable).parent)
    if venv_bin not in env.get("PATH", ""):
        env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    return env


def percorso_workspace(p: str) -> Path:
    """Risolve un percorso garantendo che resti dentro il workspace (anche seguendo symlink)."""
    p = (p or ".").strip()
    candidato = Path(p).expanduser()
    if not candidato.is_absolute():
        candidato = WORKSPACE_DIR / candidato
    reale = Path(os.path.realpath(candidato))
    if reale != WORKSPACE_DIR and WORKSPACE_DIR not in reale.parents:
        raise UserError(f"Percorso fuori dal workspace non consentito: {p}")
    return reale


def _taglia_output(testo: str) -> str:
    return tronca(testo, TERMINAL_MAX_OUTPUT, coda=TERMINAL_MAX_OUTPUT * 2 // 3)


class GestoreProcessi:
    """Processi in background avviati dal terminale."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.cartella = DATA_DIR / "jobs"
        self.cartella.mkdir(exist_ok=True)

    async def avvia(self, comando: str, cwd: Path) -> str:
        attivi = [j for j in self.jobs.values() if j["proc"].returncode is None]
        if len(attivi) >= 10:
            raise UserError("Troppi processi in background (max 10): terminane qualcuno.")
        jid = secrets.token_hex(3)
        log = self.cartella / f"{jid}.log"
        fh = open(log, "wb")
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", comando, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env_sicuro(), start_new_session=True,
        )
        fh.close()
        self.jobs[jid] = {"proc": proc, "comando": comando, "log": log, "avvio": time.time(), "cwd": str(cwd)}
        return jid

    def output(self, jid: str, ultimi: int = 6000) -> str:
        j = self._job(jid)
        dati = j["log"].read_bytes() if j["log"].exists() else b""
        testo = dati.decode("utf-8", "replace")
        return testo[-ultimi:] if len(testo) > ultimi else testo

    def _job(self, jid: str) -> dict:
        if jid not in self.jobs:
            raise UserError(f"Processo '{jid}' sconosciuto.")
        return self.jobs[jid]

    def termina(self, jid: str) -> str:
        j = self._job(jid)
        if j["proc"].returncode is not None:
            return f"Il processo {jid} era gia' terminato (codice {j['proc'].returncode})."
        with contextlib.suppress(ProcessLookupError):
            os.killpg(j["proc"].pid, signal.SIGTERM)
        return f"Segnale di terminazione inviato al processo {jid}."

    def termina_tutti(self) -> None:
        for j in self.jobs.values():
            if j["proc"].returncode is None:
                with contextlib.suppress(Exception):
                    os.killpg(j["proc"].pid, signal.SIGKILL)


PROCESSI = GestoreProcessi()


async def esegui_comando(comando: str, cwd: Path, timeout: int) -> tuple[int, str, float]:
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", comando, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, env=env_sicuro(), start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        codice = proc.returncode
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, _ = await proc.communicate()
        out = (out or b"") + f"\n[TIMEOUT: processo terminato dopo {timeout}s]".encode()
        codice = -9
    return codice, (out or b"").decode("utf-8", "replace"), time.monotonic() - t0


@tool(
    "terminale_esegui",
    title="Terminale",
    description=(
        "Esegue un comando bash nel workspace dedicato (es. git clone, ls, pip install, python script.py, "
        "grep, curl, compilazioni, test). Ogni chiamata parte da 'cartella' (relativa al workspace): "
        "per comandi in sequenza usa '&&'. Restituisce codice di uscita e output (stdout+stderr). "
        "Per server o comandi molto lunghi usa in_background=true e poi terminale_processi. "
        "Non usare comandi interattivi (usa flag come -y)."
    ),
    group="terminal",
    destructive=True,
)
async def terminale_esegui(comando: str, cartella: str = ".", timeout_secondi: int = 120,
                           in_background: bool = False) -> str:
    if not comando.strip():
        raise UserError("Comando vuoto.")
    cwd = percorso_workspace(cartella)
    if not cwd.is_dir():
        raise UserError(f"Cartella inesistente: {cartella}")
    logger.info(f"[terminale] ({cwd}) $ {comando}")
    if in_background:
        jid = await PROCESSI.avvia(comando, cwd)
        await asyncio.sleep(1.5)
        return (f"Avviato in background con id '{jid}'.\nOutput iniziale:\n{PROCESSI.output(jid, 2000) or '(nessuno)'}"
                f"\nUsa terminale_processi(azione='output', id='{jid}') per seguirlo.")
    timeout = max(1, min(int(timeout_secondi or 120), TERMINAL_MAX_TIMEOUT))
    codice, out, durata = await esegui_comando(comando, cwd, timeout)
    rel = "." if cwd == WORKSPACE_DIR else str(cwd.relative_to(WORKSPACE_DIR))
    return (f"$ {comando}\n[cartella: {rel} · codice uscita: {codice} · {durata:.1f}s]\n\n"
            f"{_taglia_output(out) or '(nessun output)'}")


@tool(
    "terminale_processi",
    title="Processi in background",
    description="Gestisce i processi avviati con terminale_esegui(in_background=true). azione: 'lista', 'output' (richiede id) o 'termina' (richiede id).",
    group="terminal",
)
async def terminale_processi(azione: Literal["lista", "output", "termina"] = "lista", id: str = "") -> str:
    if azione == "lista":
        if not PROCESSI.jobs:
            return "Nessun processo in background."
        righe = []
        for jid, j in PROCESSI.jobs.items():
            stato = "in esecuzione" if j["proc"].returncode is None else f"terminato (codice {j['proc'].returncode})"
            righe.append(f"[{jid}] {stato} · da {fmt_durata(time.time() - j['avvio'])} · {j['comando'][:120]}")
        return "\n".join(righe)
    if not id:
        raise UserError("Specifica l'id del processo.")
    if azione == "output":
        j = PROCESSI._job(id)
        stato = "in esecuzione" if j["proc"].returncode is None else f"terminato (codice {j['proc'].returncode})"
        return f"[{id}] {stato}\n\n{PROCESSI.output(id) or '(nessun output)'}"
    return PROCESSI.termina(id)


@tool(
    "esegui_python",
    title="Esegui codice Python",
    description=(
        "Esegue uno script Python (stesso interprete del server, con le librerie installate) nel workspace "
        "e restituisce l'output. Utile per calcoli complessi, analisi dati, conversioni, grafici "
        "(salva i file nel workspace). Usa print() per mostrare i risultati."
    ),
    group="terminal",
    destructive=True,
)
async def esegui_python(codice: str, timeout_secondi: int = 60) -> str:
    tmpdir = WORKSPACE_DIR / ".tmp"
    tmpdir.mkdir(exist_ok=True)
    script = tmpdir / f"snippet_{secrets.token_hex(4)}.py"
    script.write_text(codice, encoding="utf-8")
    try:
        timeout = max(1, min(int(timeout_secondi or 60), TERMINAL_MAX_TIMEOUT))
        codice_uscita, out, durata = await esegui_comando(
            f"{quote_shell(sys.executable)} {quote_shell(str(script))}", WORKSPACE_DIR, timeout)
    finally:
        script.unlink(missing_ok=True)
    return f"[codice uscita: {codice_uscita} · {durata:.1f}s]\n{_taglia_output(out) or '(nessun output)'}"


def quote_shell(s: str) -> str:
    import shlex
    return shlex.quote(s)


@tool(
    "file_leggi",
    title="Leggi file",
    description="Legge un file di testo del workspace con numeri di riga. Per file lunghi usa da_riga e num_righe.",
    group="terminal",
    read_only=True,
)
async def file_leggi(percorso: str, da_riga: int = 1, num_righe: int = 400) -> str:
    p = percorso_workspace(percorso)
    if not p.is_file():
        raise UserError(f"File non trovato: {percorso}")
    if p.stat().st_size > 50 * 1024 * 1024:
        raise UserError("File troppo grande (>50MB): usa terminale_esegui con head/tail/grep.")
    dati = p.read_bytes()
    if b"\x00" in dati[:8192]:
        return f"{percorso}: file binario di {len(dati)} byte (usa vedi_immagine per le immagini)."
    righe = dati.decode("utf-8", "replace").splitlines()
    inizio = max(1, int(da_riga or 1))
    num = max(1, min(int(num_righe or 400), 2000))
    sel = righe[inizio - 1: inizio - 1 + num]
    corpo = "\n".join(f"{i:>5}  {r[:1000]}" for i, r in enumerate(sel, inizio))
    fine = inizio + len(sel) - 1
    nota = f"\n[righe {inizio}-{fine} di {len(righe)}]" + (
        f" — continua con da_riga={fine + 1}" if fine < len(righe) else "")
    return tronca(corpo, 60000) + nota


@tool(
    "file_scrivi",
    title="Scrivi file",
    description="Crea o sovrascrive un file di testo nel workspace (crea le cartelle mancanti). Con aggiungi=true accoda al file esistente.",
    group="terminal",
    destructive=True,
)
async def file_scrivi(percorso: str, contenuto: str, aggiungi: bool = False) -> str:
    p = percorso_workspace(percorso)
    if p.is_dir():
        raise UserError(f"'{percorso}' e' una cartella.")
    p.parent.mkdir(parents=True, exist_ok=True)
    esisteva = p.exists()
    with open(p, "a" if aggiungi else "w", encoding="utf-8") as fh:
        fh.write(contenuto)
    azione = "Aggiunto a" if aggiungi else ("Sovrascritto" if esisteva else "Creato")
    return f"{azione} {p.relative_to(WORKSPACE_DIR)} ({len(contenuto)} caratteri, {contenuto.count(chr(10)) + 1} righe)."


@tool(
    "file_modifica",
    title="Modifica file",
    description=(
        "Modifica un file del workspace sostituendo testo_vecchio (che deve comparire esattamente, una sola volta, "
        "spazi inclusi) con testo_nuovo. Con tutte_le_occorrenze=true sostituisce ogni occorrenza. "
        "Leggi prima il file con file_leggi."
    ),
    group="terminal",
    destructive=True,
)
async def file_modifica(percorso: str, testo_vecchio: str, testo_nuovo: str,
                        tutte_le_occorrenze: bool = False) -> str:
    p = percorso_workspace(percorso)
    if not p.is_file():
        raise UserError(f"File non trovato: {percorso}")
    if not testo_vecchio:
        raise UserError("testo_vecchio non puo' essere vuoto.")
    contenuto = p.read_text(encoding="utf-8")
    n = contenuto.count(testo_vecchio)
    if n == 0:
        raise UserError("testo_vecchio non trovato nel file (controlla spazi e a capo; rileggi con file_leggi).")
    if n > 1 and not tutte_le_occorrenze:
        raise UserError(f"testo_vecchio compare {n} volte: aggiungi contesto per renderlo unico o usa tutte_le_occorrenze=true.")
    nuovo = contenuto.replace(testo_vecchio, testo_nuovo) if tutte_le_occorrenze \
        else contenuto.replace(testo_vecchio, testo_nuovo, 1)
    p.write_text(nuovo, encoding="utf-8")
    riga = contenuto[: contenuto.index(testo_vecchio)].count("\n") + 1
    return f"Modificato {p.relative_to(WORKSPACE_DIR)}: {n if tutte_le_occorrenze else 1} sostituzione/i (prima alla riga {riga})."


@tool(
    "file_elenca",
    title="Elenca file",
    description="Elenca file e cartelle del workspace. 'pattern' e' un glob (es. '*.py', '**/*.md' per ricorsivo).",
    group="terminal",
    read_only=True,
)
async def file_elenca(percorso: str = ".", pattern: str = "*") -> str:
    base = percorso_workspace(percorso)
    if not base.is_dir():
        raise UserError(f"Cartella non trovata: {percorso}")
    voci = []
    for p in sorted(base.glob(pattern or "*")):
        if any(part in (".git", "__pycache__", "node_modules", ".tmp") for part in p.relative_to(base).parts[:-1]):
            continue
        rel = p.relative_to(WORKSPACE_DIR)
        if p.is_dir():
            voci.append(f"📁 {rel}/")
        else:
            with contextlib.suppress(OSError):
                voci.append(f"   {rel}  ({p.stat().st_size / 1024:.1f} KB)")
        if len(voci) >= 500:
            voci.append("[... elenco troncato a 500 voci ...]")
            break
    rel_base = "." if base == WORKSPACE_DIR else base.relative_to(WORKSPACE_DIR)
    return f"Workspace/{rel_base} — {len(voci)} voci:\n" + ("\n".join(voci) or "(vuota)")


@tool(
    "file_cerca",
    title="Cerca nei file",
    description="Cerca un'espressione regolare nel contenuto dei file del workspace (come grep -rn). 'filtro_file' e' un glob (es. '*.py').",
    group="terminal",
    read_only=True,
)
async def file_cerca(regex: str, percorso: str = ".", filtro_file: str = "*",
                     ignora_maiuscole: bool = False) -> str:
    base = percorso_workspace(percorso)
    try:
        rx = re.compile(regex, re.I if ignora_maiuscole else 0)
    except re.error as e:
        raise UserError(f"Regex non valida: {e}")

    def _cerca() -> list[str]:
        trovati = []
        files = [base] if base.is_file() else base.rglob(filtro_file or "*")
        for f in files:
            if not f.is_file() or any(x in f.parts for x in (".git", "node_modules", "__pycache__")):
                continue
            with contextlib.suppress(OSError):
                if f.stat().st_size > 5 * 1024 * 1024:
                    continue
                with open(f, "r", encoding="utf-8", errors="strict") as fh:
                    try:
                        for n, riga in enumerate(fh, 1):
                            if rx.search(riga):
                                trovati.append(f"{f.relative_to(WORKSPACE_DIR)}:{n}: {riga.rstrip()[:300]}")
                                if len(trovati) >= 200:
                                    return trovati
                    except UnicodeDecodeError:
                        continue
        return trovati

    trovati = await asyncio.to_thread(_cerca)
    if not trovati:
        return f"Nessuna corrispondenza per /{regex}/."
    return f"{len(trovati)} corrispondenze{' (limite 200)' if len(trovati) >= 200 else ''}:\n" + "\n".join(trovati)


# =============================================================================
# HOME ASSISTANT
# =============================================================================
_RE_ENTITY = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_RE_SLUG = re.compile(r"^[a-z0-9_]+$")
_ATTR_RUMOROSI = {"friendly_name", "unit_of_measurement", "icon", "entity_picture", "supported_features",
                  "supported_color_modes", "attribution", "assumed_state", "editable", "id", "restored",
                  "options", "effect_list", "source_list", "sound_mode_list", "hvac_modes", "fan_modes",
                  "preset_modes", "swing_modes", "access_token", "entity_id", "device_trackers",
                  "state_class", "min_color_temp_kelvin", "max_color_temp_kelvin", "min_mireds", "max_mireds"}
_ATTR_SENSIBILI = re.compile(r"(token|password|passwd|secret|api_?key|code_format|access|credential)", re.I)
_SINONIMI = {
    "luce": "light", "luci": "light", "lampada": "light", "lampadina": "light", "faretti": "light",
    "interruttore": "switch", "presa": "switch", "prese": "switch", "spina": "switch",
    "tapparella": "cover", "tapparelle": "cover", "serranda": "cover", "tenda": "cover", "tende": "cover",
    "persiana": "cover", "garage": "cover garage", "cancello": "cover gate",
    "termostato": "climate", "clima": "climate", "condizionatore": "climate", "climatizzatore": "climate",
    "riscaldamento": "climate heat", "condizionatori": "climate",
    "ventilatore": "fan", "aspirapolvere": "vacuum", "robot": "vacuum", "serratura": "lock",
    "allarme": "alarm_control_panel", "scena": "scene", "scene": "scene", "automazione": "automation",
    "tv": "media_player tv", "televisore": "media_player tv", "cassa": "media_player", "speaker": "media_player",
    "altoparlante": "media_player", "temperatura": "temperature", "umidita": "humidity",
    "consumo": "power", "potenza": "power", "energia": "energy", "batteria": "battery",
    "autonomia": "range battery", "porta": "door", "finestra": "window", "movimento": "motion",
    "presenza": "occupancy presence", "sensore": "sensor", "pulsante": "button", "meteo": "weather",
}


class HassError(UserError):
    pass


class HassClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.session.verify = HASS_VERIFY_SSL
        retry = Retry(total=3, backoff_factor=0.4, status_forcelist=(502, 503, 504),
                      allowed_methods=("GET",), raise_on_status=False)
        self.session.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=10))
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=10))
        self._lock = threading.Lock()
        self._stati: Optional[list[dict]] = None
        self._stati_ts = 0.0
        self._aree: dict[str, str] = {}
        self._aree_ts = 0.0
        self._servizi: Optional[dict] = None
        self._servizi_ts = 0.0
        self.audit = DATA_DIR / "hass_audit.log"

    def _req(self, metodo: str, path: str, **kw) -> Any:
        kw.setdefault("timeout", 15)
        try:
            r = self.session.request(metodo, f"{self.url}{path}", **kw)
        except requests.exceptions.SSLError as e:
            raise HassError(f"Errore SSL verso Home Assistant ({e}). Controlla HASS_URL/HASS_VERIFY_SSL.")
        except requests.exceptions.ConnectionError:
            raise HassError(f"Home Assistant non raggiungibile su {self.url}.")
        except requests.exceptions.Timeout:
            raise HassError("Home Assistant non ha risposto in tempo.")
        if r.status_code == 401:
            raise HassError("Token Home Assistant non valido o scaduto (HASS_TOKEN).")
        if r.status_code == 403:
            raise HassError("Operazione non autorizzata per questo utente di Home Assistant.")
        if r.status_code == 404:
            raise HassError("Risorsa non trovata in Home Assistant.")
        if r.status_code == 400:
            raise HassError(f"Richiesta rifiutata da Home Assistant: {r.text[:300]}")
        if r.status_code >= 500:
            raise HassError(f"Errore interno di Home Assistant (HTTP {r.status_code}): {r.text[:200]}")
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        return r.json() if "json" in ct else r.text

    # --- letture ---
    def stati(self, forza: bool = False) -> list[dict]:
        with self._lock:
            if not forza and self._stati is not None and time.monotonic() - self._stati_ts < HASS_CACHE_TTL:
                return self._stati
        stati = self._req("GET", "/api/states")
        with self._lock:
            self._stati, self._stati_ts = stati, time.monotonic()
        return stati

    def stato(self, entity_id: str) -> dict:
        valida_entity(entity_id)
        try:
            return self._req("GET", f"/api/states/{entity_id}")
        except HassError as e:
            if "non trovata" in str(e):
                raise HassError(f"Entita' '{entity_id}' inesistente. Usa hass_cerca_entita per trovare l'ID corretto.")
            raise

    def aree(self) -> dict[str, str]:
        with self._lock:
            if self._aree and time.monotonic() - self._aree_ts < 300:
                return self._aree
        tpl = ("{{ {'e': states | map(attribute='entity_id') | list, "
               "'a': states | map(attribute='entity_id') | map('area_name') | list} | tojson }}")
        try:
            testo = self._req("POST", "/api/template", json={"template": tpl})
            dati = json.loads(testo) if isinstance(testo, str) else testo
            mappa = {e: a for e, a in zip(dati["e"], dati["a"]) if a}
        except Exception as e:
            logger.warning(f"Aree HA non disponibili (serve un token admin per /api/template): {e}")
            mappa = {}
        with self._lock:
            self._aree, self._aree_ts = mappa, time.monotonic()
        return mappa

    def servizi(self) -> dict[str, dict]:
        with self._lock:
            if self._servizi is not None and time.monotonic() - self._servizi_ts < 600:
                return self._servizi
        dati = self._req("GET", "/api/services")
        mappa = {d["domain"]: d.get("services", {}) for d in dati}
        with self._lock:
            self._servizi, self._servizi_ts = mappa, time.monotonic()
        return mappa

    def storico(self, entity_id: str, ore: float) -> list[dict]:
        valida_entity(entity_id)
        inizio = (datetime.now(timezone.utc) - timedelta(hours=ore)).isoformat()
        dati = self._req("GET", f"/api/history/period/{quote(inizio)}",
                         params={"filter_entity_id": entity_id, "minimal_response": "",
                                 "no_attributes": "", "significant_changes_only": ""}, timeout=30)
        return dati[0] if dati else []

    # --- scritture ---
    def chiama_servizio(self, dominio: str, servizio: str, dati: dict, risposta: bool = False) -> Any:
        path = f"/api/services/{dominio}/{servizio}" + ("?return_response" if risposta else "")
        res = self._req("POST", path, json=dati, timeout=30)
        with self._lock:
            self._stati_ts = 0.0  # invalida cache
        return res

    def registra_audit(self, testo: str) -> None:
        with contextlib.suppress(Exception):
            with open(self.audit, "a", encoding="utf-8") as fh:
                fh.write(f"{now().isoformat(timespec='seconds')} {testo}\n")


HASS = HassClient(HASS_URL, HASS_TOKEN) if HASS_TOKEN else None


def valida_entity(entity_id: str) -> str:
    if not entity_id or not _RE_ENTITY.match(entity_id):
        raise HassError(f"entity_id non valido: '{entity_id}' (formato atteso: dominio.nome, es. light.cucina)")
    return entity_id


def _hass() -> HassClient:
    if HASS is None:
        raise UserError("Home Assistant non configurato (imposta HASS_URL e HASS_TOKEN).")
    return HASS


def _valore_attr(k: str, v: Any) -> str:
    if _ATTR_SENSIBILI.search(k):
        return "[nascosto]"
    if k == "brightness" and isinstance(v, (int, float)):
        return f"{round(v / 255 * 100)}%"
    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    return s if len(s) <= 120 else s[:117] + "..."


def formatta_entita(e: dict, aree: dict[str, str], dettaglio: bool = False) -> str:
    eid = e.get("entity_id", "?")
    attrs = e.get("attributes", {}) or {}
    nome = attrs.get("friendly_name", "")
    unita = attrs.get("unit_of_measurement", "")
    riga = f"• {eid}"
    if nome:
        riga += f" «{nome}»"
    riga += f" = {e.get('state')}{(' ' + unita) if unita else ''}"
    if eid in aree:
        riga += f" [area: {aree[eid]}]"
    if dettaglio:
        with contextlib.suppress(Exception):
            lc = datetime.fromisoformat(e["last_changed"].replace("Z", "+00:00"))
            riga += f"\n    ultimo cambio: {fmt_dt(lc)} ({fa_quanto(lc)})"
        extra = [(k, v) for k, v in attrs.items() if k not in _ATTR_RUMOROSI and v is not None]
        for k, v in extra[:25]:
            riga += f"\n    {k}: {_valore_attr(k, v)}"
        for k in ("hvac_modes", "preset_modes", "fan_modes", "source_list", "options", "effect_list"):
            if attrs.get(k):
                riga += f"\n    {k}: {_valore_attr(k, attrs[k])}"
    else:
        utili = []
        for k in ("device_class", "current_temperature", "temperature", "hvac_action", "current_position",
                  "brightness", "media_title", "media_artist", "volume_level", "battery_level"):
            if attrs.get(k) is not None:
                utili.append(f"{k}={_valore_attr(k, attrs[k])}")
        if utili:
            riga += f" ({', '.join(utili[:4])})"
    return riga


def _testo_ricerca(e: dict, aree: dict[str, str]) -> str:
    attrs = e.get("attributes", {}) or {}
    return normalizza(" ".join(str(x) for x in (
        e["entity_id"], attrs.get("friendly_name", ""), aree.get(e["entity_id"], ""),
        attrs.get("device_class", ""), e["entity_id"].split(".")[0])))


def cerca_entita(stati: list[dict], aree: dict[str, str], testo: str = "", dominio: str = "",
                 area: str = "") -> list[tuple[float, dict]]:
    dominio = (dominio or "").strip().lower()
    area_n = normalizza(area) if area else ""
    toks = []
    for t in normalizza(testo).split():
        if t in _SINONIMI:
            toks.append((t, _SINONIMI[t].split()))
        elif len(t) > 1 and t not in ("il", "la", "lo", "le", "di", "del", "della", "in", "e", "dei", "delle"):
            toks.append((t, []))
    risultati = []
    for e in stati:
        eid = e["entity_id"]
        if dominio and not eid.startswith(dominio + "."):
            continue
        if area_n and area_n not in normalizza(aree.get(eid, "")) and area_n not in normalizza(
                e.get("attributes", {}).get("friendly_name", "")):
            continue
        hay = _testo_ricerca(e, aree)
        parole = hay.split()
        punteggio = 0.0
        trovati = 0
        for t, sin in toks:
            if t in hay or any(s in hay for s in sin):
                trovati += 1
                punteggio += 3 if re.search(rf"\b{re.escape(t)}\b", hay) else 2
            else:
                migliore = max((SequenceMatcher(None, t, p).ratio() for p in parole), default=0)
                if migliore >= 0.8:
                    trovati += 1
                    punteggio += migliore
        if toks and trovati == 0:
            continue
        punteggio += trovati * 5 - (len(toks) - trovati) * 4
        if e.get("state") in ("unavailable", "unknown"):
            punteggio -= 1
        risultati.append((punteggio, e))
    risultati.sort(key=lambda x: (-x[0], x[1]["entity_id"]))
    return risultati


@tool(
    "hass_cerca_entita",
    title="Cerca dispositivi della casa",
    description=(
        "Cerca entita' di Home Assistant per nome, stanza/area, tipo o dominio e ne mostra lo stato attuale. "
        "Capisce termini italiani (luce, tapparella, termostato, temperatura, batteria, consumo...). "
        "Esempi: testo='temperatura soggiorno'; testo='mokka autonomia'; dominio='light' area='cucina'. "
        "Usalo SEMPRE per trovare l'entity_id corretto prima di leggere o controllare un dispositivo."
    ),
    group="hass",
    read_only=True,
)
async def hass_cerca_entita(testo: str = "", dominio: str = "", area: str = "", limite: int = 25) -> str:
    h = _hass()
    stati, aree = await asyncio.gather(asyncio.to_thread(h.stati), asyncio.to_thread(h.aree))
    risultati = cerca_entita(stati, aree, testo, dominio, area)
    limite = max(1, min(int(limite or 25), 100))
    if not risultati:
        return (f"Nessuna entita' trovata per testo='{testo}' dominio='{dominio}' area='{area}'. "
                f"Prova termini diversi o hass_panoramica per vedere domini e aree.")
    righe = [formatta_entita(e, aree) for _, e in risultati[:limite]]
    extra = f"\n(+{len(risultati) - limite} altre: affina la ricerca)" if len(risultati) > limite else ""
    return f"{min(len(risultati), limite)} entita' trovate:\n" + "\n".join(righe) + extra


@tool(
    "hass_stato",
    title="Stato dei dispositivi",
    description=(
        "Legge lo stato attuale (in tempo reale) e tutti gli attributi di una o piu' entita' di Home Assistant. "
        "entity_id: uno o piu' ID separati da virgola (es. 'sensor.temperatura_soggiorno,light.cucina')."
    ),
    group="hass",
    read_only=True,
)
async def hass_stato(entity_id: str) -> str:
    h = _hass()
    ids = [x.strip() for x in entity_id.split(",") if x.strip()][:20]
    if not ids:
        raise UserError("Specifica almeno un entity_id.")
    for i in ids:
        valida_entity(i)
    aree = await asyncio.to_thread(h.aree)
    risultati = await asyncio.gather(*[asyncio.to_thread(h.stato, i) for i in ids], return_exceptions=True)
    righe = []
    for i, r in zip(ids, risultati):
        righe.append(f"• {i}: {r}" if isinstance(r, Exception) else formatta_entita(r, aree, dettaglio=True))
    return "\n".join(righe)


@tool(
    "hass_panoramica",
    title="Panoramica della casa",
    description=(
        "Riepilogo della casa: numero di entita' per dominio, elenco aree, e cosa e' attivo ora "
        "(luci accese, dispositivi in funzione, porte/finestre aperte, clima, media in riproduzione, avvisi)."
    ),
    group="hass",
    read_only=True,
)
async def hass_panoramica() -> str:
    h = _hass()
    stati, aree = await asyncio.gather(asyncio.to_thread(h.stati, True), asyncio.to_thread(h.aree))
    domini: dict[str, int] = {}
    for e in stati:
        d = e["entity_id"].split(".")[0]
        domini[d] = domini.get(d, 0) + 1
    righe = [f"Home Assistant: {len(stati)} entita'",
             "Domini: " + ", ".join(f"{d} ({n})" for d, n in sorted(domini.items(), key=lambda x: -x[1]))]
    nomi_aree = sorted(set(aree.values()))
    if nomi_aree:
        righe.append("Aree: " + ", ".join(nomi_aree))

    def sel(pred, titolo, max_n=30):
        trovati = [e for e in stati if pred(e)]
        if trovati:
            righe.append(f"\n{titolo} ({len(trovati)}):")
            righe.extend(formatta_entita(e, aree) for e in trovati[:max_n])

    dc = lambda e: e.get("attributes", {}).get("device_class")  # noqa: E731
    sel(lambda e: e["entity_id"].startswith("light.") and e["state"] == "on", "Luci accese")
    sel(lambda e: e["entity_id"].startswith(("switch.", "fan.", "vacuum.")) and e["state"] in ("on", "cleaning"),
        "Interruttori/dispositivi accesi")
    sel(lambda e: e["entity_id"].startswith("binary_sensor.") and dc(e) in ("door", "window", "garage_door", "opening")
        and e["state"] == "on", "Porte/finestre aperte")
    sel(lambda e: e["entity_id"].startswith("cover.") and e["state"] in ("open", "opening"), "Coperture aperte")
    sel(lambda e: e["entity_id"].startswith("climate.") and e["state"] not in ("off", "unavailable"), "Clima attivo")
    sel(lambda e: e["entity_id"].startswith("media_player.") and e["state"] in ("playing", "paused"), "Media")
    sel(lambda e: e["entity_id"].startswith(("lock.", "alarm_control_panel.")), "Sicurezza")
    sel(lambda e: e["entity_id"].startswith("persistent_notification.") or
        (e["entity_id"].startswith("update.") and e["state"] == "on"), "Notifiche/aggiornamenti", 10)
    non_disp = sum(1 for e in stati if e["state"] == "unavailable")
    if non_disp:
        righe.append(f"\nEntita' non disponibili: {non_disp}")
    return "\n".join(righe)


@tool(
    "hass_storico",
    title="Storico dei dispositivi",
    description=(
        "Storico di un'entita' nelle ultime N ore (max 168): per sensori numerici min/max/media e andamento, "
        "per gli altri l'elenco dei cambi di stato. Es: 'quanto ha consumato oggi', 'quando si e' aperta la porta'."
    ),
    group="hass",
    read_only=True,
)
async def hass_storico(entity_id: str, ore: float = 24) -> str:
    h = _hass()
    ore = max(0.1, min(float(ore or 24), 168))
    dati = await asyncio.to_thread(h.storico, entity_id.strip(), ore)
    if not dati:
        return f"Nessun dato storico per {entity_id} nelle ultime {ore:g} ore."
    punti = []
    for d in dati:
        with contextlib.suppress(Exception):
            ts = datetime.fromisoformat((d.get("last_changed") or d.get("last_updated")).replace("Z", "+00:00"))
            punti.append((ts, d.get("state")))
    unita = (dati[0].get("attributes") or {}).get("unit_of_measurement", "")
    numerici = []
    for ts, s in punti:
        with contextlib.suppress(TypeError, ValueError):
            numerici.append((ts, float(s)))
    righe = [f"Storico {entity_id} – ultime {ore:g} ore ({len(punti)} cambi di stato)"]
    if numerici and len(numerici) >= len(punti) * 0.8:
        valori = [v for _, v in numerici]
        imin = min(range(len(valori)), key=valori.__getitem__)
        imax = max(range(len(valori)), key=valori.__getitem__)
        righe.append(f"Attuale: {valori[-1]:g} {unita} · Min: {valori[imin]:g} {unita} ({fmt_dt(numerici[imin][0])}) · "
                     f"Max: {valori[imax]:g} {unita} ({fmt_dt(numerici[imax][0])}) · "
                     f"Media: {sum(valori) / len(valori):.2f} {unita}")
        passo = max(1, len(numerici) // 24)
        campioni = numerici[::passo][-24:]
        righe.append("Andamento: " + " | ".join(f"{ts.astimezone(TZ):%d/%m %H:%M} {v:g}" for ts, v in campioni))
    else:
        for ts, s in punti[-40:]:
            righe.append(f"- {fmt_dt(ts)}: {s}")
        if len(punti) > 40:
            righe.insert(1, f"(mostrati gli ultimi 40 di {len(punti)})")
    return "\n".join(righe)


@tool(
    "hass_servizi",
    title="Azioni disponibili della casa",
    description="Elenca i servizi (azioni) disponibili per un dominio di Home Assistant con i relativi parametri, es. dominio='climate' o 'media_player'.",
    group="hass",
    read_only=True,
)
async def hass_servizi(dominio: str) -> str:
    h = _hass()
    dominio = dominio.strip().lower()
    servizi = await asyncio.to_thread(h.servizi)
    if dominio not in servizi:
        raise UserError(f"Dominio '{dominio}' senza servizi. Domini disponibili: {', '.join(sorted(servizi))}")
    righe = [f"Servizi del dominio {dominio}" + ("" if dominio in HASS_ALLOWED_DOMAINS else
                                                 " (ATTENZIONE: dominio non abilitato al controllo)")]
    for nome, info in sorted(servizi[dominio].items()):
        if not _servizio_permesso(dominio, nome):
            continue
        campi = info.get("fields", {}) or {}
        desc_campi = ", ".join(
            f"{k}{'*' if v.get('required') else ''}" + (f" (es. {v['example']})" if v.get("example") is not None else "")
            for k, v in campi.items() if isinstance(v, dict) and k != "advanced_fields")
        righe.append(f"- {dominio}.{nome}: {info.get('description', '')[:150]}" +
                     (f"\n    parametri: {desc_campi}" if desc_campi else ""))
    return "\n".join(righe)


_SERVIZI_HOMEASSISTANT = {"turn_on", "turn_off", "toggle", "update_entity"}
_DOMINI_SENZA_ENTITA = {"notify", "persistent_notification", "script", "scene", "tts"}


def _servizio_permesso(dominio: str, servizio: str) -> bool:
    if dominio == "homeassistant":
        return servizio in _SERVIZI_HOMEASSISTANT
    if servizio.startswith("reload") or servizio.endswith("_reload") or "config" in servizio:
        return False
    if dominio == "automation" and servizio not in ("trigger", "turn_on", "turn_off", "toggle"):
        return False
    return True


def _match(entity_id: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(entity_id, p) for p in patterns)


def _richiede_conferma(entita: dict) -> bool:
    eid = entita["entity_id"]
    dom = eid.split(".")[0]
    if dom in HASS_CONFIRM_DOMAINS or _match(eid, HASS_CONFIRM_ENTITIES):
        return True
    dc = (entita.get("attributes") or {}).get("device_class")
    return dom == "cover" and dc in ("garage", "gate", "door")


@tool(
    "hass_esegui_servizio",
    title="Controllo dei dispositivi",
    description=(
        "Esegue un servizio (azione) di Home Assistant su una o piu' entita'. Esempi:\n"
        "- accendere luce al 50%: dominio='light' servizio='turn_on' entity_id='light.cucina' dati={\"brightness_pct\":50}\n"
        "- spegnere: servizio='turn_off'; invertire: 'toggle'\n"
        "- tapparella: dominio='cover' servizio='open_cover'/'close_cover'/'set_cover_position' dati={\"position\":30}\n"
        "- termostato: dominio='climate' servizio='set_temperature' dati={\"temperature\":21}\n"
        "- volume: dominio='media_player' servizio='volume_set' dati={\"volume_level\":0.3}\n"
        "- scena/script: dominio='scene' servizio='turn_on' entity_id='scene.cinema'\n"
        "- previsioni: dominio='weather' servizio='get_forecasts' dati={\"type\":\"daily\"}\n"
        "entity_id accetta piu' ID separati da virgola. Trova prima gli ID con hass_cerca_entita. "
        "Serrature, allarmi, garage e cancelli richiedono conferma=true: chiedi PRIMA il consenso esplicito all'utente."
    ),
    group="hass",
    destructive=True,
)
async def hass_esegui_servizio(dominio: str, servizio: str, entity_id: str = "",
                               dati: Optional[dict[str, Any]] = None, conferma: bool = False) -> str:
    h = _hass()
    if HASS_READ_ONLY:
        raise UserError("Home Assistant e' configurato in sola lettura (HASS_READ_ONLY=true).")
    dominio, servizio = dominio.strip().lower(), servizio.strip().lower()
    if "." in servizio and not dominio:
        dominio, servizio = servizio.split(".", 1)
    if not _RE_SLUG.match(dominio) or not _RE_SLUG.match(servizio):
        raise UserError("Dominio o servizio non validi.")
    if dominio not in HASS_ALLOWED_DOMAINS:
        raise UserError(f"Il dominio '{dominio}' non e' abilitato al controllo. Domini consentiti: "
                        f"{', '.join(sorted(HASS_ALLOWED_DOMAINS))}")
    if not _servizio_permesso(dominio, servizio):
        raise UserError(f"Il servizio {dominio}.{servizio} non e' consentito per motivi di sicurezza.")

    dati = dict(dati or {})
    if isinstance(dati.get("target"), dict):
        dati.update(dati.pop("target"))
    ids_param = [x.strip() for x in entity_id.split(",") if x.strip()]
    ids_dati = dati.pop("entity_id", [])
    ids_dati = [ids_dati] if isinstance(ids_dati, str) else list(ids_dati or [])
    ids = list(dict.fromkeys(ids_param + [str(x).strip() for x in ids_dati]))
    for chiave in ("area_id", "device_id", "floor_id", "label_id"):
        if chiave in dati:
            raise UserError(f"Il parametro '{chiave}' non e' consentito: indica esplicitamente gli entity_id.")

    servizi = await asyncio.to_thread(h.servizi)
    if dominio not in servizi or servizio not in servizi[dominio]:
        disponibili = ", ".join(sorted(servizi.get(dominio, {}))) or "nessuno"
        raise UserError(f"Servizio {dominio}.{servizio} inesistente. Servizi di {dominio}: {disponibili}")
    vuole_risposta = "response" in servizi[dominio][servizio]

    if not ids and dominio not in _DOMINI_SENZA_ENTITA:
        raise UserError("Specifica entity_id (usa hass_cerca_entita per trovarlo).")
    stati = {e["entity_id"]: e for e in await asyncio.to_thread(h.stati)}
    sensibili = []
    for eid in ids:
        if eid == "all":
            raise UserError("entity_id 'all' non consentito: elenca le entita' esplicitamente.")
        valida_entity(eid)
        if eid not in stati:
            stati.update({e["entity_id"]: e for e in await asyncio.to_thread(h.stati, True)})
            if eid not in stati:
                raise UserError(f"Entita' '{eid}' inesistente. Usa hass_cerca_entita.")
        dom_e = eid.split(".")[0]
        if dominio != "homeassistant" and dom_e != dominio and dominio not in ("tts", "notify", "media_player"):
            raise UserError(f"L'entita' {eid} non appartiene al dominio '{dominio}' "
                            f"(usa dominio='{dom_e}' oppure 'homeassistant').")
        if dom_e not in HASS_ALLOWED_DOMAINS:
            raise UserError(f"Le entita' del dominio '{dom_e}' non sono controllabili.")
        if _match(eid, HASS_BLOCKED_ENTITIES):
            raise UserError(f"L'entita' {eid} e' bloccata dalla configurazione di sicurezza.")
        if HASS_ALLOWED_ENTITIES and not _match(eid, HASS_ALLOWED_ENTITIES):
            raise UserError(f"L'entita' {eid} non e' nell'elenco delle entita' controllabili.")
        if _richiede_conferma(stati[eid]):
            sensibili.append(eid)
    if sensibili and not conferma:
        return (f"⚠️ AZIONE SENSIBILE: {dominio}.{servizio} su {', '.join(sensibili)}. "
                f"Chiedi all'utente una conferma esplicita; solo se conferma ripeti la chiamata con conferma=true.")

    if ids:
        dati["entity_id"] = ids if len(ids) > 1 else ids[0]
    h.registra_audit(f"CALL {dominio}.{servizio} {json.dumps(dati, ensure_ascii=False)} conferma={conferma}")
    logger.info(f"[hass] {dominio}.{servizio} {dati}")
    risposta = await asyncio.to_thread(h.chiama_servizio, dominio, servizio, dati, vuole_risposta)

    righe = [f"✅ Eseguito {dominio}.{servizio}" + (f" su {', '.join(ids)}" if ids else "")]
    if isinstance(risposta, dict) and risposta.get("service_response"):
        righe.append("Risposta: " + tronca(json.dumps(risposta["service_response"], ensure_ascii=False, indent=1), 6000))
    if ids:
        await asyncio.sleep(1.0)
        aree = await asyncio.to_thread(h.aree)
        nuovi = await asyncio.gather(*[asyncio.to_thread(h.stato, i) for i in ids[:10]], return_exceptions=True)
        righe.append("Stato attuale:")
        for i, n in zip(ids, nuovi):
            righe.append(f"• {i}: {n}" if isinstance(n, Exception) else formatta_entita(n, aree))
    return "\n".join(righe)


# =============================================================================
# TIMER E SVEGLIE
# =============================================================================
class GestoreTimer:
    def __init__(self) -> None:
        self.store = JsonStore(DATA_DIR / "timers.json", {"attivi": [], "scaduti": []})
        self.timer: dict[str, dict] = {}
        self.scaduti: deque = deque(maxlen=20)
        self._task: dict[str, asyncio.Task] = {}
        self._beep = DATA_DIR / "timer_beep.wav"

    def _salva(self) -> None:
        self.store.save({"attivi": list(self.timer.values()), "scaduti": list(self.scaduti)})

    def avvia(self) -> None:
        dati = self.store.load()
        self.scaduti.extend(dati.get("scaduti", []))
        adesso = time.time()
        for t in dati.get("attivi", []):
            if t["fine"] > adesso - 300:  # recupera anche quelli scaduti da poco (server spento)
                self.timer[t["id"]] = t
                self._programma(t)
            else:
                t["nota"] = "scaduto mentre il server era spento"
                self.scaduti.append(t)
        self._salva()
        if self.timer:
            logger.info(f"Ripristinati {len(self.timer)} timer")

    def ferma(self) -> None:
        for task in self._task.values():
            task.cancel()
        self._task.clear()

    def _programma(self, t: dict) -> None:
        self._task[t["id"]] = asyncio.get_running_loop().create_task(self._attendi(t["id"]))

    async def _attendi(self, tid: str) -> None:
        try:
            while True:
                t = self.timer.get(tid)
                if t is None:
                    return
                rimanente = t["fine"] - time.time()
                if rimanente <= 0:
                    break
                await asyncio.sleep(min(rimanente, 30))
            await self._scatta(tid)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Errore timer {tid}: {e}")

    async def _scatta(self, tid: str) -> None:
        t = self.timer.pop(tid, None)
        self._task.pop(tid, None)
        if t is None:
            return
        t["scattato"] = now().isoformat(timespec="seconds")
        self.scaduti.append(t)
        self._salva()
        tipo = "La sveglia" if t["tipo"] == "sveglia" else "Il timer"
        msg = f"{tipo} {('«' + t['etichetta'] + '» ') if t['etichetta'] else ''}è scaduto!"
        if t["tipo"] == "timer":
            msg += f" (durata {fmt_durata(t['durata'])})"
        logger.info(f"⏰ {msg}")
        await self._notifica(t, msg)

    async def _notifica(self, t: dict, msg: str) -> None:
        compiti = []
        if HASS is not None:
            if TIMER_PERSISTENT_NOTIFICATION:
                compiti.append(("persistent_notification", "create",
                                {"title": "⏰ Timer", "message": msg, "notification_id": f"mcp_timer_{t['id']}"}))
            if TIMER_NOTIFY_SERVICE and "." in TIMER_NOTIFY_SERVICE:
                d, s = TIMER_NOTIFY_SERVICE.split(".", 1)
                compiti.append((d, s, {"title": "⏰ Timer", "message": msg}))
            if TIMER_TTS_ENTITY and TIMER_TTS_PLAYER:
                compiti.append(("tts", "speak", {"entity_id": TIMER_TTS_ENTITY,
                                                 "media_player_entity_id": TIMER_TTS_PLAYER, "message": msg}))
            if TIMER_HASS_SCRIPT:
                compiti.append(("script", "turn_on", {"entity_id": TIMER_HASS_SCRIPT,
                                                      "variables": {"messaggio": msg, "etichetta": t["etichetta"]}}))
        for d, s, dati in compiti:
            try:
                await asyncio.to_thread(HASS.chiama_servizio, d, s, dati)
            except Exception as e:
                logger.warning(f"Notifica timer via {d}.{s} fallita: {e}")
        if TIMER_LOCAL_SOUND:
            await self._suono_locale()

    async def _suono_locale(self) -> None:
        if not self._beep.exists():
            with wave.open(str(self._beep), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                frames = bytearray()
                for rip in range(3):
                    for i in range(int(22050 * 0.35)):
                        frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 880 * i / 22050)))
                    frames += b"\x00\x00" * int(22050 * 0.2)
                w.writeframes(bytes(frames))
        for cmd in (["mpv", "--no-video", "--really-quiet"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
                    ["paplay"], ["aplay", "-q"]):
            if shutil.which(cmd[0]):
                with contextlib.suppress(Exception):
                    p = await asyncio.create_subprocess_exec(*cmd, str(self._beep), stdout=subprocess.DEVNULL,
                                                             stderr=subprocess.DEVNULL)
                    await asyncio.wait_for(p.wait(), 15)
                return

    def crea(self, secondi: int, etichetta: str, tipo: str, fine: Optional[float] = None) -> dict:
        if len(self.timer) >= 50:
            raise UserError("Troppi timer attivi (max 50).")
        t = {
            "id": secrets.token_hex(2),
            "etichetta": etichetta.strip()[:80],
            "tipo": tipo,
            "durata": secondi,
            "creato": time.time(),
            "fine": fine if fine is not None else time.time() + secondi,
        }
        while t["id"] in self.timer:
            t["id"] = secrets.token_hex(2)
        self.timer[t["id"]] = t
        self._salva()
        self._programma(t)
        return t

    def annulla(self, chiave: str) -> list[dict]:
        chiave = chiave.strip()
        if chiave.lower() in ("tutti", "all", "*"):
            eliminati = list(self.timer.values())
        else:
            eliminati = [t for t in self.timer.values()
                         if t["id"] == chiave or (t["etichetta"] and normalizza(t["etichetta"]) == normalizza(chiave))]
        for t in eliminati:
            self.timer.pop(t["id"], None)
            task = self._task.pop(t["id"], None)
            if task:
                task.cancel()
        self._salva()
        return eliminati


TIMER = GestoreTimer()


def _descr_timer(t: dict) -> str:
    rim = t["fine"] - time.time()
    fine = datetime.fromtimestamp(t["fine"], TZ)
    nome = f"«{t['etichetta']}» " if t["etichetta"] else ""
    if t["tipo"] == "sveglia":
        return f"[{t['id']}] ⏰ Sveglia {nome}alle {fine:%H:%M} di {GIORNI[fine.weekday()]} {fine:%d/%m} (tra {fmt_durata(rim)})"
    return f"[{t['id']}] ⏲️ Timer {nome}di {fmt_durata(t['durata'])}: mancano {fmt_durata(rim)} (scade alle {fine:%H:%M:%S})"


@tool(
    "timer_imposta",
    title="Imposta timer o sveglia",
    description=(
        "Imposta un timer (parametro 'durata', es. '10m', '1h30m', '45 secondi', '2 ore e 15 minuti'; un numero "
        "senza unita' = minuti) OPPURE una sveglia/promemoria a un orario (parametro 'orario', es. '07:30', "
        "'domani 9:00', '2026-12-24 18:00'). Supporta piu' timer contemporanei: usa 'etichetta' per distinguerli "
        "(es. 'pasta'). Alla scadenza viene inviata una notifica tramite Home Assistant."
    ),
)
async def timer_imposta(durata: str = "", etichetta: str = "", orario: str = "") -> str:
    if orario.strip():
        dt, ha_ora = parse_data_ora(orario)
        if not ha_ora:
            raise UserError("Per la sveglia indica anche l'ora (es. 'domani 7:30').")
        if dt <= now():
            if _parse_ora(orario) is not None:  # solo ora: la prossima occorrenza
                dt += timedelta(days=1)
            else:
                raise UserError(f"L'orario {fmt_dt(dt)} e' gia' passato.")
        sec = int((dt - now()).total_seconds())
        t = TIMER.crea(sec, etichetta, "sveglia", fine=dt.timestamp())
    elif durata.strip():
        sec = parse_durata(durata)
        if sec < 1 or sec > 7 * 86400:
            raise UserError("La durata deve essere tra 1 secondo e 7 giorni.")
        t = TIMER.crea(sec, etichetta, "timer")
    else:
        raise UserError("Indica 'durata' (es. '10m') oppure 'orario' (es. '07:30').")
    altri = len(TIMER.timer) - 1
    return "Impostato: " + _descr_timer(t) + (f"\n(altri timer attivi: {altri})" if altri else "")


@tool(
    "timer_lista",
    title="Elenco timer",
    description="Mostra i timer e le sveglie attivi con il tempo rimanente, e quelli scaduti di recente. Usalo per 'quanto manca al timer?'.",
    read_only=True,
)
async def timer_lista() -> str:
    righe = []
    attivi = sorted(TIMER.timer.values(), key=lambda t: t["fine"])
    righe.append(f"Timer/sveglie attivi: {len(attivi)}")
    righe.extend(_descr_timer(t) for t in attivi)
    recenti = [t for t in TIMER.scaduti if t.get("fine", 0) > time.time() - 6 * 3600]
    if recenti:
        righe.append("\nScaduti nelle ultime 6 ore:")
        for t in recenti[-10:]:
            nome = f"«{t['etichetta']}» " if t["etichetta"] else ""
            righe.append(f"- {t['tipo']} {nome}scaduto alle {datetime.fromtimestamp(t['fine'], TZ):%H:%M}"
                         + (f" ({t['nota']})" if t.get("nota") else ""))
    return "\n".join(righe)


@tool(
    "timer_annulla",
    title="Annulla timer",
    description="Annulla un timer o una sveglia tramite id o etichetta (vedi timer_lista), oppure tutti con 'tutti'.",
    destructive=True,
)
async def timer_annulla(id_o_etichetta: str) -> str:
    eliminati = TIMER.annulla(id_o_etichetta)
    if not eliminati:
        raise UserError(f"Nessun timer attivo corrisponde a '{id_o_etichetta}'. Usa timer_lista.")
    return "Annullati:\n" + "\n".join(_descr_timer(t) for t in eliminati)


# =============================================================================
# MUSICA (YouTube) - stile Alexa con riproduzione continua di brani simili
# =============================================================================
@dataclass
class Brano:
    video_id: str
    titolo: str
    artista: str = ""
    durata: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    def descr(self) -> str:
        return f"«{self.titolo}»" + (f" di {self.artista}" if self.artista else "") + \
            (f" ({self.durata})" if self.durata else "")


_RE_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


class YouTube:
    def __init__(self) -> None:
        self._ytm = None
        self._lock = threading.Lock()

    def _ytmusic(self):
        if YTMusic is None:
            return None
        with self._lock:
            if self._ytm is None:
                try:
                    self._ytm = YTMusic(language="it", location="IT")
                except Exception as e:
                    logger.warning(f"YTMusic non inizializzabile: {e}")
                    return None
            return self._ytm

    @staticmethod
    def _ydl(extra: Optional[dict] = None):
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                "socket_timeout": 15}
        opts.update(extra or {})
        return yt_dlp.YoutubeDL(opts)

    @staticmethod
    def _artisti(item: dict) -> str:
        arts = item.get("artists") or []
        return ", ".join(a.get("name", "") for a in arts if isinstance(a, dict) and a.get("name"))

    def cerca(self, query: str, n: int = 5) -> list[Brano]:
        m = _RE_YT_ID.search(query)
        if m:
            with self._ydl() as ydl:
                info = ydl.extract_info(m.group(1), download=False)
            return [Brano(info["id"], info.get("title", ""), info.get("artist") or info.get("uploader", ""),
                          fmt_durata(info.get("duration") or 0))]
        ytm = self._ytmusic()
        if ytm is not None:
            for filtro in ("songs", "videos"):
                try:
                    res = [r for r in ytm.search(query, filter=filtro, limit=n) if r.get("videoId")]
                    if res:
                        return [Brano(r["videoId"], r.get("title", ""), self._artisti(r), r.get("duration") or "")
                                for r in res[:n]]
                except Exception as e:
                    logger.warning(f"Ricerca YouTube Music fallita ({filtro}): {e}")
        with self._ydl({"extract_flat": True}) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
        return [Brano(e["id"], e.get("title", ""), e.get("channel") or e.get("uploader") or "",
                      fmt_durata(e.get("duration") or 0) if e.get("duration") else "")
                for e in (info.get("entries") or []) if e.get("id")]

    def simili(self, video_id: str, n: int = 30) -> list[Brano]:
        ytm = self._ytmusic()
        if ytm is not None:
            try:
                res = ytm.get_watch_playlist(videoId=video_id, radio=True, limit=n)
                brani = [Brano(t["videoId"], t.get("title", ""), self._artisti(t), t.get("length") or "")
                         for t in res.get("tracks", []) if t.get("videoId")]
                if brani:
                    return brani
            except Exception as e:
                logger.warning(f"Radio YouTube Music fallita: {e}")
        with self._ydl({"extract_flat": True, "noplaylist": False, "playlistend": n}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}", download=False)
        return [Brano(e["id"], e.get("title", ""), e.get("channel") or e.get("uploader") or "")
                for e in (info.get("entries") or []) if e.get("id")]

    def stream(self, video_id: str) -> dict:
        with self._ydl({"format": "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best"}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return {"url": info["url"], "titolo": info.get("title", ""), "thumb": info.get("thumbnail", ""),
                "mime": "audio/mp4" if info.get("ext") == "m4a" else "audio/webm"}


class BackendLocale:
    """Riproduzione locale con mpv (controllato via socket IPC JSON)."""

    nome = "locale (mpv)"

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.sock = DATA_DIR / "mpv.sock"
        self._rid = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def disponibile() -> bool:
        return shutil.which("mpv") is not None

    async def _assicura(self) -> None:
        if self.proc is not None and self.proc.returncode is None and self.sock.exists():
            return
        if not self.disponibile():
            raise UserError("mpv non installato: impossibile riprodurre in locale (apt install mpv) "
                            "oppure indica un media_player di Home Assistant come dispositivo.")
        self.sock.unlink(missing_ok=True)
        ytdl = shutil.which("yt-dlp") or str(Path(sys.executable).parent / "yt-dlp")
        args = ["mpv", "--idle=yes", "--no-video", "--no-terminal", "--really-quiet", "--force-window=no",
                f"--input-ipc-server={self.sock}", f"--volume={MUSIC_DEFAULT_VOLUME}",
                "--ytdl-format=bestaudio/best", f"--script-opts=ytdl_hook-ytdl_path={ytdl}"]
        if MUSIC_MPV_AUDIO_DEVICE:
            args.append(f"--audio-device={MUSIC_MPV_AUDIO_DEVICE}")
        if MUSIC_MPV_AO:
            args.append(f"--ao={MUSIC_MPV_AO}")
        self.proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                         stdin=subprocess.DEVNULL)
        for _ in range(50):
            if self.sock.exists():
                return
            await asyncio.sleep(0.1)
        raise RuntimeError("mpv non ha creato il socket IPC")

    async def _cmd(self, *comando) -> Any:
        async with self._lock:
            await self._assicura()
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(self.sock)), 3)
            try:
                self._rid += 1
                rid = self._rid
                writer.write((json.dumps({"command": list(comando), "request_id": rid}) + "\n").encode())
                await writer.drain()
                while True:
                    riga = await asyncio.wait_for(reader.readline(), 5)
                    if not riga:
                        raise RuntimeError("connessione mpv chiusa")
                    msg = json.loads(riga)
                    if msg.get("request_id") == rid:
                        if msg.get("error") not in (None, "success"):
                            raise RuntimeError(msg["error"])
                        return msg.get("data")
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    async def _prop(self, nome: str) -> Any:
        try:
            return await self._cmd("get_property", nome)
        except RuntimeError:
            return None

    async def riproduci(self, brano: Brano) -> None:
        await self._cmd("loadfile", brano.url, "replace")
        await self._cmd("set_property", "pause", False)

    async def stato(self) -> str:
        if self.proc is None or self.proc.returncode is not None:
            return "idle"
        if await self._prop("idle-active"):
            return "idle"
        return "paused" if await self._prop("pause") else "playing"

    async def posizione(self) -> str:
        pos, dur = await self._prop("time-pos"), await self._prop("duration")
        if pos is None:
            return ""
        return f"{fmt_durata(pos)}" + (f" / {fmt_durata(dur)}" if dur else "")

    async def pausa(self) -> None:
        await self._cmd("set_property", "pause", True)

    async def riprendi(self) -> None:
        await self._cmd("set_property", "pause", False)

    async def stop(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            await self._cmd("stop")

    async def volume(self) -> int:
        v = await self._prop("volume")
        return int(v) if v is not None else MUSIC_DEFAULT_VOLUME

    async def imposta_volume(self, v: int) -> None:
        await self._cmd("set_property", "volume", max(0, min(100, v)))

    async def chiudi(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._cmd("quit"), 2)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.proc.wait(), 3)
            if self.proc.returncode is None:
                self.proc.kill()
        self.sock.unlink(missing_ok=True)


class BackendHass:
    """Riproduzione su un media_player di Home Assistant (Chromecast, Sonos, Music Assistant...)."""

    def __init__(self, entity_id: str, yt: YouTube) -> None:
        self.entity_id = entity_id
        self.yt = yt
        self.nome = entity_id
        self._url_corrente = ""

    async def _servizio(self, servizio: str, **dati) -> None:
        await asyncio.to_thread(_hass().chiama_servizio, "media_player", servizio,
                                {"entity_id": self.entity_id, **dati})

    async def _stato_ha(self) -> dict:
        return await asyncio.to_thread(_hass().stato, self.entity_id)

    async def riproduci(self, brano: Brano) -> None:
        info = await asyncio.to_thread(self.yt.stream, brano.video_id)
        self._url_corrente = info["url"]
        extra = {"title": brano.titolo, "thumb": info.get("thumb", ""),
                 "metadata": {"metadataType": 3, "title": brano.titolo, "artist": brano.artista,
                              "images": [{"url": info.get("thumb", "")}] if info.get("thumb") else []}}
        try:
            await self._servizio("play_media", media_content_id=info["url"], media_content_type="music", extra=extra)
        except HassError:
            await self._servizio("play_media", media_content_id=info["url"], media_content_type="music")

    async def stato(self) -> str:
        s = await self._stato_ha()
        st = s.get("state", "")
        cid = (s.get("attributes") or {}).get("media_content_id")
        if st == "playing" and cid and self._url_corrente and cid != self._url_corrente:
            return "altro"  # qualcos'altro ha preso il controllo del dispositivo
        if st in ("idle", "off", "standby", "stopped"):
            return "idle"
        return {"playing": "playing", "paused": "paused"}.get(st, "attesa")

    async def posizione(self) -> str:
        a = (await self._stato_ha()).get("attributes") or {}
        pos, dur = a.get("media_position"), a.get("media_duration")
        return f"{fmt_durata(pos)} / {fmt_durata(dur)}" if pos is not None and dur else ""

    async def pausa(self) -> None:
        await self._servizio("media_pause")

    async def riprendi(self) -> None:
        await self._servizio("media_play")

    async def stop(self) -> None:
        await self._servizio("media_stop")

    async def volume(self) -> int:
        v = ((await self._stato_ha()).get("attributes") or {}).get("volume_level")
        return int(round(v * 100)) if v is not None else 50

    async def imposta_volume(self, v: int) -> None:
        await self._servizio("volume_set", volume_level=max(0, min(100, v)) / 100)

    async def chiudi(self) -> None:
        pass


class LettoreMusicale:
    def __init__(self) -> None:
        self.yt = YouTube()
        self.coda: list[Brano] = []
        self.indice = -1
        self.backend: Any = None
        self._locale: Optional[BackendLocale] = None
        self.attivo = False
        self.radio = True
        self._lock = asyncio.Lock()
        self._monitor: Optional[asyncio.Task] = None
        self._avvio = 0.0
        self._visto_play = False
        self._fallimenti = 0
        self._estendendo = False
        self._ids: set[str] = set()

    @property
    def corrente(self) -> Optional[Brano]:
        return self.coda[self.indice] if 0 <= self.indice < len(self.coda) else None

    def avvia_monitor(self) -> None:
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.get_running_loop().create_task(self._ciclo_monitor())

    async def chiudi(self) -> None:
        if self._monitor:
            self._monitor.cancel()
        if self._locale:
            await self._locale.chiudi()

    async def risolvi_dispositivo(self, dispositivo: str) -> Any:
        d = (dispositivo or MUSIC_DEFAULT_DEVICE or "locale").strip()
        if normalizza(d) in ("locale", "local", "server", "mpv", "questo dispositivo"):
            if self._locale is None:
                self._locale = BackendLocale()
            return self._locale
        if HASS is None:
            raise UserError("Per riprodurre su un altoparlante serve Home Assistant configurato.")
        if _RE_ENTITY.match(d):
            if not d.startswith("media_player."):
                raise UserError("Il dispositivo deve essere un media_player.")
            await asyncio.to_thread(HASS.stato, d)
        else:
            stati, aree = await asyncio.gather(asyncio.to_thread(HASS.stati), asyncio.to_thread(HASS.aree))
            trovati = cerca_entita(stati, aree, d, "media_player")
            if not trovati:
                disponibili = [e["entity_id"] for e in stati if e["entity_id"].startswith("media_player.")]
                raise UserError(f"Nessun altoparlante corrisponde a '{d}'. Disponibili: {', '.join(disponibili) or 'nessuno'}")
            d = trovati[0][1]["entity_id"]
        if _match(d, HASS_BLOCKED_ENTITIES):
            raise UserError(f"Il dispositivo {d} e' bloccato dalla configurazione.")
        if isinstance(self.backend, BackendHass) and self.backend.entity_id == d:
            return self.backend
        return BackendHass(d, self.yt)

    async def _avvia_corrente(self) -> None:
        brano = self.corrente
        self.attivo = True
        self._avvio = time.monotonic()
        self._visto_play = False
        logger.info(f"[musica] riproduco {brano.descr()} su {self.backend.nome}")
        await self.backend.riproduci(brano)

    async def _estendi_coda(self) -> None:
        if self._estendendo or not self.coda:
            return
        self._estendendo = True
        try:
            seme = self.coda[-1]
            nuovi = await asyncio.to_thread(self.yt.simili, seme.video_id, 30)
            aggiunti = 0
            titoli = {normalizza(b.titolo) for b in self.coda}
            for b in nuovi:
                if b.video_id in self._ids or normalizza(b.titolo) in titoli:
                    continue
                self.coda.append(b)
                self._ids.add(b.video_id)
                titoli.add(normalizza(b.titolo))
                aggiunti += 1
                if aggiunti >= 20:
                    break
            logger.info(f"[musica] aggiunti {aggiunti} brani simili a {seme.descr()}")
        except Exception as e:
            logger.warning(f"[musica] impossibile trovare brani simili: {e}")
        finally:
            self._estendendo = False

    async def riproduci(self, richiesta: str, dispositivo: str, simili: bool) -> str:
        brani = await asyncio.to_thread(self.yt.cerca, richiesta, 5)
        if not brani:
            raise UserError(f"Nessun brano trovato su YouTube per '{richiesta}'.")
        nuovo_backend = await self.risolvi_dispositivo(dispositivo)
        async with self._lock:
            if self.backend is not None and self.backend is not nuovo_backend and self.attivo:
                with contextlib.suppress(Exception):
                    await self.backend.stop()
            self.backend = nuovo_backend
            self.coda, self.indice, self.radio = [brani[0]], 0, simili
            self._ids = {brani[0].video_id}
            self._fallimenti = 0
            await self._avvia_corrente()
        self.avvia_monitor()
        if simili:
            asyncio.get_running_loop().create_task(self._estendi_coda())
        return (f"▶️ In riproduzione: {brani[0].descr()} su {self.backend.nome}.\n"
                + ("Al termine continuerò con brani simili (radio)." if simili else "Riproduzione del solo brano richiesto.")
                + "\nAlternative trovate: " + "; ".join(b.descr() for b in brani[1:4]))

    async def accoda(self, richiesta: str) -> str:
        brani = await asyncio.to_thread(self.yt.cerca, richiesta, 3)
        if not brani:
            raise UserError(f"Nessun brano trovato per '{richiesta}'.")
        b = brani[0]
        async with self._lock:
            if not self.attivo or self.corrente is None:
                pass
            else:
                self.coda.insert(self.indice + 1, b)
                self._ids.add(b.video_id)
                return f"Aggiunto come prossimo brano: {b.descr()}"
        return await self.riproduci(richiesta, "", self.radio)

    async def successivo(self) -> str:
        if not self.coda:
            raise UserError("Nessuna riproduzione in corso.")
        if self.indice + 1 >= len(self.coda) and self.radio:
            await self._estendi_coda()
        if self.indice + 1 >= len(self.coda):
            self.attivo = False
            with contextlib.suppress(Exception):
                await self.backend.stop()
            return "Coda terminata: non ci sono altri brani."
        self.indice += 1
        await self._avvia_corrente()
        if self.radio and len(self.coda) - self.indice <= 3:
            asyncio.get_running_loop().create_task(self._estendi_coda())
        return f"⏭️ Ora in riproduzione: {self.corrente.descr()}"

    async def precedente(self) -> str:
        if self.indice <= 0:
            raise UserError("Non c'e' un brano precedente.")
        self.indice -= 1
        await self._avvia_corrente()
        return f"⏮️ Ora in riproduzione: {self.corrente.descr()}"

    async def _ciclo_monitor(self) -> None:
        while True:
            try:
                await asyncio.sleep(2)
                if not self.attivo or self.backend is None:
                    continue
                st = await self.backend.stato()
                if st == "playing":
                    self._visto_play = True
                    self._fallimenti = 0
                    continue
                if st == "altro":
                    logger.info("[musica] il dispositivo sta riproducendo altro: interrompo la coda")
                    self.attivo = False
                    continue
                if st != "idle":
                    continue
                trascorso = time.monotonic() - self._avvio
                if not self._visto_play and trascorso < MUSIC_START_TIMEOUT:
                    continue
                if not self._visto_play:
                    self._fallimenti += 1
                    logger.warning(f"[musica] il brano non e' partito ({self._fallimenti}/3)")
                    if self._fallimenti >= 3:
                        self.attivo = False
                        continue
                async with self._lock:
                    if self.attivo:
                        msg = await self.successivo()
                        logger.info(f"[musica] {msg}")
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"[musica] errore nel monitor: {e}")
                await asyncio.sleep(5)


LETTORE = LettoreMusicale()


@tool(
    "musica_riproduci",
    title="Riproduci musica",
    description=(
        "Riproduce musica da YouTube come un assistente vocale: cerca il brano/artista/genere richiesto "
        "(es. 'Bohemian Rhapsody Queen', 'musica jazz rilassante', 'ultimo singolo di Elodie' o un link YouTube) "
        "e, finito il brano, continua automaticamente con brani simili. 'dispositivo': 'locale' oppure il nome/"
        "entity_id di un altoparlante di Home Assistant (es. 'cucina' o 'media_player.soggiorno'); vuoto = predefinito. "
        "simili=false per riprodurre solo il brano richiesto."
    ),
    group="music",
)
async def musica_riproduci(richiesta: str, dispositivo: str = "", simili: bool = True) -> str:
    if not richiesta.strip():
        raise UserError("Indica cosa riprodurre.")
    return await LETTORE.riproduci(richiesta.strip(), dispositivo, simili)


@tool(
    "musica_controlla",
    title="Controlla musica",
    description=(
        "Controlla la riproduzione musicale in corso. azione: 'pausa', 'riprendi', 'stop', 'successivo' (salta), "
        "'precedente', 'volume' (valore 0-100), 'alza_volume' / 'abbassa_volume' (valore = passo, default 10), "
        "'radio_on' / 'radio_off' (continua o no con brani simili)."
    ),
    group="music",
)
async def musica_controlla(
    azione: Literal["pausa", "riprendi", "stop", "successivo", "precedente", "volume",
                    "alza_volume", "abbassa_volume", "radio_on", "radio_off"],
    valore: Optional[int] = None,
) -> str:
    L = LETTORE
    if L.backend is None:
        raise UserError("Nessuna riproduzione avviata.")
    if azione == "pausa":
        await L.backend.pausa()
        return "⏸️ In pausa."
    if azione == "riprendi":
        if not L.attivo and L.corrente:
            async with L._lock:
                await L._avvia_corrente()
            return f"▶️ Riprendo: {L.corrente.descr()}"
        await L.backend.riprendi()
        L.attivo = True
        return "▶️ Riprendo la riproduzione."
    if azione == "stop":
        L.attivo = False
        await L.backend.stop()
        return "⏹️ Riproduzione interrotta."
    if azione == "successivo":
        async with L._lock:
            return await L.successivo()
    if azione == "precedente":
        async with L._lock:
            return await L.precedente()
    if azione in ("radio_on", "radio_off"):
        L.radio = azione == "radio_on"
        if L.radio:
            asyncio.get_running_loop().create_task(L._estendi_coda())
        return "Riproduzione continua di brani simili " + ("attivata." if L.radio else "disattivata.")
    attuale = await L.backend.volume()
    if azione == "volume":
        if valore is None:
            return f"Volume attuale: {attuale}%"
        nuovo = int(valore)
    else:
        passo = abs(int(valore)) if valore else 10
        nuovo = attuale + passo if azione == "alza_volume" else attuale - passo
    nuovo = max(0, min(100, nuovo))
    await L.backend.imposta_volume(nuovo)
    return f"🔊 Volume: {attuale}% → {nuovo}%"


@tool(
    "musica_aggiungi_in_coda",
    title="Aggiungi brano in coda",
    description="Aggiunge un brano da riprodurre subito dopo quello attuale (es. 'dopo metti Volare'). Se non c'e' musica in corso, lo avvia.",
    group="music",
)
async def musica_aggiungi_in_coda(richiesta: str) -> str:
    return await LETTORE.accoda(richiesta)


@tool(
    "musica_stato",
    title="Cosa sta suonando",
    description="Mostra il brano in riproduzione, dispositivo, stato, volume e i prossimi brani in coda. Usalo per 'che canzone e' questa?'.",
    group="music",
    read_only=True,
)
async def musica_stato() -> str:
    L = LETTORE
    if L.backend is None or L.corrente is None:
        return "Nessuna musica in riproduzione."
    try:
        st, pos, vol = await asyncio.gather(L.backend.stato(), L.backend.posizione(), L.backend.volume())
    except Exception as e:
        st, pos, vol = f"sconosciuto ({e})", "", "?"
    nomi = {"playing": "in riproduzione", "paused": "in pausa", "idle": "fermo", "attesa": "in caricamento",
            "altro": "il dispositivo riproduce altro"}
    righe = [f"🎵 {L.corrente.descr()}", f"Stato: {nomi.get(st, st)}{(' · ' + pos) if pos else ''} · volume {vol}%",
             f"Dispositivo: {L.backend.nome} · radio brani simili: {'sì' if L.radio else 'no'}"]
    prossimi = L.coda[L.indice + 1: L.indice + 11]
    if prossimi:
        righe.append("Prossimi:")
        righe.extend(f"  {i}. {b.descr()}" for i, b in enumerate(prossimi, 1))
    return "\n".join(righe)


# =============================================================================
# GOOGLE CALENDAR
# =============================================================================
class GoogleCalendar:
    def __init__(self) -> None:
        self._svc = None
        self._lock = threading.Lock()  # le chiamate httplib2 non sono thread-safe

    def _crea_servizio(self):
        try:
            from googleapiclient.discovery import build
            from google.auth.transport.requests import Request
        except ImportError:
            raise UserError("Installa google-api-python-client e google-auth per usare il calendario.")
        if GOOGLE_SA_FILE and Path(GOOGLE_SA_FILE).is_file():
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(GOOGLE_SA_FILE, scopes=GOOGLE_SCOPES)
            if GOOGLE_DELEGATED_USER:
                creds = creds.with_subject(GOOGLE_DELEGATED_USER)
        elif Path(GOOGLE_TOKEN_FILE).is_file():
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, GOOGLE_SCOPES)
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
                Path(GOOGLE_TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")
        else:
            raise UserError("Google Calendar non configurato (GOOGLE_SERVICE_ACCOUNT_FILE o GOOGLE_OAUTH_TOKEN_FILE).")
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def esegui(self, fn):
        """Esegue fn(service).execute() in modo thread-safe, traducendo gli errori."""
        with self._lock:
            if self._svc is None:
                self._svc = self._crea_servizio()
            try:
                return fn(self._svc).execute()
            except UserError:
                raise
            except Exception as e:
                stato = getattr(getattr(e, "resp", None), "status", None)
                if stato == 404:
                    raise UserError("Calendario o evento non trovato (controlla l'ID e la condivisione col service account).")
                if stato in (401, 403):
                    raise UserError(f"Accesso negato a Google Calendar: {e}")
                raise


CALENDARIO = GoogleCalendar()


def _cal_id(calendario: str) -> str:
    c = (calendario or "").strip()
    return c or GOOGLE_CALENDAR_IDS[0]


def _evento_dt(campo: dict) -> tuple[datetime, bool]:
    if "dateTime" in campo:
        return datetime.fromisoformat(campo["dateTime"].replace("Z", "+00:00")).astimezone(TZ), False
    d = date.fromisoformat(campo["date"])
    return datetime(d.year, d.month, d.day, tzinfo=TZ), True


def _formatta_evento(ev: dict, cal: str) -> str:
    inizio, intero = _evento_dt(ev["start"])
    fine, _ = _evento_dt(ev["end"])
    if intero:
        giorni = (fine - inizio).days
        quando = f"{GIORNI_BREVI[inizio.weekday()]} {inizio:%d/%m/%Y} (tutto il giorno" + \
                 (f", {giorni} giorni)" if giorni > 1 else ")")
    else:
        quando = f"{GIORNI_BREVI[inizio.weekday()]} {inizio:%d/%m/%Y %H:%M}–" + \
                 (f"{fine:%H:%M}" if fine.date() == inizio.date() else f"{fine:%d/%m %H:%M}")
    riga = f"• {quando} · {ev.get('summary', '(senza titolo)')}"
    if ev.get("location"):
        riga += f" · 📍 {ev['location']}"
    if ev.get("description"):
        riga += f"\n    {tronca(re.sub(r'<[^>]+>', ' ', ev['description']).strip(), 200)}"
    riga += f"\n    [id: {ev['id']} · calendario: {cal}]"
    return riga


@tool(
    "calendario_eventi",
    title="Eventi del calendario",
    description=(
        "Elenca gli eventi del Google Calendar in un intervallo. Senza date mostra i prossimi 'giorni' (default 7). "
        "'da'/'a' accettano 'oggi', 'domani', '18/11/2026', '2026-11-18 15:00'. 'cerca' filtra per testo."
    ),
    group="calendar",
    read_only=True,
)
async def calendario_eventi(da: str = "", a: str = "", giorni: int = 7, cerca: str = "",
                            calendario: str = "") -> str:
    inizio = parse_data_ora(da)[0] if da.strip() else now()
    if a.strip():
        fine, ha_ora = parse_data_ora(a)
        if not ha_ora:
            fine += timedelta(days=1)
    else:
        if da.strip() and not parse_data_ora(da)[1]:
            inizio = inizio.replace(hour=0, minute=0)
        fine = inizio + timedelta(days=max(1, min(int(giorni or 7), 366)))
    cals = [calendario.strip()] if calendario.strip() else GOOGLE_CALENDAR_IDS
    tutti = []
    errori = []
    for cal in cals:
        try:
            params = dict(calendarId=cal, timeMin=inizio.isoformat(), timeMax=fine.isoformat(), singleEvents=True,
                          orderBy="startTime", maxResults=100)
            if cerca.strip():
                params["q"] = cerca.strip()
            res = await asyncio.to_thread(CALENDARIO.esegui, lambda s, p=params: s.events().list(**p))
            for ev in res.get("items", []):
                if ev.get("status") != "cancelled":
                    tutti.append((_evento_dt(ev["start"])[0], ev, cal))
        except UserError as e:
            errori.append(f"{cal}: {e}")
    tutti.sort(key=lambda x: x[0])
    intest = f"Eventi dal {fmt_dt(inizio)} al {fmt_dt(fine)}" + (f" con '{cerca}'" if cerca else "")
    if not tutti:
        return intest + ": nessun evento." + (("\nErrori: " + "; ".join(errori)) if errori else "")
    return intest + f" ({len(tutti)}):\n" + "\n".join(_formatta_evento(ev, cal) for _, ev, cal in tutti) + \
        (("\nErrori: " + "; ".join(errori)) if errori else "")


def _corpo_tempi(inizio: str, fine: str, durata_minuti: int, tutto_il_giorno: bool) -> tuple[dict, dict]:
    dt_i, ha_ora = parse_data_ora(inizio)
    if tutto_il_giorno or not ha_ora:
        d_f = parse_data_ora(fine)[0].date() + timedelta(days=1) if fine.strip() else dt_i.date() + timedelta(days=1)
        if d_f <= dt_i.date():
            raise UserError("La data di fine deve essere successiva all'inizio.")
        return {"date": dt_i.date().isoformat()}, {"date": d_f.isoformat()}
    if fine.strip():
        dt_f, ha_ora_f = parse_data_ora(fine, riferimento=dt_i)
        if not ha_ora_f:
            raise UserError("Indica anche l'ora di fine (o usa durata_minuti).")
    else:
        dt_f = dt_i + timedelta(minutes=max(1, int(durata_minuti or 60)))
    if dt_f <= dt_i:
        raise UserError("La fine deve essere successiva all'inizio.")
    return ({"dateTime": dt_i.isoformat(), "timeZone": TZ_NAME},
            {"dateTime": dt_f.isoformat(), "timeZone": TZ_NAME})


@tool(
    "calendario_crea_evento",
    title="Crea evento nel calendario",
    description=(
        "Crea un evento nel Google Calendar. 'inizio' es. '2026-11-20 15:00' o 'domani 9:30'; 'fine' opzionale "
        "(altrimenti usa durata_minuti, default 60). Se l'inizio non ha ora o tutto_il_giorno=true crea un evento "
        "di un'intera giornata. promemoria_minuti aggiunge una notifica (es. 30)."
    ),
    group="calendar",
)
async def calendario_crea_evento(titolo: str, inizio: str, fine: str = "", durata_minuti: int = 60,
                                 tutto_il_giorno: bool = False, descrizione: str = "", luogo: str = "",
                                 promemoria_minuti: Optional[int] = None, calendario: str = "") -> str:
    if not titolo.strip():
        raise UserError("Il titolo e' obbligatorio.")
    start, end = _corpo_tempi(inizio, fine, durata_minuti, tutto_il_giorno)
    corpo: dict[str, Any] = {"summary": titolo.strip(), "start": start, "end": end}
    if descrizione:
        corpo["description"] = descrizione
    if luogo:
        corpo["location"] = luogo
    if promemoria_minuti is not None:
        corpo["reminders"] = {"useDefault": False,
                              "overrides": [{"method": "popup", "minutes": max(0, int(promemoria_minuti))}]}
    cal = _cal_id(calendario)
    ev = await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.events().insert(calendarId=cal, body=corpo))
    return "✅ Evento creato:\n" + _formatta_evento(ev, cal) + (f"\n    link: {ev.get('htmlLink')}" if ev.get("htmlLink") else "")


@tool(
    "calendario_modifica_evento",
    title="Modifica evento del calendario",
    description=(
        "Modifica un evento esistente (ottieni l'id con calendario_eventi). Specifica solo i campi da cambiare. "
        "Se cambi solo 'inizio' la durata originale viene mantenuta."
    ),
    group="calendar",
)
async def calendario_modifica_evento(event_id: str, titolo: str = "", inizio: str = "", fine: str = "",
                                     descrizione: Optional[str] = None, luogo: Optional[str] = None,
                                     calendario: str = "") -> str:
    cal = _cal_id(calendario)
    patch: dict[str, Any] = {}
    if titolo.strip():
        patch["summary"] = titolo.strip()
    if descrizione is not None:
        patch["description"] = descrizione
    if luogo is not None:
        patch["location"] = luogo
    if inizio.strip() or fine.strip():
        ev = await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.events().get(calendarId=cal, eventId=event_id))
        vecchio_i, intero = _evento_dt(ev["start"])
        vecchio_f, _ = _evento_dt(ev["end"])
        durata = int((vecchio_f - vecchio_i).total_seconds() // 60)
        if intero:
            i_txt = inizio.strip() or vecchio_i.date().isoformat()
            f_txt = fine.strip() or (parse_data_ora(i_txt)[0].date() + timedelta(days=max(1, durata // 1440) - 1)).isoformat()
            patch["start"], patch["end"] = _corpo_tempi(i_txt, f_txt, 0, True)
        else:
            i_txt = inizio.strip() or vecchio_i.isoformat()
            patch["start"], patch["end"] = _corpo_tempi(i_txt, fine, durata, False)
    if not patch:
        raise UserError("Nessuna modifica indicata.")
    ev = await asyncio.to_thread(CALENDARIO.esegui,
                                 lambda s: s.events().patch(calendarId=cal, eventId=event_id, body=patch))
    return "✅ Evento aggiornato:\n" + _formatta_evento(ev, cal)


@tool(
    "calendario_elimina_evento",
    title="Elimina evento dal calendario",
    description="Elimina un evento dal Google Calendar. Chiedi conferma all'utente e poi richiama con conferma=true.",
    group="calendar",
    destructive=True,
)
async def calendario_elimina_evento(event_id: str, conferma: bool = False, calendario: str = "") -> str:
    cal = _cal_id(calendario)
    ev = await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.events().get(calendarId=cal, eventId=event_id))
    if not conferma:
        return ("⚠️ Stai per eliminare:\n" + _formatta_evento(ev, cal) +
                "\nChiedi conferma all'utente e poi richiama con conferma=true.")
    await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.events().delete(calendarId=cal, eventId=event_id))
    return f"🗑️ Evento «{ev.get('summary', '')}» eliminato."


@tool(
    "calendario_lista",
    title="Elenco dei calendari",
    description="Elenca i calendari Google accessibili con il loro ID (da usare nel parametro 'calendario').",
    group="calendar",
    read_only=True,
)
async def calendario_lista() -> str:
    res = await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.calendarList().list())
    righe = [f"• {c.get('summary')} — id: {c['id']}{' (principale)' if c.get('primary') else ''} [{c.get('accessRole')}]"
             for c in res.get("items", [])]
    configurati = ", ".join(GOOGLE_CALENDAR_IDS)
    return ("Calendari accessibili:\n" + "\n".join(righe) if righe else
            "Nessun calendario nella lista (con un service account i calendari condivisi possono non comparire qui, "
            "ma sono comunque utilizzabili tramite ID).") + f"\nCalendari configurati (GOOGLE_CALENDAR_IDS): {configurati}"


# =============================================================================
# APP HTTP: autenticazione, health check, ciclo di vita
# =============================================================================
@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"stato": "ok", "tool": len(REGISTERED_TOOLS), "ora": now().isoformat(timespec="seconds")})


class ApiKeyMiddleware:
    """Richiede 'Authorization: Bearer <MCP_API_KEY>' (o header X-API-Key) su tutte le rotte tranne /health."""

    def __init__(self, app, chiave: str) -> None:
        self.app = app
        self.chiave = chiave.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") != "/health":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            fornita = headers.get("x-api-key", "")
            auth = headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                fornita = auth[7:].strip()
            if not hmac.compare_digest(fornita.encode(), self.chiave):
                corpo = b'{"error":"unauthorized"}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": corpo})
                return
        await self.app(scope, receive, send)


async def avvio() -> None:
    TIMER.avvia()
    if FEATURES["music"]:
        LETTORE.avvia_monitor()
    logger.info(f"Tool attivi ({len(REGISTERED_TOOLS)}): {', '.join(REGISTERED_TOOLS)}")


async def arresto() -> None:
    logger.info("Arresto in corso...")
    TIMER.ferma()
    PROCESSI.termina_tutti()
    with contextlib.suppress(Exception):
        await LETTORE.chiudi()
    with contextlib.suppress(Exception):
        await BROWSER.chiudi()


def crea_app():
    app = mcp.streamable_http_app()
    originale = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async with originale(a):
            await avvio()
            try:
                yield
            finally:
                await arresto()

    app.router.lifespan_context = lifespan
    if MCP_API_KEY:
        return ApiKeyMiddleware(app, MCP_API_KEY)
    return app


def google_auth(client_secret: str) -> None:
    """Flusso OAuth una tantum per creare il token di Google Calendar."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, GOOGLE_SCOPES)
    creds = flow.run_local_server(port=8765, open_browser=True)
    Path(GOOGLE_TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(GOOGLE_TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")
    os.chmod(GOOGLE_TOKEN_FILE, 0o600)
    print(f"Token salvato in {GOOGLE_TOKEN_FILE}")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--google-auth":
        google_auth(sys.argv[2])
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "--check":
        print(f"Funzionalita': {json.dumps(FEATURES)}")
        print(f"Tool registrati ({len(REGISTERED_TOOLS)}): {', '.join(REGISTERED_TOOLS)}")
        print(f"Workspace: {WORKSPACE_DIR}  Dati: {DATA_DIR}")
        return
    import uvicorn
    if not MCP_API_KEY:
        logger.warning("MCP_API_KEY non impostata: l'endpoint e' accessibile a chiunque sulla rete "
                       "(sconsigliato con il terminale abilitato).")
    if not HASS_TOKEN:
        logger.warning("HASS_TOKEN non impostato: tool Home Assistant disabilitati.")
    logger.info(f"Avvio server MCP su {MCP_HOST}:{MCP_PORT} — endpoint OpenWebUI: http://<IP>:{MCP_PORT}/")
    uvicorn.run(crea_app(), host=MCP_HOST, port=MCP_PORT, log_level="warning")


if __name__ == "__main__":
    main()
