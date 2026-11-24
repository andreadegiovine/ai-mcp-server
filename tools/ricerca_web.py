"""Strumenti: ricerca sul web (Google, DuckDuckGo, Bing) e notizie (Google News)."""
import asyncio
import base64
import contextlib
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, urlparse, parse_qs

from nucleo.configurazione import logger
from nucleo.server import UserError, tool
from nucleo.utilita import fmt_dt
from nucleo.rete import http_get
from nucleo.browser import PLAYWRIGHT_DISPONIBILE, BROWSER, _accetta_cookie

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


_JS_GOOGLE = """
() => {
  const out = []; const seen = new Set();
  document.querySelectorAll('#rso a h3, #search a h3').forEach(h3 => {
    const a = h3.closest('a');
    if (!a || !a.href || seen.has(a.href)) return;
    if (a.closest('#tads, #tadsb, #bottomads, [data-text-ad]')) return;
    const box = a.closest('div[data-rpos], div.MjjYud, div.g, div[data-hveid]') || a.parentElement;
    if (box && box.querySelector('[data-initq]')) return;
    let snip = '';
    const s = box && box.querySelector('div[data-sncf], .VwiC3b, [data-content-feature="1"], .IsZvec');
    if (s) snip = s.innerText;
    seen.add(a.href);
    out.push({url: a.href, titolo: h3.innerText, snippet: snip});
  });
  return out;
}
"""

_JS_BING = """
() => Array.from(document.querySelectorAll('#b_results > li.b_algo')).map(li => {
  const a = li.querySelector('h2 a');
  const p = li.querySelector('.b_caption p, p.b_lineclamp2, p.b_lineclamp3, p.b_lineclamp4, .b_algoSlug');
  return a ? {url: a.href, titolo: a.innerText, snippet: p ? p.innerText : ''} : null;
}).filter(Boolean)
"""


def _pulisci_url_risultato(url: str) -> str:
    """Rimuove i redirect di Google (/url?q=) e Bing (/ck/a?u=a1<base64>)."""
    try:
        p = urlparse(url)
        if "google." in p.netloc and p.path == "/url":
            return parse_qs(p.query).get("q", parse_qs(p.query).get("url", [url]))[0]
        if "bing.com" in p.netloc and p.path.startswith("/ck/"):
            u = parse_qs(p.query).get("u", [""])[0]
            if u.startswith("a1"):
                b = u[2:] + "=" * (-len(u[2:]) % 4)
                return base64.urlsafe_b64decode(b).decode("utf-8", "ignore")
    except Exception:
        pass
    return url


def _filtra_risultati(risultati: list[dict], n: int) -> list[dict]:
    visti, puliti = set(), []
    for r in risultati:
        url = _pulisci_url_risultato(r.get("url", ""))
        if not url.startswith("http"):
            continue
        if any(x in url for x in ("google.com/aclk", "googleadservices", "google.it/aclk",
                                   "bing.com/aclk", "/search?", "accounts.google")):
            continue
        chiave = url.split("#")[0].rstrip("/")
        if chiave in visti:
            continue
        visti.add(chiave)
        puliti.append({"url": url, "titolo": (r.get("titolo") or "").strip(),
                       "snippet": re.sub(r"\s+", " ", r.get("snippet") or "").strip()})
        if len(puliti) >= n:
            break
    return puliti


async def _cerca_google(query: str) -> list[dict]:
    async with BROWSER.pagina() as page:
        url = f"https://www.google.it/search?q={quote_plus(query)}&hl=it&gl=it"
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if "consent." in page.url:
            await _accetta_cookie(page)
            with contextlib.suppress(Exception):
                await page.wait_for_url("**/search**", timeout=8000)
        else:
            await _accetta_cookie(page)
        if "/sorry/" in page.url or await page.locator("#captcha-form, form#captcha").count():
            raise RuntimeError("Google ha mostrato un captcha")
        with contextlib.suppress(Exception):
            await page.wait_for_selector("#search, #rso", timeout=10000)
        return await page.evaluate(_JS_GOOGLE)


async def _cerca_bing(query: str) -> list[dict]:
    async with BROWSER.pagina() as page:
        await page.goto(f"https://www.bing.com/search?q={quote_plus(query)}&setlang=it&cc=IT",
                        wait_until="domcontentloaded", timeout=20000)
        await _accetta_cookie(page)
        with contextlib.suppress(Exception):
            await page.wait_for_selector("#b_results", timeout=10000)
        return await page.evaluate(_JS_BING)


def _cerca_ddg(query: str, n: int) -> list[dict]:
    if DDGS is None:
        raise RuntimeError("ddgs non installato")
    res = DDGS().text(query, region="it-it", safesearch="moderate", max_results=n)
    return [{"url": r.get("href", ""), "titolo": r.get("title", ""), "snippet": r.get("body", "")} for r in res or []]


@tool(
    "cerca_online",
    title="Ricerca online",
    description=(
        "Cerca sul web (Google, con ripiego automatico su DuckDuckGo e Bing) e restituisce i primi risultati "
        "organici: titolo, URL e descrizione. Usalo quando non sei sicuro di un'informazione, per dati "
        "aggiornati o per verificare un'ipotesi; poi leggi le pagine piu' utili con leggi_pagina_web. "
        "Per notizie recenti preferisci cerca_notizie, per immagini cerca_immagini."
    ),
    read_only=True,
)
async def cerca_online(query: str, num_risultati: int = 10) -> str:
    query = (query or "").strip()
    if not query:
        raise UserError("Query vuota.")
    n = max(1, min(int(num_risultati or 10), 20))
    errori = []
    motori = []
    if PLAYWRIGHT_DISPONIBILE:
        motori.append(("Google", lambda: _cerca_google(query)))
    motori.append(("DuckDuckGo", lambda: asyncio.to_thread(_cerca_ddg, query, n)))
    if PLAYWRIGHT_DISPONIBILE:
        motori.append(("Bing", lambda: _cerca_bing(query)))
    for nome, fn in motori:
        try:
            risultati = _filtra_risultati(await asyncio.wait_for(fn(), timeout=35), n)
            if risultati:
                righe = [f"{i}. {r['titolo']}\n   {r['url']}\n   {r['snippet']}".rstrip()
                         for i, r in enumerate(risultati, 1)]
                return f"Risultati {nome} per '{query}':\n\n" + "\n\n".join(righe)
            errori.append(f"{nome}: nessun risultato")
        except Exception as e:
            logger.warning(f"Ricerca {nome} fallita: {e}")
            errori.append(f"{nome}: {type(e).__name__}: {str(e)[:120]}")
    return f"Nessun risultato per '{query}'. Dettagli: " + "; ".join(errori)


@tool(
    "cerca_notizie",
    title="Cerca notizie",
    description=(
        "Cerca le notizie piu' recenti su un argomento (Google News, in italiano) con titolo, fonte, data e link. "
        "Usalo per attualita', eventi recenti, risultati sportivi, ultime notizie."
    ),
    read_only=True,
)
async def cerca_notizie(argomento: str, num_risultati: int = 8) -> str:
    n = max(1, min(int(num_risultati or 8), 25))
    url = f"{GOOGLE_NEWS_RSS}?q={quote_plus(argomento)}&hl=it&gl=IT&ceid=IT:it"
    try:
        dati, _, _ = await asyncio.to_thread(http_get, url, max_bytes=5_000_000)
        root = ET.fromstring(dati)
        items = root.findall("./channel/item")[:n]
        righe = []
        for i, it in enumerate(items, 1):
            titolo = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            fonte = (it.findtext("source") or "").strip()
            data_txt = it.findtext("pubDate") or ""
            with contextlib.suppress(Exception):
                from email.utils import parsedate_to_datetime
                data_txt = fmt_dt(parsedate_to_datetime(data_txt))
            righe.append(f"{i}. {titolo}\n   Fonte: {fonte} · {data_txt}\n   {link}")
        if righe:
            return f"Notizie su '{argomento}':\n\n" + "\n\n".join(righe)
    except UserError:
        raise
    except Exception as e:
        logger.warning(f"Google News fallito: {e}")
    if DDGS is not None:
        res = await asyncio.to_thread(lambda: DDGS().news(argomento, region="it-it", max_results=n))
        righe = [f"{i}. {r.get('title')}\n   Fonte: {r.get('source')} · {r.get('date')}\n   {r.get('url')}"
                 for i, r in enumerate(res or [], 1)]
        if righe:
            return f"Notizie su '{argomento}':\n\n" + "\n\n".join(righe)
    return f"Nessuna notizia trovata per '{argomento}'."
