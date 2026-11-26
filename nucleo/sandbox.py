"""Esecuzione dei comandi dell'agente nella sandbox bubblewrap."""

import asyncio
import contextlib
import functools
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from nucleo.configurazione import WORKSPACE_DIR, env_str
from nucleo.server import UserError
from nucleo.workspace import HOME_AGENTE, HOME_SANDBOX, env_sicuro, variabili_home

# Sandbox (bubblewrap) per terminale ed esegui_python: i comandi vedono solo la cartella di lavoro e i programmi
# di sistema. "auto" = usala se disponibile, "obbligatoria" = senza sandbox il terminale non funziona,
# "disattivata" = comandi eseguiti direttamente (vedono tutto ciò che vede l'utente del servizio).
TERMINAL_SANDBOX = env_str("TERMINAL_SANDBOX", "auto").lower()


@functools.lru_cache(maxsize=1)
def sandbox_disponibile() -> tuple[bool, str]:
    """(disponibile, motivo): verifica una volta sola che bubblewrap funzioni in questo sistema."""
    if TERMINAL_SANDBOX in ("disattivata", "off", "false", "no", "0"):
        return False, "disattivata con TERMINAL_SANDBOX"
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return False, "bubblewrap non installato (apt install -y bubblewrap)"
    try:
        r = subprocess.run(
            [bwrap, "--unshare-all", "--share-net", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as e:
        return False, f"bubblewrap non avviabile: {e}"
    if r.returncode != 0:
        return False, (
            f"bubblewrap non utilizzabile ({r.stderr.strip()[:200]}). In un container LXC di Proxmox "
            f"abilita Opzioni > Funzionalità > Nesting e riavvia il container"
        )
    return True, ""


def proteggi_processo() -> bool:
    """Rende il processo del server non ispezionabile (PR_SET_DUMPABLE=0).

    Così i comandi dell'agente, che girano con lo stesso utente, non possono leggere /proc/<pid>/environ
    (dove ci sono i token) né la memoria del server, anche quando la sandbox non è disponibile."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        pr_set_dumpable, pr_get_dumpable = 4, 3
        if libc.prctl(pr_set_dumpable, 0, 0, 0, 0) != 0:
            return False
        return libc.prctl(pr_get_dumpable, 0, 0, 0, 0) == 0
    except Exception:
        return False


def argomenti_sandbox(base: Path, cwd: Path) -> list[str]:
    """
    Comando bwrap: il processo vede solo la cartella di lavoro (lettura/scrittura) e, in sola lettura,
    i programmi di sistema, /etc e l'interprete Python del server. /tmp è temporanea; la home dell'agente
    (/home/agente) è persistente, per gli strumenti installati senza root. La rete resta disponibile.
    """
    a = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--share-net",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        "/usr",
        "/usr",
    ]
    for d in ("/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32"):
        if os.path.islink(d):
            a += ["--symlink", os.readlink(d), d]
        elif os.path.isdir(d):
            a += ["--ro-bind", d, d]
    a += ["--ro-bind", "/etc", "/etc"]
    if os.path.isdir("/etc/mcp-server"):
        a += ["--tmpfs", "/etc/mcp-server"]  # configurazione personale del server: nascosta
    resolv = os.path.realpath("/etc/resolv.conf")
    if not resolv.startswith("/etc/") and os.path.exists(resolv):
        a += ["--ro-bind", os.path.dirname(resolv), os.path.dirname(resolv)]  # DNS (es. systemd-resolved)
    for d in sorted({sys.prefix, sys.base_prefix, str(Path(sys.executable).resolve().parent.parent)}):
        if d and not d.startswith("/usr") and os.path.isdir(d):
            a += ["--ro-bind", d, d]  # interprete Python e librerie del server (venv)
    HOME_AGENTE.mkdir(parents=True, exist_ok=True)
    a += ["--bind", str(HOME_AGENTE), HOME_SANDBOX]
    for nome, valore in variabili_home(HOME_SANDBOX).items():
        a += ["--setenv", nome, valore]
    a += ["--bind", str(base), str(base), "--chdir", str(cwd)]
    return a


def comando_da_eseguire(comando: str, cwd: Path, base: Path | None) -> list[str]:
    """Argomenti del processo: dentro la sandbox se disponibile, secondo TERMINAL_SANDBOX."""
    disponibile, motivo = sandbox_disponibile()
    if disponibile:
        return argomenti_sandbox(base or WORKSPACE_DIR, cwd) + ["bash", "-c", comando]
    if TERMINAL_SANDBOX in ("obbligatoria", "required", "si", "sì"):
        raise UserError(f"Il terminale richiede la sandbox (TERMINAL_SANDBOX=obbligatoria), non disponibile: {motivo}")
    return ["bash", "-c", comando]


async def esegui_comando(comando: str, cwd: Path, timeout: int, base: Path | None = None) -> tuple[int, str, float]:
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *comando_da_eseguire(comando, cwd, base),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env_sicuro(base),
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        codice = proc.returncode
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, _ = await proc.communicate()
        out = (out or b"") + f"\n[TIMEOUT: processo terminato dopo {timeout}s]".encode()
        codice = -9
    return codice, (out or b"").decode("utf-8", "replace"), time.monotonic() - t0


def quote_shell(s: str) -> str:
    import shlex

    return shlex.quote(s)
