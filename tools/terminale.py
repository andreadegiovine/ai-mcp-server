"""Strumenti: terminale bash, processi in background ed esecuzione di Python (in sandbox)."""

import asyncio
import contextlib
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from nucleo import echo_bridge
from nucleo.configurazione import DATA_DIR, WORKSPACE_DIR, env_int, logger
from nucleo.sandbox import comando_da_eseguire, esegui_comando, quote_shell
from nucleo.server import UserError, tool
from nucleo.utilita import fmt_durata, tronca
from nucleo.workspace import IdConversazione, cartella_lavoro, env_sicuro, percorso_workspace, relativo

TERMINAL_MAX_OUTPUT = env_int("TERMINAL_MAX_OUTPUT", 12000)
TERMINAL_MAX_TIMEOUT = env_int("TERMINAL_MAX_TIMEOUT", 900)


def _taglia_output(testo: str) -> str:
    return tronca(testo, TERMINAL_MAX_OUTPUT, coda=TERMINAL_MAX_OUTPUT * 2 // 3)


class GestoreProcessi:
    """Processi in background avviati dal terminale."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.cartella = DATA_DIR / "jobs"
        self._avvisi: set[asyncio.Task] = set()

    async def avvia(self, comando: str, cwd: Path, base: Path | None = None) -> str:
        attivi = [j for j in self.jobs.values() if j["proc"].returncode is None]
        if len(attivi) >= 10:
            raise UserError("Troppi processi in background (max 10): terminane qualcuno.")
        jid = secrets.token_hex(3)
        argomenti = comando_da_eseguire(comando, cwd, base)
        self.cartella.mkdir(parents=True, exist_ok=True)
        log = self.cartella / f"{jid}.log"
        fh = open(log, "wb")
        proc = await asyncio.create_subprocess_exec(
            *argomenti,
            cwd=str(cwd),
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env_sicuro(base),
            start_new_session=True,
        )
        fh.close()
        self.jobs[jid] = {
            "proc": proc,
            "comando": comando,
            "log": log,
            "avvio": time.time(),
            "cwd": str(cwd),
            "base": str(base or WORKSPACE_DIR),
        }
        return jid

    def avvisa_al_termine(self, jid: str, echo: str = "") -> None:
        """Alla fine del processo lo annuncia sull'Echo (bridge), in background."""

        async def attendi() -> None:
            j = self.jobs.get(jid)
            if j is None:
                return
            codice = await j["proc"].wait()
            durata = fmt_durata(time.time() - j["avvio"])
            esito = "è terminato correttamente" if codice == 0 else f"è terminato con un errore, codice {codice}"
            await echo_bridge.annuncia(f"Il processo {_nome_vocale(j['comando'])} {esito}, dopo {durata}.", echo)

        task = asyncio.get_running_loop().create_task(attendi())
        self._avvisi.add(task)
        task.add_done_callback(self._avvisi.discard)

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
            return f"Il processo {jid} era già terminato (codice {j['proc'].returncode})."
        with contextlib.suppress(ProcessLookupError):
            os.killpg(j["proc"].pid, signal.SIGTERM)
        return f"Segnale di terminazione inviato al processo {jid}."

    def rimuovi_terminati(self, ore: float = 24) -> int:
        """Dimentica i processi terminati da più di 'ore' (i log li cancella la pulizia periodica)."""
        limite = time.time() - ore * 3600
        vecchi = [jid for jid, j in self.jobs.items() if j["proc"].returncode is not None and j["avvio"] < limite]
        for jid in vecchi:
            self.jobs.pop(jid, None)
        return len(vecchi)

    def termina_tutti(self) -> None:
        for task in list(self._avvisi):
            task.cancel()
        for j in self.jobs.values():
            if j["proc"].returncode is None:
                with contextlib.suppress(Exception):
                    os.killpg(j["proc"].pid, signal.SIGKILL)


PROCESSI = GestoreProcessi()


def _nome_vocale(comando: str) -> str:
    """Il comando in forma breve da leggere a voce (es. 'npm run build')."""
    primo = comando.strip().split("&&")[-1].split("|")[0].strip()
    parole = [w for w in primo.split() if not w.startswith("-")][:3]
    return " ".join(parole) or "in background"


@tool(
    "terminale_esegui",
    title="Terminale",
    description=(
        "Esegue un comando bash nel workspace dedicato (es. git clone, ls, pip install, python script.py, "
        "grep, curl, compilazioni, test). Ogni chiamata parte da 'cartella' (relativa al workspace): "
        "per comandi in sequenza usa '&&'. Restituisce codice di uscita e output (stdout+stderr). "
        "Per server o comandi molto lunghi usa in_background=true e poi terminale_processi; con "
        "avvisa_al_termine=true (e il bridge Echo) la fine del processo viene annunciata a voce sull'Echo. "
        "Non usare comandi interattivi (usa flag come -y). I comandi girano in una sandbox: vedono solo la "
        "cartella di lavoro e i programmi di sistema, non il resto del server. Non hai permessi di amministratore: "
        "apt e sudo non funzionano. Per Node.js, Python o Go usa ambiente_installa; altri strumenti installali "
        "senza root nella tua home persistente (~/.local/bin è nel PATH, npm install -g e pip in un venv "
        "funzionano e restano installati)."
        " Facoltativo: id_conversazione per lavorare nella cartella della conversazione."
    ),
    group="terminal",
    destructive=True,
)
async def terminale_esegui(
    comando: str,
    cartella: str = ".",
    timeout_secondi: int = 120,
    in_background: bool = False,
    avvisa_al_termine: bool = False,
    id_conversazione: IdConversazione = None,
) -> str:
    if not comando.strip():
        raise UserError("Comando vuoto.")
    base = cartella_lavoro(id_conversazione)
    cwd = percorso_workspace(cartella, base)
    if not cwd.is_dir():
        raise UserError(f"Cartella inesistente: {cartella}")
    logger.info(f"[terminale] ({cwd}) $ {comando}")
    if in_background:
        jid = await PROCESSI.avvia(comando, cwd, base)
        avviso = ""
        if avvisa_al_termine:
            if echo_bridge.attivo():
                PROCESSI.avvisa_al_termine(jid)
                avviso = "\nAlla fine lo annuncerò sull'Echo."
            else:
                avviso = "\n(Avviso vocale non disponibile: bridge Echo non configurato.)"
        await asyncio.sleep(1.5)
        return (
            f"Avviato in background con id '{jid}'.\nOutput iniziale:\n{PROCESSI.output(jid, 2000) or '(nessuno)'}"
            f"\nUsa terminale_processi(azione='output', id='{jid}') per seguirlo.{avviso}"
        )
    timeout = max(1, min(int(timeout_secondi or 120), TERMINAL_MAX_TIMEOUT))
    codice, out, durata = await esegui_comando(comando, cwd, timeout, base)
    return (
        f"$ {comando}\n[cartella: {relativo(cwd, base)} · codice uscita: {codice} · {durata:.1f}s]\n\n"
        f"{_taglia_output(out) or '(nessun output)'}"
    )


@tool(
    "terminale_processi",
    title="Processi in background",
    description=(
        "Gestisce i processi avviati con terminale_esegui(in_background=true). azione: 'lista', "
        "'output' (richiede id) o 'termina' (richiede id). Con id_conversazione la lista mostra solo "
        "i processi di quella conversazione."
    ),
    group="terminal",
)
async def terminale_processi(
    azione: Literal["lista", "output", "termina"] = "lista", id: str = "", id_conversazione: IdConversazione = None
) -> str:
    if azione == "lista":
        filtro = cartella_lavoro(id_conversazione)
        jobs = {jid: j for jid, j in PROCESSI.jobs.items() if filtro == WORKSPACE_DIR or j.get("base") == str(filtro)}
        if not jobs:
            return "Nessun processo in background."
        righe = []
        for jid, j in jobs.items():
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
        " Facoltativo: id_conversazione per lavorare nella cartella della conversazione."
    ),
    group="terminal",
    destructive=True,
)
async def esegui_python(codice: str, timeout_secondi: int = 60, id_conversazione: IdConversazione = None) -> str:
    base = cartella_lavoro(id_conversazione)
    tmpdir = base / ".tmp"
    tmpdir.mkdir(exist_ok=True)
    script = tmpdir / f"snippet_{secrets.token_hex(4)}.py"
    await asyncio.to_thread(script.write_text, codice, encoding="utf-8")
    try:
        timeout = max(1, min(int(timeout_secondi or 60), TERMINAL_MAX_TIMEOUT))
        codice_uscita, out, durata = await esegui_comando(
            f"{quote_shell(sys.executable)} {quote_shell(str(script))}", base, timeout, base
        )
    finally:
        script.unlink(missing_ok=True)
    return f"[codice uscita: {codice_uscita} · {durata:.1f}s]\n{_taglia_output(out) or '(nessun output)'}"
