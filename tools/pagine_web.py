"""Strumenti: lettura di pagine web e PDF online, screenshot, download nel workspace."""
import asyncio
import contextlib
import io
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from mcp.types import TextContent

from typing import Optional

from mcp.server.fastmcp import Context
from nucleo.link_file import nota_link
from nucleo.configurazione import _env_int
from nucleo.server import UserError, tool
from nucleo.utilita import now
from nucleo.rete import HTTP, controlla_url, http_get, immagine_a_contenuto
from nucleo.browser import PLAYWRIGHT_DISPONIBILE, BROWSER, _accetta_cookie
from nucleo.workspace import IdConversazione, percorso_workspace, cartella_lavoro, relativo

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None


WEB_CHUNK_CHARS = _env_int("WEB_CHUNK_CHARS", 8000)
DOWNLOAD_MAX_MB = _env_int("DOWNLOAD_MAX_MB", 200)


_cache_pagine: dict[str, tuple[float, str]] = {}


def _testo_da_pdf(dati: bytes) -> str:
    if PdfReader is None:
        raise UserError("pypdf non installato: impossibile leggere PDF")
    reader = PdfReader(io.BytesIO(dati))
    parti = []
    for i, p in enumerate(reader.pages[:200], 1):
        with contextlib.suppress(Exception):
            t = (p.extract_text() or "").strip()
            if t:
                parti.append(f"--- Pagina {i} ---\n{t}")
    return "\n\n".join(parti)


def _dividi_in_parti(testo: str, dim: int) -> list[str]:
    parti, corrente = [], ""
    for para in re.split(r"(\n\s*\n)", testo):
        if len(corrente) + len(para) > dim and corrente:
            parti.append(corrente)
            corrente = ""
        while len(para) > dim:
            parti.append(para[:dim])
            para = para[dim:]
        corrente += para
    if corrente.strip():
        parti.append(corrente)
    return parti or [""]


async def _scarica_pagina(url: str) -> str:
    """Ritorna testo completo (con intestazione) di una pagina HTML o PDF. Cache 10 minuti."""
    cache = _cache_pagine.get(url)
    if cache and time.time() - cache[0] < 600:
        return cache[1]

    is_pdf = urlparse(url).path.lower().endswith(".pdf")
    if not is_pdf:
        with contextlib.suppress(Exception):
            h = await asyncio.to_thread(lambda: HTTP.head(url, timeout=8, allow_redirects=False))
            is_pdf = "application/pdf" in h.headers.get("content-type", "")

    if is_pdf:
        dati, _, finale = await asyncio.to_thread(http_get, url, max_bytes=40 * 1024 * 1024, timeout=40)
        testo = await asyncio.to_thread(_testo_da_pdf, dati)
        completo = f"Documento PDF: {finale}\n\n{testo or '(nessun testo estraibile, forse PDF scansionato)'}"
    else:
        if not PLAYWRIGHT_DISPONIBILE:
            dati, _, finale = await asyncio.to_thread(http_get, url)
            html, titolo, immagini = dati.decode("utf-8", "ignore"), "", []
        else:
            async with BROWSER.pagina() as page:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=5000)
                await _accetta_cookie(page)
                if resp is not None and resp.status >= 400:
                    raise UserError(f"La pagina ha risposto con HTTP {resp.status}")
                html = await page.content()
                titolo = await page.title()
                finale = page.url
                immagini = await page.evaluate(
                    """() => {
                        const out = [];
                        const og = document.querySelector('meta[property="og:image"]');
                        if (og && og.content) out.push(og.content);
                        document.querySelectorAll('article img, main img, img').forEach(i => {
                            const src = i.currentSrc || i.src;
                            if (src && src.startsWith('http') && (i.naturalWidth >= 300 || i.width >= 300)) out.push(src);
                        });
                        return [...new Set(out)].slice(0, 6);
                    }"""
                )
                fallback = await page.evaluate("() => document.body ? document.body.innerText : ''")
        testo = None
        if trafilatura is not None:
            testo = await asyncio.to_thread(
                trafilatura.extract, html, url=finale, include_tables=True, include_links=False,
                include_comments=False, output_format="markdown", favor_recall=True,
            )
        if not testo or len(testo) < 200:
            testo = (locals().get("fallback") or testo or "").strip()
        if not testo:
            raise UserError("Pagina caricata ma nessun contenuto testuale estraibile.")
        intest = f"Titolo: {titolo}\nURL: {finale}\n" if titolo else f"URL: {finale}\n"
        if immagini:
            intest += "Immagini principali (per mostrarle usa ![descrizione](url)):\n" + \
                      "\n".join(f"  - {u}" for u in immagini) + "\n"
        completo = intest + "\n" + re.sub(r"\n{3,}", "\n\n", testo)

    _cache_pagine[url] = (time.time(), completo)
    if len(_cache_pagine) > 50:
        for k in sorted(_cache_pagine, key=lambda k: _cache_pagine[k][0])[:10]:
            _cache_pagine.pop(k, None)
    return completo


@tool(
    "leggi_pagina_web",
    title="Leggi pagina web",
    description=(
        "Apre un URL (pagine web o PDF) con un browser reale ed estrae il testo pulito in markdown, "
        "con titolo e immagini principali. I contenuti lunghi sono divisi in parti: usa il parametro "
        "'parte' (2, 3, ...) per continuare. Usalo dopo una ricerca per leggere le fonti, o quando "
        "l'utente invia un link chiedendo analisi o riassunti."
    ),
    read_only=True,
)
async def leggi_pagina_web(url: str, parte: int = 1) -> str:
    url = await asyncio.to_thread(controlla_url, url)
    completo = await _scarica_pagina(url)
    parti = _dividi_in_parti(completo, WEB_CHUNK_CHARS)
    parte = max(1, int(parte or 1))
    if parte > len(parti):
        raise UserError(f"La pagina ha solo {len(parti)} parti.")
    testo = parti[parte - 1]
    if len(parti) > 1:
        testo = f"[Parte {parte} di {len(parti)}]\n\n{testo}"
        if parte < len(parti):
            testo += f"\n\n[Continua: chiama leggi_pagina_web con parte={parte + 1}]"
    return testo


@tool(
    "screenshot_pagina",
    title="Screenshot della pagina",
    description=(
        "Apre un URL e restituisce uno screenshot (immagine) della pagina, utile per vedere layout, grafici, "
        "mappe o contenuti visivi che il testo non descrive. Lo screenshot viene anche salvato nel workspace."
        " Facoltativo: id_conversazione per lavorare nella cartella della conversazione."
    ),
    read_only=True,
)
async def screenshot_pagina(url: str, pagina_intera: bool = False, id_conversazione: IdConversazione = None,
                            ctx: Optional[Context] = None) -> list:
    base = cartella_lavoro(id_conversazione)
    if not PLAYWRIGHT_DISPONIBILE:
        raise UserError("Playwright non disponibile.")
    url = await asyncio.to_thread(controlla_url, url)
    async with BROWSER.pagina(blocca_media=False) as page:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=6000)
        await _accetta_cookie(page)
        titolo = await page.title()
        if pagina_intera:
            altezza = min(await page.evaluate("() => document.body.scrollHeight"), 8000)
            png = await page.screenshot(clip={"x": 0, "y": 0, "width": 1366, "height": altezza}, full_page=True)
        else:
            png = await page.screenshot()
    cartella = base / "screenshot"
    cartella.mkdir(exist_ok=True)
    nome = cartella / f"screenshot_{now():%Y%m%d_%H%M%S}.png"
    nome.write_bytes(png)
    img = await asyncio.to_thread(immagine_a_contenuto, png, 1366)
    return [TextContent(type="text", text=f"Screenshot di '{titolo}' ({url}) salvato in "
                                          f"{relativo(nome, base)}.\n{nota_link(nome, f'Screenshot di {titolo}', ctx)}"), img]


@tool(
    "scarica_file",
    title="Scarica file",
    description=(
        "Scarica un file da un URL http/https e lo salva nel workspace (es. dataset, archivi, PDF). "
        "Restituisce percorso e dimensione. Per i repository git usa invece terminale_esegui con 'git clone'."
        " Facoltativo: id_conversazione per lavorare nella cartella della conversazione."
    ),
    group="terminal",
)
async def scarica_file(url: str, percorso_destinazione: str = "", id_conversazione: IdConversazione = None,
                       ctx: Optional[Context] = None) -> str:
    url = await asyncio.to_thread(controlla_url, url)
    base = cartella_lavoro(id_conversazione)
    nome = percorso_destinazione.strip() or (Path(urlparse(url).path).name or "download.bin")
    dest = percorso_workspace(nome, base)
    if dest.is_dir():
        dest = dest / (Path(urlparse(url).path).name or "download.bin")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        _, headers, finale = await asyncio.to_thread(
            http_get, url, max_bytes=DOWNLOAD_MAX_MB * 1024 * 1024, timeout=60, verso_file=tmp)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return (f"Scaricato {finale}\n→ {relativo(dest, base)} "
            f"({dest.stat().st_size / 1024:.1f} KB, {headers.get('Content-Type', 'tipo sconosciuto')})\n"
            f"{nota_link(dest, ctx=ctx)}")
