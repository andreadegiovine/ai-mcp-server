"""Client del bridge Echo (ai-echo-dot-bridge): annunci, timer e sveglie nativi, musica sull'Echo.

Le richieste sono quelle descritte in REQUESTS.md del bridge. Il campo 'echo' vuoto indica l'Echo che ha
ricevuto l'ultima richiesta vocale: è il default giusto quando un tool parte da una domanda fatta all'Echo.
"""

import contextlib

import httpx

from nucleo.configurazione import env_bool, env_str, logger
from nucleo.utilita import normalizza

ECHO_BRIDGE_URL = env_str("ECHO_BRIDGE_URL").rstrip("/")
ECHO_BRIDGE_TOKEN = env_str("ECHO_BRIDGE_TOKEN")
ECHO_BRIDGE_ECHO = env_str("ECHO_BRIDGE_ECHO")  # Echo predefinito; vuoto = quello dell'ultima richiesta vocale
# se un annuncio non viene detto (es. "non disturbare"), lascia una notifica (anello giallo)
ECHO_NOTIFICA_SE_NON_DETTO = env_bool("ECHO_NOTIFY_IF_NOT_PLAYED", True)

PAROLE_ECHO = ("echo", "alexa", "echo dot", "amazon echo")


class BridgeError(Exception):
    """Errore restituito dal bridge (HTTP 400 con {"error"}) o bridge non raggiungibile."""


def attivo() -> bool:
    return bool(ECHO_BRIDGE_URL)


def _intestazioni() -> dict:
    return {"Authorization": f"Bearer {ECHO_BRIDGE_TOKEN}"} if ECHO_BRIDGE_TOKEN else {}


def echo_predefinito(echo: str = "") -> str:
    return (echo or "").strip() or ECHO_BRIDGE_ECHO


async def richiesta(percorso: str, dati: dict | None = None, *, timeout: float = 15, metodo: str = "POST") -> dict:
    """Chiama un endpoint REST del bridge e ritorna il JSON. Solleva BridgeError con il messaggio del bridge."""
    if not attivo():
        raise BridgeError("bridge Echo non configurato (ECHO_BRIDGE_URL)")
    url = f"{ECHO_BRIDGE_URL}{percorso}"
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as http:
            if metodo == "GET":
                r = await http.get(
                    url, params={k: v for k, v in (dati or {}).items() if v not in (None, "")}, headers=_intestazioni()
                )
            else:
                r = await http.post(url, json=dati or {}, headers=_intestazioni())
    except httpx.HTTPError as e:
        raise BridgeError(f"bridge Echo non raggiungibile: {e}") from e
    if r.status_code == 401:
        raise BridgeError("bridge Echo: token non valido (ECHO_BRIDGE_TOKEN)")
    try:
        corpo = r.json()
    except ValueError:
        corpo = {}
    if r.status_code >= 400:
        errore = corpo.get("error") if isinstance(corpo, dict) else None
        raise BridgeError(f"bridge Echo: {errore or f'HTTP {r.status_code}'}")
    return corpo


async def elenco_echo() -> list[dict]:
    dati = await richiesta("/api/echos", metodo="GET")
    return dati if isinstance(dati, list) else []


async def trova_echo(nome: str, *, esatto: bool = False) -> str | None:
    """Nome dell'Echo che corrisponde a 'nome' (nome, nodo o IP); None se nessuno."""
    n = normalizza(nome)
    if not n:
        return None
    echos = await elenco_echo()
    for e in echos:
        if n in (normalizza(e.get("name", "")), normalizza(e.get("node", "")), normalizza(e.get("address", ""))):
            return e.get("name")
    if not esatto:
        for e in echos:
            if n in normalizza(e.get("name", "")):
                return e.get("name")
    return None


async def annuncia(testo: str, echo: str = "", *, ascolta: bool = False) -> bool:
    """Fa dire una frase all'Echo. Se non viene detta (es. "non disturbare") lascia una notifica.

    Non solleva eccezioni: ritorna True se l'Echo l'ha detta."""
    if not attivo() or not testo.strip():
        return False
    echo = echo_predefinito(echo)
    try:
        r = await richiesta("/api/announce", {"text": testo, "echo": echo, "listen": ascolta}, timeout=150)
        detto = bool(r.get("played")) if "results" not in r else any(x.get("played") for x in r["results"])
    except BridgeError as e:
        logger.warning(f"[echo] annuncio non riuscito: {e}")
        detto = False
    if not detto and ECHO_NOTIFICA_SE_NON_DETTO:
        with contextlib.suppress(BridgeError):
            await richiesta("/api/notify", {"action": "add", "text": testo, "source": "mcp-server", "echo": echo})
    return detto


async def imposta_volume(livello: int, echo: str = "") -> None:
    """Volume dell'Echo (0-100): il bridge lo espone solo come strumento MCP 'set_volume'."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    if not attivo():
        raise BridgeError("bridge Echo non configurato (ECHO_BRIDGE_URL)")
    try:
        async with streamablehttp_client(f"{ECHO_BRIDGE_URL}/mcp", headers=_intestazioni(), timeout=10) as (
            lettura,
            scrittura,
            _,
        ):
            async with ClientSession(lettura, scrittura) as sessione:
                await sessione.initialize()
                res = await sessione.call_tool(
                    "set_volume", {"level": max(0, min(100, int(livello))), "echo": echo_predefinito(echo)}
                )
    except BridgeError:
        raise
    except Exception as e:
        raise BridgeError(f"volume dell'Echo non impostabile: {e}") from e
    if res.isError:
        testo = " ".join(getattr(c, "text", "") for c in res.content).strip()
        raise BridgeError(f"bridge Echo: {testo or 'set_volume non riuscito'}")


async def volume(echo: str) -> int | None:
    """Volume attuale dell'Echo (0-100) da GET /api/echos."""
    for e in await elenco_echo():
        if not echo or e.get("name") == echo:
            v = e.get("volume")
            if v is None:
                return None
            v = float(v)
            return int(round(v * 100)) if v <= 1 else int(round(v))
    return None
