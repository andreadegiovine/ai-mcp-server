"""Strumenti: ricerca di immagini sul web (con anteprime) e visione di un'immagine."""

import asyncio
import contextlib
import json
from urllib.parse import quote_plus

from mcp.server.fastmcp import Context
from mcp.types import ImageContent, TextContent

from nucleo.browser import BROWSER, PLAYWRIGHT_DISPONIBILE, accetta_cookie
from nucleo.configurazione import logger
from nucleo.link_file import markdown_file
from nucleo.rete import http_get, immagine_a_contenuto, url_consentito
from nucleo.server import UserError, tool
from nucleo.workspace import IdConversazione, cartella_lavoro, percorso_workspace, relativo

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


async def _immagini_bing(query: str, n: int) -> list[dict]:
    async with BROWSER.pagina() as page:
        await page.goto(
            f"https://www.bing.com/images/search?q={quote_plus(query)}&setlang=it&cc=IT",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        await accetta_cookie(page)
        with contextlib.suppress(Exception):
            await page.wait_for_selector("a.iusc", timeout=8000)
        meta = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a.iusc')).slice(0, 40).map(a => a.getAttribute('m'))"
        )
    out = []
    for m in meta:
        with contextlib.suppress(Exception):
            d = json.loads(m)
            out.append(
                {
                    "immagine": d.get("murl"),
                    "anteprima": d.get("turl"),
                    "titolo": d.get("t", ""),
                    "pagina": d.get("purl", ""),
                }
            )
        if len(out) >= n:
            break
    return out


def _immagini_ddg(query: str, n: int) -> list[dict]:
    if DDGS is None:
        raise RuntimeError("ddgs non installato")
    res = DDGS().images(query, region="it-it", safesearch="moderate", max_results=n)
    return [
        {
            "immagine": r.get("image"),
            "anteprima": r.get("thumbnail"),
            "titolo": r.get("title", ""),
            "pagina": r.get("url", ""),
            "dimensioni": f"{r.get('width')}x{r.get('height')}",
        }
        for r in res or []
    ]


def _scarica_anteprima(info: dict) -> ImageContent | None:
    for chiave in ("anteprima", "immagine"):
        u = info.get(chiave)
        if not u:
            continue
        try:
            dati, headers, _ = http_get(u, max_bytes=6 * 1024 * 1024, timeout=10, headers={"Accept": "image/*"})
            return immagine_a_contenuto(dati, 512)
        except Exception as e:
            logger.info(f"Anteprima non scaricabile ({u}): {e}")
    return None


@tool(
    "cerca_immagini",
    title="Cerca immagini",
    description=(
        "Cerca immagini sul web e restituisce titolo, URL dell'immagine e pagina di origine; se "
        "mostra_anteprime=true allega anche le anteprime così puoi VEDERLE e scegliere le migliori. "
        "Per mostrare un'immagine all'utente nella chat scrivi nella risposta: ![descrizione](URL_immagine)."
    ),
    read_only=True,
)
async def cerca_immagini(query: str, numero: int = 4, mostra_anteprime: bool = True) -> list:
    n = max(1, min(int(numero or 4), 10))
    risultati: list[dict] = []
    errori = []
    try:
        risultati = await asyncio.wait_for(asyncio.to_thread(_immagini_ddg, query, n + 4), timeout=25)
    except Exception as e:
        errori.append(f"DuckDuckGo: {e}")
    if not risultati and PLAYWRIGHT_DISPONIBILE:
        try:
            risultati = await asyncio.wait_for(_immagini_bing(query, n + 4), timeout=35)
        except Exception as e:
            errori.append(f"Bing: {e}")
    risultati = [r for r in risultati if r.get("immagine", "").startswith("http") and url_consentito(r["immagine"])[0]]
    if not risultati:
        raise UserError(f"Nessuna immagine trovata per '{query}'. " + "; ".join(errori))

    anteprime: list[ImageContent | None] = [None] * len(risultati)
    if mostra_anteprime:
        anteprime = list(
            await asyncio.gather(*[asyncio.to_thread(_scarica_anteprima, r) for r in risultati[: n + 2]])
        ) + [None] * max(0, len(risultati) - n - 2)

    contenuti: list = []
    righe = []
    scelti = 0
    for r, img in zip(risultati, anteprime, strict=False):
        if scelti >= n:
            break
        if mostra_anteprime and img is None:
            continue  # immagine non raggiungibile: la scarto
        scelti += 1
        titolo = (r.get("titolo") or query).replace("[", "(").replace("]", ")")[:100]
        righe.append(
            f"{scelti}. {titolo}\n   Immagine: {r['immagine']}\n   Fonte: {r.get('pagina', '')}\n"
            f"   Markdown: ![{titolo}]({r['immagine']})"
        )
        if img is not None:
            contenuti.append(TextContent(type="text", text=f"Anteprima immagine {scelti}:"))
            contenuti.append(img)
    if not righe:
        raise UserError("Immagini trovate ma nessuna scaricabile.")
    intro = (
        f"Immagini per '{query}' ({len(righe)}). Per mostrarle all'utente includi nella risposta "
        f"il markdown indicato.\n\n" + "\n\n".join(righe)
    )
    return [TextContent(type="text", text=intro), *contenuti]


@tool(
    "vedi_immagine",
    title="Guarda immagine",
    description=(
        "Scarica un'immagine da un URL (o da un file nel workspace) e te la mostra, così puoi descriverla "
        "o analizzarla. Restituisce anche il markdown per mostrarla all'utente."
        " Facoltativo: id_conversazione per lavorare nella cartella della conversazione."
    ),
    read_only=True,
)
async def vedi_immagine(sorgente: str, id_conversazione: IdConversazione = None, ctx: Context | None = None) -> list:
    sorgente = sorgente.strip()
    if sorgente.startswith(("http://", "https://")):
        dati, headers, finale = await asyncio.to_thread(http_get, sorgente, max_bytes=15 * 1024 * 1024)
        md = f"![immagine]({finale})"
    else:
        base = cartella_lavoro(id_conversazione)
        p = percorso_workspace(sorgente, base)
        if not p.is_file():
            raise UserError(f"File non trovato: {sorgente}")
        dati = await asyncio.to_thread(p.read_bytes)
        md = markdown_file(p, ctx=ctx) or (
            f"(file {relativo(p, base)}: link non disponibile; non usare il percorso locale come link)"
        )
    try:
        img = await asyncio.to_thread(immagine_a_contenuto, dati, 1024)
    except UserError:
        raise
    except Exception as e:
        raise UserError(f"Il contenuto non è un'immagine valida: {e}")
    return [TextContent(type="text", text=f"Immagine caricata. Per mostrarla all'utente: {md}"), img]
