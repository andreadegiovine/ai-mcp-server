"""Browser Chromium (Playwright) condiviso dai tool web: sessione persistente e anti-SSRF."""

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Any

from nucleo.configurazione import DATA_DIR, TZ_NAME, env_int, env_str, logger
from nucleo.rete import USER_AGENT, url_consentito
from nucleo.server import FUNZIONALITA, UserError

try:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
except ImportError:  # pragma: no cover
    async_playwright = None


CHROMIUM_PATH = env_str("CHROMIUM_PATH")
BROWSER_PROXY = env_str("BROWSER_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
BROWSER_MAX_PAGES = env_int("BROWSER_MAX_PAGES", 3)
PLAYWRIGHT_DISPONIBILE = async_playwright is not None
FUNZIONALITA["web"] = PLAYWRIGHT_DISPONIBILE


class BrowserManager:
    def __init__(self) -> None:
        self._pw: "Playwright" | None = None
        self._browser: "Browser" | None = None
        self._context: "BrowserContext" | None = None
        self._lock = asyncio.Lock()
        self._sem: asyncio.Semaphore | None = None
        self._stato_file = DATA_DIR / "browser_state.json"

    async def _avvia(self) -> "BrowserContext":
        if async_playwright is None:
            raise UserError("Playwright non installato (pip install playwright && playwright install chromium)")
        logger.info("Avvio sessione browser Playwright...")
        self._pw = await async_playwright().start()
        opzioni: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        }
        if CHROMIUM_PATH:
            opzioni["executable_path"] = CHROMIUM_PATH
        if BROWSER_PROXY:
            bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
            opzioni["proxy"] = {"server": BROWSER_PROXY, **({"bypass": bypass} if bypass else {})}
        try:
            self._browser = await self._pw.chromium.launch(**opzioni)
        except Exception as e:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None
            if "doesn't exist" in str(e) or "does not exist" in str(e):
                percorso = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
                comando = (
                    f"PLAYWRIGHT_BROWSERS_PATH={percorso} " if percorso else ""
                ) + f"{Path(sys.executable).parent / 'playwright'} install chromium"
                logger.error(f"Browser Chromium non installato per questa versione di Playwright. Esegui: {comando}")
                raise UserError(
                    "Il browser del server non è installato (probabilmente Playwright è stato "
                    f"aggiornato). L'amministratore deve eseguire: {comando}"
                )
            raise
        ctx_opts: dict[str, Any] = dict(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="it-IT",
            timezone_id=TZ_NAME,
            accept_downloads=False,
            extra_http_headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.8"},
        )
        if self._stato_file.is_file():
            ctx_opts["storage_state"] = str(self._stato_file)
        try:
            self._context = await self._browser.new_context(**ctx_opts)
        except Exception:
            ctx_opts.pop("storage_state", None)
            self._context = await self._browser.new_context(**ctx_opts)
        await self._context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        await self._context.route("**/*", self._filtro_richieste)
        logger.info("Sessione browser pronta.")
        return self._context

    @staticmethod
    async def _filtro_richieste(route) -> None:
        url = route.request.url
        if url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
            return
        ok, motivo = await asyncio.to_thread(url_consentito, url)
        if not ok:
            logger.warning(f"Richiesta browser bloccata: {url} ({motivo})")
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    async def contesto(self) -> "BrowserContext":
        async with self._lock:
            if self._context is not None and self._browser is not None and self._browser.is_connected():
                return self._context
            await self._chiudi_interno(salva=False)
            return await self._avvia()

    @contextlib.asynccontextmanager
    async def pagina(self, blocca_media: bool = True):
        if self._sem is None:
            self._sem = asyncio.Semaphore(BROWSER_MAX_PAGES)
        async with self._sem:
            ctx = await self.contesto()
            page = await ctx.new_page()
            if blocca_media:

                async def _blocca(route):
                    if route.request.resource_type in ("media", "font"):
                        await route.abort()
                    else:
                        await route.fallback()

                await page.route("**/*", _blocca)
            try:
                yield page
            finally:
                with contextlib.suppress(Exception):
                    await page.close()

    async def _chiudi_interno(self, salva: bool = True) -> None:
        try:
            if self._context is not None:
                if salva:
                    with contextlib.suppress(Exception):
                        await self._context.storage_state(path=str(self._stato_file))
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()
        except Exception as e:
            logger.warning(f"Errore chiusura browser: {e}")
        finally:
            self._context = self._browser = self._pw = None

    async def chiudi(self) -> None:
        async with self._lock:
            await self._chiudi_interno(salva=True)


BROWSER = BrowserManager()


async def accetta_cookie(page: "Page") -> None:
    """Chiude i banner cookie più comuni (Google, Bing, generici)."""
    selettori = [
        "#L2AGLb",
        "button:has-text('Accetta tutto')",
        "button:has-text('Accetta tutti')",
        "button:has-text('Accept all')",
        "#bnp_btn_accept",
        "button:has-text('Accetta')",
        "button:has-text('Accetto')",
        "button#onetrust-accept-btn-handler",
    ]
    for frame in [page.main_frame, *page.frames[1:4]]:
        for sel in selettori:
            try:
                btn = frame.locator(sel).first
                if await btn.is_visible(timeout=400):
                    await btn.click(timeout=1500)
                    await page.wait_for_timeout(500)
                    logger.info(f"Banner cookie chiuso ({sel})")
                    return
            except Exception:
                continue
