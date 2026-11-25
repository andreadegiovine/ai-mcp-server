"""Invii verso l'esterno (email, WhatsApp, GitHub): conferma obbligatoria, allegati, registro."""

import asyncio
import mimetypes
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.fastmcp import Context

from nucleo.configurazione import DATA_DIR, ELICITATION_TIMEOUT, env_int, logger
from nucleo.manutenzione import scrivi_registro
from nucleo.rete import controlla_url, http_get
from nucleo.server import UserError, client_supporta_moduli
from nucleo.utilita import now
from nucleo.workspace import percorso_workspace

ALLEGATI_MAX_MB = env_int("ALLEGATI_MAX_MB", 20)


def registra_invio(testo: str) -> None:
    scrivi_registro(DATA_DIR / "invii.log", f"{now().isoformat(timespec='seconds')} {testo}")


_IN_ATTESA: dict[str, dict] = {}
ATTESA_SECONDI = 900


def crea_attesa(tipo: str, dati: dict) -> str:
    adesso = time.time()
    for k in [k for k, v in _IN_ATTESA.items() if v["scade"] < adesso]:
        _IN_ATTESA.pop(k, None)
    codice = secrets.token_hex(3).upper()
    _IN_ATTESA[codice] = {"tipo": tipo, "dati": dati, "scade": adesso + ATTESA_SECONDI}
    return codice


def prendi_attesa(codice: str, tipo: str) -> dict:
    voce = _IN_ATTESA.get(codice.strip().upper())
    if not voce or voce["tipo"] != tipo or voce["scade"] < time.time():
        raise UserError(
            "Codice di conferma non valido, già usato o scaduto: prepara di nuovo l'invio e chiedi conferma all'utente."
        )
    _IN_ATTESA.pop(codice.strip().upper(), None)
    return voce["dati"]


async def conferma_interattiva(ctx: Context | None, messaggio: str) -> bool | None:
    """Chiede conferma con un modulo (elicitation). None = modulo non disponibile."""
    if not client_supporta_moduli(ctx):
        return None
    from pydantic import Field, create_model

    modello = create_model("Conferma", conferma=(bool, Field(title="Confermi l'invio?")))
    try:
        esito = await asyncio.wait_for(ctx.elicit(messaggio, modello), ELICITATION_TIMEOUT)
    except TimeoutError:
        return False
    except Exception as e:
        logger.warning(f"Conferma con modulo non riuscita, uso la conferma in chat: {e}")
        return None
    return esito.action == "accept" and bool(getattr(esito.data, "conferma", False))


async def chiedi_conferma(
    ctx: Context | None, tipo: str, dati: dict, frase: str, anteprima: str, nome_tool: str
) -> str | None:
    """None = confermato (procedi con l'invio); altrimenti il testo da restituire al modello."""
    esito = await conferma_interattiva(ctx, f"{frase}\n\n{anteprima}")
    if esito is True:
        return None
    if esito is False:
        return "L'utente non ha confermato: invio annullato."
    codice = crea_attesa(tipo, dati)
    return (
        "Invio NON ancora effettuato: serve la conferma esplicita dell'utente.\n"
        f"Frase da dire, in italiano (usala così com'è):\n{frase}\n\n"
        f"Anteprima (per te, non leggerla tutta):\n{anteprima}\n\n"
        f"Solo se l'utente conferma (es. 'sì', 'ok', 'manda'), richiama {nome_tool} con "
        f"codice_conferma='{codice}' e nessun altro parametro. Se rifiuta o chiede modifiche, non usare il codice. "
        f"Non dire il codice all'utente. Il codice vale una sola volta e scade tra {ATTESA_SECONDI // 60} minuti."
    )


def controlla_allegati(allegati: list[str], base: Path | None = None) -> list[str]:
    """Verifica gli allegati: gli URL restano tali, i file locali diventano percorsi assoluti nel workspace."""
    puliti = []
    for a in allegati[:10]:
        a = str(a).strip()
        if not a:
            continue
        if a.startswith(("http://", "https://")):
            controlla_url(a)
        else:
            p = percorso_workspace(a, base)
            if not p.is_file():
                raise UserError(f"Allegato non trovato nel workspace: {a}")
            a = str(p)
        puliti.append(a)
    return puliti


def carica_allegato(sorgente: str, max_bytes: int) -> tuple[bytes, str, str]:
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


def lista_testi(valore: list[str] | str | None) -> list[str]:
    if not valore:
        return []
    if isinstance(valore, list):
        return [str(v) for v in valore]
    return [v for v in re.split(r"[\s,]+", str(valore)) if v]
