"""Istanza del server MCP, decoratore dei tool e gestione uniforme degli errori."""

import functools
import time
import traceback

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from nucleo.configurazione import (
    DISABLED_TOOLS,
    ENABLE_ELICITATION,
    MCP_HOST,
    MCP_PORT,
    logger,
    suggerimento_permessi,
)

ISTRUZIONI = """Sei un assistente con accesso a strumenti reali.
- Per informazioni attuali o incerte usa cerca_online e poi leggi_pagina_web sulle fonti migliori; cita le fonti.
- Per mostrare un'immagine all'utente scrivi nella risposta il markdown ![descrizione](url_immagine).
- Per mostrare un file del workspace (immagine, grafico, PDF) usa il link restituito dagli strumenti o da
  link_file: non scrivere mai un percorso locale come link, il client non può aprirlo.
- Per Home Assistant: trova prima l'entity_id con hass_cerca_entita, poi leggi o controlla.
  Per serrature, allarmi, garage o cancelli chiedi SEMPRE conferma esplicita all'utente prima di usare conferma=true.
- Il workspace (terminale e file) è una cartella dedicata: lavora lì dentro.
- Salva nella memoria le preferenze stabili dell'utente quando le comunica.
- Rispondi sempre in italiano, senza inserire parole di altre lingue."""

mcp = FastMCP(
    name="mcp-server",
    instructions=ISTRUZIONI,
    host=MCP_HOST,
    port=MCP_PORT,
    streamable_http_path="/",
    # Con elicitation (default) servono sessione e risposte in streaming, perché il server deve poter
    # inviare una richiesta al client durante l'esecuzione di un tool. Con ENABLE_ELICITATION=false
    # ogni richiesta è autonoma e la risposta è un JSON semplice (modalità più conservativa).
    stateless_http=not ENABLE_ELICITATION,
    json_response=not ENABLE_ELICITATION,
)

REGISTERED_TOOLS: list[str] = []

# Gruppi di strumenti attivi (ogni modulo di tools/ registra il proprio, es. FUNZIONALITA['hass'])
FUNZIONALITA: dict[str, bool] = {}


class UserError(Exception):
    """Errore 'previsto' da mostrare al modello senza traceback."""


def tool(
    name: str,
    description: str,
    *,
    title: str | None = None,
    group: str | None = None,
    read_only: bool | None = None,
    destructive: bool | None = None,
):
    """Registra un tool MCP con gestione uniforme degli errori e feature-flag."""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            try:
                return await fn(*args, **kwargs)
            except UserError as e:
                logger.info(f"[{name}] {e}")
                return f"{e}"
            except PermissionError as e:
                logger.error(f"[{name}] permessi insufficienti: {e}. {suggerimento_permessi()}")
                return (
                    f"Il server non ha i permessi per scrivere su {getattr(e, 'filename', '') or 'un file'}. "
                    f"L'amministratore deve correggere i permessi della cartella dati."
                )
            except Exception as e:
                logger.error(f"[{name}] errore: {e}\n{traceback.format_exc()}")
                return f"Errore durante l'esecuzione di '{name}': {type(e).__name__}: {e}"
            finally:
                logger.info(f"[{name}] completato in {time.monotonic() - t0:.2f}s")

        if name in DISABLED_TOOLS:
            logger.info(f"Tool '{name}' disabilitato da DISABLED_TOOLS")
            return wrapper
        if group and not FUNZIONALITA.get(group, True):
            return wrapper
        mcp.tool(
            name=name,
            title=title,
            description=description,
            annotations=ToolAnnotations(title=title, readOnlyHint=read_only, destructiveHint=destructive),
            structured_output=False,
        )(wrapper)
        REGISTERED_TOOLS.append(name)
        return wrapper

    return deco


def client_supporta_moduli(ctx: Context | None) -> bool:
    """True se i moduli interattivi sono abilitati e il client dichiara il supporto all'elicitation."""
    if not ENABLE_ELICITATION or ctx is None:
        return False
    try:
        params = ctx.session.client_params
        return bool(params and params.capabilities and params.capabilities.elicitation is not None)
    except Exception:
        return False
