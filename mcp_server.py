#!/usr/bin/env python3
"""
Server MCP per OpenWebUI (LLM locale) - punto di ingresso
==========================================================

Espone gli strumenti via protocollo MCP "Streamable HTTP" sulla porta 8000, con l'endpoint sulla root "/"
(non "/mcp") per evitare il bug di redirect 307 -> 404 di OpenWebUI.

    URL da inserire in OpenWebUI:  http://<IP_CONTAINER>:8000/
    (con MCP_API_KEY, in OpenWebUI scegli l'autenticazione "Bearer" con la stessa chiave)

Struttura del codice
--------------------
  mcp_server.py        questo file: app HTTP, autenticazione, avvio/arresto, riga di comando
  nucleo/              servizi comuni: configurazione, server MCP e decoratore dei tool, utilità,
                       rete (anti-SSRF), browser, workspace, sandbox, conferme degli invii, link ai file
  tools/               un modulo per gruppo di strumenti, ognuno con le proprie costanti di configurazione

Configurazione: file .env (impostazioni generali) e /etc/mcp-server/mcp-server.env (variabili fisse).
Tutte le variabili sono descritte nel README.

Riga di comando:
  python mcp_server.py                              avvia il server
  python mcp_server.py --check                      mostra gruppi e strumenti attivi
  python mcp_server.py --google-auth client.json    crea il token OAuth di Google Calendar
"""

import asyncio
import contextlib
import hmac
import json
import os
import sys
from pathlib import Path

from nucleo import echo_bridge, manutenzione, sandbox
from nucleo.browser import BROWSER
from nucleo.configurazione import (
    DATA_DIR,
    MCP_API_KEY,
    MCP_HOST,
    MCP_PORT,
    WORKSPACE_DIR,
    controlla_permessi,
    logger,
    prepara_cartelle,
    suggerimento_permessi,
)
from nucleo.link_file import PREFISSO_ROTTA
from nucleo.server import FUNZIONALITA, REGISTERED_TOOLS, mcp
from nucleo.utilita import now

# Importare il pacchetto tools registra tutti gli strumenti (vedi tools/__init__.py)
from tools.calendario_google import GOOGLE_SCOPES, GOOGLE_TOKEN_FILE
from tools.home_assistant import HASS_TOKEN
from tools.musica import LETTORE
from tools.terminale import PROCESSI
from tools.timer import TIMER

VERSIONE = manutenzione.versione()


# =============================================================================
# APP HTTP: autenticazione, health check, ciclo di vita
# =============================================================================
@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"stato": "ok", "versione": VERSIONE, "tool": len(REGISTERED_TOOLS), "ora": now().isoformat(timespec="seconds")}
    )


class ApiKeyMiddleware:
    """Richiede 'Authorization: Bearer <MCP_API_KEY>' (o header X-API-Key), tranne su /health e sui link firmati."""

    def __init__(self, app, chiave: str) -> None:
        self.app = app
        self.chiave = chiave.encode()

    async def __call__(self, scope, receive, send):
        # /health e i link firmati ai file (/file/...) non richiedono la chiave: un tag <img> non può inviarla
        if (
            scope["type"] == "http"
            and scope.get("path") != "/health"
            and not scope.get("path", "").startswith(PREFISSO_ROTTA)
        ):
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            fornita = headers.get("x-api-key", "")
            auth = headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                fornita = auth[7:].strip()
            if not hmac.compare_digest(fornita.encode(), self.chiave):
                corpo = b'{"error":"unauthorized"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
                    }
                )
                await send({"type": "http.response.body", "body": corpo})
                return
        await self.app(scope, receive, send)


_attivita: list[asyncio.Task] = []


async def avvio() -> None:
    logger.info(f"Server MCP versione {VERSIONE}")
    prepara_cartelle()
    problemi = controlla_permessi()
    if problemi:
        logger.error(f"Permessi insufficienti su: {', '.join(problemi[:5])}. {suggerimento_permessi()}")
    TIMER.avvia()
    if FUNZIONALITA.get("music"):
        LETTORE.avvia_monitor()
    if FUNZIONALITA["terminal"]:
        disponibile, motivo = await asyncio.to_thread(sandbox.sandbox_disponibile)
        if disponibile:
            logger.info("Terminale in sandbox (bubblewrap): i comandi vedono solo la cartella di lavoro.")
        elif sandbox.TERMINAL_SANDBOX in ("obbligatoria", "required", "si", "sì"):
            logger.error(f"Sandbox del terminale non disponibile ({motivo}): terminale ed esegui_python disattivati.")
        else:
            logger.warning(
                f"Terminale SENZA sandbox: i comandi possono leggere tutto ciò che legge l'utente del "
                f"servizio. Motivo: {motivo}"
            )
    logger.info(f"Tool attivi ({len(REGISTERED_TOOLS)}): {', '.join(REGISTERED_TOOLS)}")
    _attivita.append(asyncio.get_running_loop().create_task(manutenzione.ciclo_pulizia(PROCESSI.rimuovi_terminati)))


async def arresto() -> None:
    logger.info("Arresto in corso...")
    for attivita in _attivita:
        attivita.cancel()
    TIMER.ferma()
    PROCESSI.termina_tutti()
    with contextlib.suppress(Exception):
        await LETTORE.chiudi()
    with contextlib.suppress(Exception):
        await BROWSER.chiudi()


def crea_app():
    app = mcp.streamable_http_app()
    originale = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async with originale(a):
            await avvio()
            try:
                yield
            finally:
                await arresto()

    app.router.lifespan_context = lifespan
    if MCP_API_KEY:
        return ApiKeyMiddleware(app, MCP_API_KEY)
    return app


def google_auth(client_secret: str) -> None:
    """Flusso OAuth una tantum per creare il token di Google Calendar."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secret, GOOGLE_SCOPES)
    creds = flow.run_local_server(port=8765, open_browser=True)
    Path(GOOGLE_TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(GOOGLE_TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")
    os.chmod(GOOGLE_TOKEN_FILE, 0o600)
    print(f"Token salvato in {GOOGLE_TOKEN_FILE}")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--google-auth":
        google_auth(sys.argv[2])
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "--check":
        print(f"Funzionalità: {json.dumps(FUNZIONALITA)}")
        print(f"Tool registrati ({len(REGISTERED_TOOLS)}): {', '.join(REGISTERED_TOOLS)}")
        print(f"Workspace: {WORKSPACE_DIR}  Dati: {DATA_DIR}")
        print(f"Versione: {VERSIONE}")
        print(manutenzione.spazio_occupato())
        print(f"Bridge Echo: {echo_bridge.ECHO_BRIDGE_URL or 'non configurato'}")
        if echo_bridge.attivo():
            try:
                echos = asyncio.run(echo_bridge.elenco_echo())
                print(
                    "  Echo: "
                    + (
                        ", ".join(
                            f"{e.get('name')} ({'collegato' if e.get('connected') else 'scollegato'})" for e in echos
                        )
                        or "nessuno"
                    )
                )
            except echo_bridge.BridgeError as e:
                print(f"  {e}")
        return
    import uvicorn

    if not MCP_API_KEY:
        logger.warning(
            "MCP_API_KEY non impostata: l'endpoint è accessibile a chiunque sulla rete "
            "(sconsigliato con il terminale abilitato)."
        )
    if not HASS_TOKEN:
        logger.warning("HASS_TOKEN non impostato: tool Home Assistant disabilitati.")
    if echo_bridge.attivo():
        logger.info(f"Bridge Echo: {echo_bridge.ECHO_BRIDGE_URL} (timer, sveglie, musica e avvisi vocali sull'Echo)")
    logger.info(f"Avvio server MCP su {MCP_HOST}:{MCP_PORT} — endpoint OpenWebUI: http://<IP>:{MCP_PORT}/")
    uvicorn.run(crea_app(), host=MCP_HOST, port=MCP_PORT, log_level="warning")


if __name__ == "__main__":
    main()
