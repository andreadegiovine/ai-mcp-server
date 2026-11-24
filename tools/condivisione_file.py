"""Strumento: link per mostrare nella chat (o scaricare) un file del workspace."""
from typing import Optional

from mcp.server.fastmcp import Context

from nucleo.link_file import LINK_FILE_GIORNI, crea_link, markdown_file
from nucleo.server import UserError, tool
from nucleo.workspace import IdConversazione, cartella_lavoro, percorso_workspace, relativo


@tool(
    "link_file",
    title="Condividi un file",
    description=(
        "Crea un link per mostrare nella chat o scaricare un file del workspace (immagini, grafici, PDF, documenti "
        "creati o scaricati). Restituisce il markdown da scrivere nella risposta: per le immagini ![...](...), per "
        "gli altri file [...](...). Non scrivere mai nella risposta un percorso locale: il client non puo' aprirlo."
        " Facoltativo: id_conversazione per lavorare nella cartella della conversazione."
    ),
    group="terminal",
    read_only=True,
)
async def link_file(percorso: str, descrizione: str = "", id_conversazione: IdConversazione = None,
                    ctx: Optional[Context] = None) -> str:
    base = cartella_lavoro(id_conversazione)
    p = percorso_workspace(percorso, base)
    if not p.is_file():
        raise UserError(f"File non trovato: {percorso}")
    url = crea_link(p, ctx=ctx)
    if not url:
        raise UserError("Impossibile determinare l'indirizzo del server per il link: imposta MCP_URL_PUBBLICO.")
    md = markdown_file(p, descrizione, ctx)
    return (f"Link a {relativo(p, base)} (valido {LINK_FILE_GIORNI} giorni): {url}\n"
            f"Per mostrarlo all'utente scrivi nella risposta: {md}")
