"""Workspace: cartelle di lavoro (anche per conversazione), percorsi confinati, ambiente senza segreti."""
import os
import re
import sys
from pathlib import Path
from typing import Annotated, Optional

from pydantic import Field

from nucleo.configurazione import _env_bool, WORKSPACE_DIR, logger
from nucleo.server import FUNZIONALITA, UserError


# Strumenti che usano il workspace (terminale, file, download): ENABLE_TERMINAL=false li disattiva tutti
FUNZIONALITA["terminal"] = _env_bool("ENABLE_TERMINAL", True)

# Parametro comune dei tool che usano il workspace (vedi cartella_lavoro)
IdConversazione = Annotated[Optional[str], Field(
    description="ID della conversazione corrente (facoltativo). Se indicato, i file vengono letti e scritti "
                "nella sottocartella del workspace con questo nome, creata se non esiste. Usa sempre lo "
                "stesso ID per tutta la conversazione.")]


_CHIAVI_SENSIBILI = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CREDENTIAL|PRIVATE_KEY|"
                               r"^HASS_|^GOOGLE_|^MCP_|^AWS_|^OPENAI|^ANTHROPIC)", re.I)


def env_sicuro(base: Optional[Path] = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _CHIAVI_SENSIBILI.search(k)}
    env.update({"PYTHONUNBUFFERED": "1", "GIT_TERMINAL_PROMPT": "0", "TERM": "dumb",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PAGER": "cat", "GIT_PAGER": "cat",
                "WORKSPACE": str(base or WORKSPACE_DIR)})
    venv_bin = str(Path(sys.executable).parent)
    if venv_bin not in env.get("PATH", ""):
        env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    return env


def percorso_workspace(p: str, base: Optional[Path] = None) -> Path:
    """
    Risolve un percorso garantendo che resti dentro 'base' (default: tutto il workspace), anche seguendo
    i symlink. I percorsi relativi partono da 'base'.
    """
    radice = Path(os.path.realpath(base or WORKSPACE_DIR))
    if radice != WORKSPACE_DIR and WORKSPACE_DIR not in radice.parents:
        raise UserError("Cartella di lavoro fuori dal workspace non consentita.")
    p = (p or ".").strip()
    candidato = Path(p).expanduser()
    if not candidato.is_absolute():
        candidato = radice / candidato
    reale = Path(os.path.realpath(candidato))
    if reale != radice and radice not in reale.parents:
        dove = "" if radice == WORKSPACE_DIR else " della conversazione"
        raise UserError(f"Percorso fuori dal workspace{dove} non consentito: {p}")
    return reale


_RE_ID_CONV = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")

def cartella_lavoro(id_conversazione: Optional[str] = None) -> Path:
    """
    Cartella in cui lavorano i tool: la sottocartella della conversazione (creata se non esiste) oppure,
    senza ID, la radice del workspace.
    """
    cid = (id_conversazione or "").strip()
    if not cid:
        return WORKSPACE_DIR
    if not _RE_ID_CONV.match(cid) or ".." in cid:
        raise UserError(f"ID conversazione non valido: '{cid[:60]}' (ammessi lettere, numeri, '-', '_' e '.').")
    cartella = WORKSPACE_DIR / cid
    if not cartella.exists():
        cartella.mkdir(parents=True, exist_ok=True)
        logger.info(f"Creata la cartella della conversazione '{cid}'")
    return percorso_workspace(".", cartella)


def relativo(p: Path, base: Path) -> str:
    try:
        r = str(p.relative_to(base))
    except ValueError:
        r = str(p)
    return "." if r in ("", ".") else r
