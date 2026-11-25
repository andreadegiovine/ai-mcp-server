"""Manutenzione: rotazione dei registri, pulizia periodica dei file vecchi, spazio occupato, versione del codice."""

import asyncio
import contextlib
import os
import time
from pathlib import Path

from nucleo.configurazione import BASE_DIR, DATA_DIR, WORKSPACE_DIR, env_int, logger

REGISTRI_MAX_MB = env_int("REGISTRI_MAX_MB", 5)
REGISTRI_COPIE = env_int("REGISTRI_COPIE", 3)
PULIZIA_GIORNI_PROCESSI = env_int("PULIZIA_GIORNI_PROCESSI", 7)
PULIZIA_GIORNI_SCREENSHOT = env_int("PULIZIA_GIORNI_SCREENSHOT", 30)
PULIZIA_GIORNI_CACHE = env_int("PULIZIA_GIORNI_CACHE", 30)
PULIZIA_INTERVALLO_ORE = 24


def scrivi_registro(percorso: Path, riga: str) -> None:
    """Aggiunge una riga a un registro, ruotandolo quando supera REGISTRI_MAX_MB (file.1, file.2, ...)."""
    with contextlib.suppress(OSError):
        percorso.parent.mkdir(parents=True, exist_ok=True)
        if percorso.exists() and percorso.stat().st_size > REGISTRI_MAX_MB * 1024 * 1024:
            for n in range(REGISTRI_COPIE - 1, 0, -1):
                vecchio = percorso.with_name(f"{percorso.name}.{n}")
                if vecchio.exists():
                    os.replace(vecchio, percorso.with_name(f"{percorso.name}.{n + 1}"))
            os.replace(percorso, percorso.with_name(f"{percorso.name}.1"))
        with open(percorso, "a", encoding="utf-8") as fh:
            fh.write(riga.rstrip("\n") + "\n")


def _rimuovi_vecchi(cartella: Path, giorni: int, schema: str = "*") -> tuple[int, int]:
    """Cancella i file più vecchi di 'giorni' (data di modifica); ritorna (file rimossi, byte liberati)."""
    if giorni <= 0 or not cartella.is_dir():
        return 0, 0
    limite = time.time() - giorni * 86400
    rimossi = liberati = 0
    for f in cartella.rglob(schema):
        with contextlib.suppress(OSError):
            if f.is_file() and not f.is_symlink() and f.stat().st_mtime < limite:
                dimensione = f.stat().st_size
                f.unlink()
                rimossi += 1
                liberati += dimensione
    return rimossi, liberati


def pulisci() -> str:
    """Una passata di pulizia; ritorna il resoconto."""
    totale_file = totale_byte = 0
    for cartella, giorni, schema in (
        (DATA_DIR / "jobs", PULIZIA_GIORNI_PROCESSI, "*.log"),
        (DATA_DIR / "home_agente" / ".cache", PULIZIA_GIORNI_CACHE, "*"),
    ):
        n, b = _rimuovi_vecchi(cartella, giorni, schema)
        totale_file, totale_byte = totale_file + n, totale_byte + b
    if WORKSPACE_DIR.is_dir() and PULIZIA_GIORNI_SCREENSHOT > 0:
        for cartella in [WORKSPACE_DIR / "screenshot", *WORKSPACE_DIR.glob("*/screenshot")]:
            n, b = _rimuovi_vecchi(cartella, PULIZIA_GIORNI_SCREENSHOT, "*.png")
            totale_file, totale_byte = totale_file + n, totale_byte + b
    return f"Pulizia: rimossi {totale_file} file vecchi, liberati {totale_byte / 1e6:.1f} MB"


def _dimensione(percorso: Path) -> int:
    totale = 0
    for radice, _, files in os.walk(percorso):
        for f in files:
            with contextlib.suppress(OSError):
                totale += os.lstat(os.path.join(radice, f)).st_size
    return totale


def spazio_occupato() -> str:
    """Spazio occupato nella cartella dei dati, per sottocartella."""
    if not DATA_DIR.is_dir():
        return f"Cartella dati {DATA_DIR} non ancora creata"
    voci = []
    altro = 0
    for elemento in DATA_DIR.iterdir():
        if elemento.is_dir() and not elemento.is_symlink():
            voci.append((elemento.name, _dimensione(elemento)))
        else:
            with contextlib.suppress(OSError):
                altro += elemento.lstat().st_size
    voci.append(("altri file", altro))
    voci.sort(key=lambda v: -v[1])
    totale = sum(b for _, b in voci)
    dettaglio = ", ".join(f"{nome} {b / 1e6:.1f} MB" for nome, b in voci if b)
    return f"Spazio occupato in {DATA_DIR}: {totale / 1e6:.1f} MB ({dettaglio or 'vuota'})"


async def ciclo_pulizia(extra=None) -> None:
    """Pulizia all'avvio (dopo un minuto) e poi ogni PULIZIA_INTERVALLO_ORE ore. 'extra': funzione aggiuntiva."""
    await asyncio.sleep(60)
    while True:
        try:
            resoconto = await asyncio.to_thread(pulisci)
            if extra is not None:
                extra()
            logger.info(f"{resoconto}. {await asyncio.to_thread(spazio_occupato)}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Pulizia periodica non riuscita: {e}")
        await asyncio.sleep(PULIZIA_INTERVALLO_ORE * 3600)


def versione() -> str:
    """Commit del codice in esecuzione, letto da .git senza eseguire git (es. 'main@1a2b3c4')."""
    git = BASE_DIR / ".git"
    try:
        testa = (git / "HEAD").read_text().strip()
        if not testa.startswith("ref: "):
            return testa[:7]
        ref = testa[5:]
        ramo = ref.rsplit("/", 1)[-1]
        file_ref = git / ref
        if file_ref.exists():
            return f"{ramo}@{file_ref.read_text().strip()[:7]}"
        for riga in (git / "packed-refs").read_text().splitlines():
            if riga.endswith(" " + ref):
                return f"{ramo}@{riga.split()[0][:7]}"
    except OSError:
        pass
    return "sconosciuta"
