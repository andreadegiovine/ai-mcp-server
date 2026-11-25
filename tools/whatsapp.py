"""Strumento: invio di messaggi WhatsApp tramite il gateway OpenWA, con conferma obbligatoria."""

import asyncio
import base64
from typing import Literal
from urllib.parse import quote

import requests
from mcp.server.fastmcp import Context

from nucleo.configurazione import env_bool, env_str
from nucleo.invii import (
    ALLEGATI_MAX_MB,
    carica_allegato,
    chiedi_conferma,
    controlla_allegati,
    lista_testi,
    prendi_attesa,
    registra_invio,
)
from nucleo.server import FUNZIONALITA, UserError, tool
from nucleo.utilita import tronca
from nucleo.workspace import IdConversazione, cartella_lavoro
from tools.rubrica import RE_TELEFONO, risolvi_destinatario, telefono_leggibile

OPENWA_URL = env_str("OPENWA_URL").rstrip("/")
OPENWA_API_KEY = env_str("OPENWA_API_KEY")
OPENWA_SESSION_ID = env_str("OPENWA_SESSION_ID")
FUNZIONALITA["whatsapp"] = env_bool("ENABLE_WHATSAPP", True) and bool(OPENWA_URL and OPENWA_SESSION_ID)


# --- WhatsApp -----------------------------------------------------------------
def _openwa_invia(azione: Literal["send-text", "send-image"], corpo: dict) -> None:
    """POST verso il gateway OpenWA, con errori comprensibili."""
    url = f"{OPENWA_URL}/api/sessions/{quote(OPENWA_SESSION_ID, safe='')}/messages/{azione}"
    headers = {"X-API-Key": OPENWA_API_KEY} if OPENWA_API_KEY else {}
    try:
        r = requests.post(url, json=corpo, headers=headers, timeout=60)
    except requests.exceptions.RequestException as e:
        raise UserError(f"Gateway WhatsApp (OpenWA) non raggiungibile su {OPENWA_URL}: {type(e).__name__}")
    if r.status_code in (401, 403):
        raise UserError("Chiave del gateway WhatsApp non valida o senza permessi (OPENWA_API_KEY).")
    if r.status_code == 404:
        raise UserError(
            f"Il gateway OpenWA non trova la sessione '{OPENWA_SESSION_ID}': "
            f"OPENWA_SESSION_ID deve essere l'ID della sessione, non il nome."
        )
    if r.status_code >= 400:
        raise UserError(
            f"Il gateway OpenWA ha risposto HTTP {r.status_code}: {r.text[:200]} (la sessione è collegata al telefono?)"
        )


def _invia_whatsapp_sync(dati: dict) -> None:
    numero, testo, immagini = dati["numero"], dati["testo"], dati["immagini"]
    chat = numero.lstrip("+") + "@c.us"
    if testo:
        _openwa_invia("send-text", {"chatId": chat, "text": testo})
    residuo = ALLEGATI_MAX_MB * 1024 * 1024
    for sorgente in immagini:
        contenuto, nome, mime = carica_allegato(sorgente, residuo)
        residuo -= len(contenuto)
        if not mime.startswith("image/"):
            raise UserError(f"'{sorgente}' non è un'immagine ({mime}).")
        _openwa_invia(
            "send-image",
            {
                "chatId": chat,
                "base64": base64.b64encode(contenuto).decode(),
                "mimetype": mime,
                "filename": nome,
                "caption": "",
            },
        )


@tool(
    "invia_whatsapp",
    title="Invia messaggio WhatsApp",
    description=(
        "Invia un messaggio WhatsApp con testo e/o immagini (es. 'cerca immagini di X e mandamele su WhatsApp': "
        "passa gli URL trovati con cerca_immagini in 'immagini'). destinatario: 'io' per l'utente stesso, il nome "
        "di un contatto della rubrica oppure un numero. Se il numero non è in rubrica lo strumento ti dice cosa "
        "chiedere all'utente e di salvarlo con rubrica_salva. L'invio richiede SEMPRE la conferma dell'utente: "
        "la prima chiamata prepara il messaggio e ti restituisce la domanda di conferma; solo dopo il sì "
        "dell'utente richiami lo strumento con il codice_conferma ricevuto. Le immagini possono essere anche file "
        "del workspace (con id_conversazione, relativi alla cartella della conversazione)."
    ),
    group="whatsapp",
    destructive=True,
)
async def invia_whatsapp(
    destinatario: str = "io",
    testo: str = "",
    immagini: list[str] | str | None = None,
    codice_conferma: str = "",
    id_conversazione: IdConversazione = None,
    ctx: Context | None = None,
) -> str:
    if codice_conferma.strip():
        dati = prendi_attesa(codice_conferma, "whatsapp")
    else:
        lista = await asyncio.to_thread(controlla_allegati, lista_testi(immagini), cartella_lavoro(id_conversazione))
        if not testo.strip() and not lista:
            raise UserError("Indica il testo del messaggio e/o le immagini da inviare.")
        numero, chiave, messaggio = await asyncio.to_thread(risolvi_destinatario, destinatario, "telefono")
        if not numero:
            return messaggio
        dati = {"numero": numero, "chiave": chiave, "testo": testo.strip()[:4000], "immagini": lista}
        chi = "" if chiave == "io" or RE_TELEFONO.match(chiave) else f"a {chiave} "
        cosa = "il messaggio" + (
            f" con {len(lista)} immagini" if len(lista) > 1 else " con un'immagine" if lista else ""
        )
        frase = f"Mando {cosa} {chi}al numero {telefono_leggibile(numero)}?"
        anteprima = f"A: {numero}\nTesto: {tronca(dati['testo'], 800) or '(nessuno)'}" + (
            f"\nImmagini: {len(lista)}" if lista else ""
        )
        attesa = await chiedi_conferma(ctx, "whatsapp", dati, frase, anteprima, "invia_whatsapp")
        if attesa is not None:
            return attesa
    await asyncio.to_thread(_invia_whatsapp_sync, dati)
    registra_invio(f"WHATSAPP a={dati['numero']} immagini={len(dati['immagini'])}")
    return f"Messaggio WhatsApp inviato al numero {telefono_leggibile(dati['numero'])}" + (
        f" con {len(dati['immagini'])} immagini." if dati["immagini"] else "."
    )
