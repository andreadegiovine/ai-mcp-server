"""Configurazione generale: variabili d'ambiente, cartelle dati, fuso orario e logging."""

import logging
import os
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


# cartella del progetto (questo file è in nucleo/)
BASE_DIR = Path(__file__).resolve().parent.parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "si", "sì", "on")


def env_lista(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in env_str(name, default).split(",") if x.strip()]


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


MCP_HOST = env_str("MCP_HOST", "0.0.0.0")
MCP_PORT = env_int("MCP_PORT", 8000)
MCP_API_KEY = env_str("MCP_API_KEY")
# Moduli interattivi MCP ("elicitation"): richiedono un server con sessione e un client compatibile.
ENABLE_ELICITATION = env_bool("ENABLE_ELICITATION", True)
ELICITATION_TIMEOUT = env_int("ELICITATION_TIMEOUT", 300)

DATA_DIR = Path(env_str("DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
WORKSPACE_DIR = Path(env_str("WORKSPACE_DIR", str(DATA_DIR / "workspace"))).expanduser().resolve()

TZ_NAME = env_str("TIMEZONE", "Europe/Rome")
try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:  # pragma: no cover - tzdata mancante
    TZ = timezone(timedelta(hours=1))

DISABLED_TOOLS = set(env_lista("DISABLED_TOOLS"))


# ----------------------------------------------------------------------------- logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
for _n in (
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "httpx",
    "mcp.server.streamable_http_manager",
    "mcp.server.streamable_http",
    "mcp.server.lowlevel.server",
):
    logging.getLogger(_n).setLevel(logging.WARNING)
logger = logging.getLogger("mcp-server")


class _FiltroChiusuraSessione(logging.Filter):
    """Nasconde l'errore innocuo che la libreria MCP registra quando un client chiude una sessione."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not (
            record.getMessage().startswith("Error in standalone SSE writer")
            and type(exc).__name__ == "ClosedResourceError"
        )


logging.getLogger("mcp.server.streamable_http").addFilter(_FiltroChiusuraSessione())


def suggerimento_permessi() -> str:
    try:
        import pwd

        utente = pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        utente = str(os.getuid())
    return f"Esegui come root: chown -R {utente}:{utente} {DATA_DIR}"


def controlla_permessi() -> list[str]:
    """Cartelle o file di DATA_DIR non scrivibili dall'utente del servizio (es. creati da root)."""
    problemi = []
    for radice, cartelle, files in os.walk(DATA_DIR):
        if Path(radice) in (WORKSPACE_DIR, DATA_DIR / "home_agente"):
            cartelle[:] = []  # workspace e home dell'agente: i file li gestisce l'agente
        for nome in [radice] + [os.path.join(radice, f) for f in files]:
            if not os.access(nome, os.W_OK):
                problemi.append(nome)
        if len(problemi) >= 5:
            break
    if WORKSPACE_DIR.exists() and not os.access(WORKSPACE_DIR, os.W_OK) and str(WORKSPACE_DIR) not in problemi:
        problemi.append(str(WORKSPACE_DIR))
    return problemi


def prepara_cartelle() -> None:
    """Crea le cartelle dati all'avvio del servizio (non all'import: un '--check' eseguito come root
    creerebbe cartelle di proprietà di root, non scrivibili dall'utente del servizio)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
