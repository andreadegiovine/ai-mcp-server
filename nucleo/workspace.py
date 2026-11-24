"""Workspace: cartelle di lavoro (anche per conversazione), percorsi confinati, ambiente senza segreti."""
import os
import re
import sys
from pathlib import Path
from typing import Annotated, Optional

from pydantic import Field

from nucleo.configurazione import DATA_DIR, _env_bool, WORKSPACE_DIR, logger
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


# Home persistente dell'agente: strumenti installati senza root (Node.js, Python con uv, Go, npm -g, pip in
# venv...) restano tra un comando e l'altro e tra le conversazioni. Nella sandbox e' visibile come /home/agente.
HOME_AGENTE = DATA_DIR / "home_agente"
HOME_SANDBOX = "/home/agente"


def path_base() -> str:
    """PATH di sistema con il Python del server."""
    path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    venv_bin = str(Path(sys.executable).parent)
    return path if venv_bin in path.split(os.pathsep) else venv_bin + os.pathsep + path


def variabili_home(home: str) -> dict[str, str]:
    """HOME, PATH e cartelle degli strumenti installati dall'agente nella sua home."""
    cartelle_bin = [f"{home}/.local/bin", f"{home}/go/bin", f"{home}/.cargo/bin"]
    return {"HOME": home, "PATH": os.pathsep.join(cartelle_bin + [path_base()]),
            "NPM_CONFIG_PREFIX": f"{home}/.local", "GOPATH": f"{home}/go", "XDG_CACHE_HOME": f"{home}/.cache"}


def env_sicuro(base: Optional[Path] = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _CHIAVI_SENSIBILI.search(k)}
    env.update({"PYTHONUNBUFFERED": "1", "GIT_TERMINAL_PROMPT": "0", "TERM": "dumb",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PAGER": "cat", "GIT_PAGER": "cat",
                "WORKSPACE": str(base or WORKSPACE_DIR)})
    HOME_AGENTE.mkdir(parents=True, exist_ok=True)
    env.update(variabili_home(str(HOME_AGENTE)))
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
