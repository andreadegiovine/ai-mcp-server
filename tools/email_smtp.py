"""Strumento: invio di email via SMTP, con conferma obbligatoria."""
import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional, Union

from mcp.server.fastmcp import Context

from nucleo.configurazione import _env, _env_bool, _env_int
from nucleo.server import FUNZIONALITA, UserError, tool
from nucleo.utilita import tronca
from nucleo.workspace import IdConversazione, cartella_lavoro
from nucleo.invii import (
    ALLEGATI_MAX_MB,
    registra_invio,
    prendi_attesa,
    chiedi_conferma,
    controlla_allegati,
    carica_allegato,
    lista_testi,
)
from tools.rubrica import risolvi_destinatario


SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = _env_int("SMTP_PORT", 587)
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
SMTP_FROM = _env("SMTP_FROM") or SMTP_USER
SMTP_NOME = _env("SMTP_NOME", "Assistente")
SMTP_STARTTLS = _env_bool("SMTP_STARTTLS", True)
FUNZIONALITA["email"] = _env_bool("ENABLE_EMAIL", True) and bool(SMTP_HOST and SMTP_FROM)


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
        contenuto, nome, mime = carica_allegato(sorgente, residuo)
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
        "richiami lo strumento con il codice_conferma ricevuto. allegati: URL di immagini/file o percorsi del workspace "
        "(con id_conversazione, relativi alla cartella della conversazione)."
    ),
    group="email",
    destructive=True,
)
async def invia_email(destinatario: str = "io", oggetto: str = "", testo: str = "",
                      allegati: Union[list[str], str, None] = None, codice_conferma: str = "",
                      id_conversazione: IdConversazione = None, ctx: Optional[Context] = None) -> str:
    if codice_conferma.strip():
        dati = prendi_attesa(codice_conferma, "email")
    else:
        if not testo.strip():
            raise UserError("Il testo dell'email e' vuoto.")
        indirizzo, chiave, messaggio = await asyncio.to_thread(risolvi_destinatario, destinatario, "email")
        if not indirizzo:
            return messaggio
        dati = {"a": indirizzo, "chiave": chiave, "oggetto": (oggetto.strip() or "Messaggio dal tuo assistente")[:200],
                "testo": testo.strip(), "allegati": await asyncio.to_thread(controlla_allegati, lista_testi(allegati),
                                                 cartella_lavoro(id_conversazione))}
        frase = (f"Mando la mail a {indirizzo}?" if chiave in ("io", indirizzo)
                 else f"Mando la mail a {chiave} all'indirizzo {indirizzo}?")
        anteprima = (f"A: {indirizzo}\nOggetto: {dati['oggetto']}\nTesto:\n{tronca(dati['testo'], 1500)}"
                     + (f"\nAllegati: {len(dati['allegati'])}" if dati["allegati"] else ""))
        attesa = await chiedi_conferma(ctx, "email", dati, frase, anteprima, "invia_email")
        if attesa is not None:
            return attesa
    await asyncio.to_thread(_invia_email_sync, dati)
    registra_invio(f"EMAIL a={dati['a']} oggetto={dati['oggetto']!r} allegati={len(dati['allegati'])}")
    return (f"Email inviata a {dati['a']} con oggetto '{dati['oggetto']}'"
            + (f" e {len(dati['allegati'])} allegati." if dati["allegati"] else "."))
