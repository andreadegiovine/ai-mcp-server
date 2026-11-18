"""
Test end-to-end del server MCP tramite un vero client MCP Streamable HTTP.

Avvia in locale un Home Assistant finto, un sito web finto (meteo, news, cambi, pagine, PDF, immagini)
e il server MCP, poi chiama i tool come farebbe OpenWebUI. Non serve accesso a internet.

Uso:   python tests/run_tests.py
Richiede: le dipendenze di requirements.txt, Chromium per Playwright e mpv (test musica in locale).
Variabile opzionale: CHROMIUM_PATH=/percorso/chrome se Playwright non trova il suo browser.
"""
import asyncio, os, sys, shutil, tempfile, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = Path(tempfile.mkdtemp(prefix="mcp-test-"))
DATA = S / "testdata"
os.environ.update({
    "DATA_DIR": str(DATA), "HASS_URL": "http://127.0.0.1:18123", "HASS_TOKEN": "test-token",
    "WEB_ALLOW_PRIVATE_URLS": "false", "MCP_API_KEY": "chiave-test", "MCP_PORT": "18000",
    "TIMER_NOTIFY_SERVICE": "notify.mobile_app_test", "MUSIC_MPV_AO": "null", "MUSIC_START_TIMEOUT": "8",
    "HASS_CONFIRM_ENTITIES": "switch.caldaia", "HASS_BLOCKED_ENTITIES": "camera.*",
})
os.environ.pop("HTTPS_PROXY", None); os.environ.pop("https_proxy", None)
sys.path.insert(0, str(Path(__file__).parent))
import mocks  # noqa
import uvicorn

spec = importlib.util.spec_from_file_location("srv", ROOT / "mcp_server.py")
srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)

# --- reindirizza le API esterne ai mock ---
W = "http://127.0.0.1:18080"
srv.OPEN_METEO_URL = W + "/forecast"; srv.OPEN_METEO_GEO_URL = W + "/geo"
srv.GOOGLE_NEWS_RSS = W + "/rss"; srv.FRANKFURTER_URLS = ["http://127.0.0.1:1/nope", W + "/fx"]
# Per i test web locali permettiamo gli URL privati solo per 127.0.0.1:18080 (via funzione)
_orig = srv.url_consentito
def url_test(url):
    if url.startswith(W):
        return True, ""
    return _orig(url)
srv.url_consentito = url_test

# --- YouTube finto: brani = file wav locali ---
WAV = S / "wav"; WAV.mkdir(exist_ok=True)
for vid in ["AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC", "DDDDDDDDDDD", "EEEEEEEEEEE"]:
    mocks.crea_wav(WAV / f"{vid}.wav", 8)
class FakeYT:
    def cerca(self, q, n=5):
        return [srv.Brano("AAAAAAAAAAA", "Bohemian Rhapsody", "Queen", "5:55"), srv.Brano("ZZZZZZZZZZZ", "Altro", "X")]
    def simili(self, vid, n=30):
        return [srv.Brano(v, f"Simile {v[0]}", "Artista") for v in ["AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC", "DDDDDDDDDDD"]]
    def stream(self, vid):
        return {"url": f"http://cdn.example/{vid}.m4a", "titolo": vid, "thumb": ""}
srv.LETTORE.yt = FakeYT()
srv.Brano.url = property(lambda self: str(WAV / f"{self.video_id}.wav"))

from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession
import httpx

RIS = []
def check(nome, cond, info=""):
    RIS.append((nome, bool(cond)))
    print(("✅" if cond else "❌"), nome, ("" if cond else f"\n     -> {str(info)[:1500]}"))

def testo(res):
    return "\n".join(c.text for c in res.content if c.type == "text")

async def serve(app, port):
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    s = uvicorn.Server(cfg); t = asyncio.create_task(s.serve())
    while not s.started:
        await asyncio.sleep(0.05)
    return s, t

async def main():
    servers = [await serve(mocks.hass_app, 18123), await serve(mocks.web_app, 18080), await serve(srv.crea_app(), 18000)]
    # --- auth ---
    async with httpx.AsyncClient() as c:
        r = await c.post("http://127.0.0.1:18000/", json={})
        check("auth: richiesta senza chiave rifiutata (401)", r.status_code == 401, r.status_code)
        r = await c.get("http://127.0.0.1:18000/health")
        check("health senza chiave ok", r.status_code == 200, r.text)

    hdr = {"Authorization": "Bearer chiave-test"}
    async with streamablehttp_client("http://127.0.0.1:18000/", headers=hdr) as (rd, wr, _):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            check("list_tools", len(tools) >= 30, list(tools))
            import re as _re
            titoli_sporchi = [t.title for t in tools.values() if not _re.fullmatch(r"[A-Za-zÀ-ÿ ]+", t.title or "")]
            check("titoli dei tool leggibili a voce (solo lettere e spazi)", not titoli_sporchi, titoli_sporchi)
            print("   tool:", len(tools))
            call = lambda n, **a: s.call_tool(n, a)

            # --- generali ---
            t = testo(await call("data_ora_corrente")); check("data_ora", "Europe/Rome" in t and "settimana" in t, t)
            t = testo(await call("data_ora_corrente", fuso_orario="Mars/Base")); check("data_ora fuso errato", "sconosciuto" in t, t)
            t = testo(await call("calcola", espressione="12 * (3 + 4) / 2")); check("calcola base", t.endswith("= 42"), t)
            t = testo(await call("calcola", espressione="sqrt(16) + 2^10 + 250*15%")); check("calcola funzioni/%", t.endswith("= 1065.5"), t)
            t = testo(await call("calcola", espressione="0.1+0.2")); check("calcola float", t.endswith("= 0.3"), t)
            t = testo(await call("calcola", espressione="9**9**9")); check("calcola DoS bloccato", "non valida" in t, t)
            t = testo(await call("calcola", espressione="__import__('os').system('id')")); check("calcola injection bloccata", "non valida" in t, t)
            t = testo(await call("calcola", espressione="1/0")); check("calcola div0", "zero" in t, t)
            t = testo(await call("meteo", localita="Priolo Gargallo, Siracusa", giorni=2))
            check("meteo per nome", "Priolo Gargallo" in t and "ADESSO" in t and "PREVISIONI" in t and "parzialmente nuvoloso" in t, t)
            t = testo(await call("meteo", latitudine=37.1, longitudine=15.2)); check("meteo per coordinate", "ADESSO" in t, t)
            t = testo(await call("converti_valuta", importo=100, da="eur", a="usd")); check("valuta (con fallback)", "108.32 USD" in t, t)
            t = testo(await call("cerca_notizie", argomento="test")); check("notizie RSS", "Notizia uno" in t and "Fonte A" in t, t)
            t = testo(await call("memoria_salva", testo="L'utente ha una Opel Mokka-e", categoria="auto")); check("memoria salva", "Salvato" in t, t)
            t = testo(await call("memoria_salva", testo="l'utente ha una opel mokka-e")); check("memoria no duplicati", "Gia'" in t, t)
            t = testo(await call("memoria_leggi", cerca="mokka")); check("memoria cerca", "Mokka" in t, t)
            mid = t.split("[")[1].split("]")[0]
            t = testo(await call("memoria_elimina", id=mid)); check("memoria elimina", "eliminata" in t, t)

            # --- domande all'utente (modalita' conversazionale) ---
            check("chiedi_all_utente: ctx non esposto nello schema", "ctx" not in tools["chiedi_all_utente"].inputSchema["properties"],
                  tools["chiedi_all_utente"].inputSchema)
            t = testo(await call("chiedi_all_utente", domanda="Quale versione vuoi ascoltare?", opzioni="Originale | Live 1985 | Remaster"))
            check("chiedi_all_utente: opzioni da testo con |", "1. Originale" in t and "3. Remaster" in t and "rispondere liberamente" in t and "attendere" in t, t)
            t = testo(await call("chiedi_all_utente", domanda="Accendo tutte le luci?", opzioni=["Sì", "No"], risposta_libera=False))
            check("chiedi_all_utente: opzioni come lista, solo scelte", "2. No" in t and "deve scegliere" in t, t)
            t = testo(await call("chiedi_all_utente", domanda="Come si chiama il tuo gatto?"))
            check("chiedi_all_utente: domanda aperta", "Opzioni" not in t and "gatto" in t, t)
            t = testo(await call("chiedi_all_utente", domanda="  "))
            check("chiedi_all_utente: domanda vuota", "vuota" in t, t)

            # --- web ---
            t = testo(await call("leggi_pagina_web", url=W + "/articolo"))
            check("leggi pagina (browser+trafilatura)", "Guida alla pasta" in t and "Parte 1 di" in t and "Menu Home" not in t and "pasta.png" in t, t[:600])
            t2 = testo(await call("leggi_pagina_web", url=W + "/articolo", parte=2)); check("leggi pagina parte 2", "[Parte 2 di" in t2, t2[:200])
            t = testo(await call("leggi_pagina_web", url=W + "/doc.pdf")); check("leggi PDF", "4200 kWh" in t, t)
            t = testo(await call("leggi_pagina_web", url="file:///etc/passwd")); check("blocca file://", "rifiutato" in t, t)
            t = testo(await call("leggi_pagina_web", url="http://192.168.178.48:8123/")); check("blocca IP privato (SSRF)", "rete locale" in t, t)
            t = testo(await call("leggi_pagina_web", url="http://localhost:18123/api/states")); check("blocca localhost", "rete locale" in t, t)
            t = testo(await call("scarica_file", url=W + "/redirect-locale")); check("blocca redirect verso rete interna", "rifiutato" in t, t)
            r = await call("vedi_immagine", sorgente=W + "/img/pasta.png")
            check("vedi_immagine restituisce immagine", any(c.type == "image" for c in r.content) and "![immagine]" in testo(r), testo(r))
            r = await call("screenshot_pagina", url=W + "/articolo")
            check("screenshot restituisce immagine", any(c.type == "image" and c.mimeType == "image/jpeg" for c in r.content), testo(r))
            t = testo(await call("scarica_file", url=W + "/file.bin", percorso_destinazione="dl/file.bin")); check("scarica_file", "dl/file.bin" in t and "1.0 KB" in t, t)
            t = testo(await call("scarica_file", url=W + "/file.bin", percorso_destinazione="../../fuori.bin")); check("scarica_file fuori workspace bloccato", "fuori dal workspace" in t, t)

            # immagini: motori esterni non raggiungibili qui -> sostituisco la sorgente DDG con dati locali
            srv._immagini_ddg = lambda q, n: [{"immagine": W + "/img/pasta.png", "anteprima": W + "/img/pasta.png", "titolo": "Pasta [1]", "pagina": W + "/articolo"},
                                              {"immagine": "http://10.0.0.5/x.png", "anteprima": "", "titolo": "interna", "pagina": ""},
                                              {"immagine": W + "/non-esiste.png", "anteprima": W + "/non-esiste.png", "titolo": "rotta", "pagina": ""}]
            r = await call("cerca_immagini", query="pasta", numero=3)
            t = testo(r)
            check("cerca_immagini: anteprime + markdown, scarta interne/rotte",
                  sum(c.type == "image" for c in r.content) == 1 and "![Pasta (1)](" in t and "interna" not in t and "rotta" not in t, t)
            # ricerca web: simulo fallimento Google/DDG -> verifica il ripiego
            async def g_fail(q): raise RuntimeError("captcha")
            srv._cerca_google = g_fail
            srv._cerca_ddg = lambda q, n: [{"url": "https://www.google.com/aclk?ad=1", "titolo": "Annuncio", "snippet": ""},
                                           {"url": "https://example.org/a", "titolo": "Risultato A", "snippet": "desc A"},
                                           {"url": "https://example.org/a#x", "titolo": "Dup", "snippet": ""}]
            t = testo(await call("cerca_online", query="prova"))
            check("cerca_online fallback DDG + filtro annunci/duplicati", "DuckDuckGo" in t and "Risultato A" in t and "Annuncio" not in t and "Dup" not in t, t)
            check("decodifica redirect Bing", srv._pulisci_url_risultato("https://www.bing.com/ck/a?!&&p=x&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9wYWdpbmE&ntb=1") == "https://example.com/pagina")
            check("decodifica redirect Google", srv._pulisci_url_risultato("https://www.google.it/url?q=https://ex.com/p&sa=U") == "https://ex.com/p")

            # --- workspace / terminale ---
            t = testo(await call("terminale_esegui", comando="echo ciao && pwd && echo $HASS_TOKEN$MCP_API_KEY | wc -c"))
            check("terminale esegue nel workspace, senza segreti in env", "ciao" in t and "workspace" in t and "\n1" in t and "codice uscita: 0" in t, t)
            t = testo(await call("terminale_esegui", comando="sleep 5", timeout_secondi=1)); check("terminale timeout", "TIMEOUT" in t, t)
            t = testo(await call("terminale_esegui", comando="ls", cartella="../..")); check("terminale cartella fuori workspace bloccata", "fuori dal workspace" in t, t)
            t = testo(await call("terminale_esegui", comando="git init -q repo && cd repo && git status | head -1")); check("git disponibile", "On branch" in t or "Sul branch" in t, t)
            t = testo(await call("terminale_esegui", comando="for i in 1 2 3; do echo riga$i; sleep 1; done", in_background=True))
            jid = t.split("id '")[1].split("'")[0]
            await asyncio.sleep(3.5)
            t = testo(await call("terminale_processi", azione="output", id=jid)); check("processo background", "riga3" in t and "terminato" in t, t)
            t = testo(await call("terminale_processi", azione="lista")); check("lista processi", jid in t, t)
            t = testo(await call("file_scrivi", percorso="prog/app.py", contenuto="def f():\n    return 1\n\nprint(f())\n")); check("file_scrivi", "Creato" in t, t)
            t = testo(await call("file_modifica", percorso="prog/app.py", testo_vecchio="return 1", testo_nuovo="return 41 + 1")); check("file_modifica", "Modificato" in t, t)
            t = testo(await call("file_leggi", percorso="prog/app.py")); check("file_leggi con numeri riga", "    2      return 41 + 1" in t, t)
            t = testo(await call("file_cerca", regex="return \\d+", filtro_file="*.py")); check("file_cerca", "prog/app.py:2" in t, t)
            t = testo(await call("file_elenca", pattern="**/*.py")); check("file_elenca", "prog/app.py" in t, t)
            t = testo(await call("esegui_python", codice="import math\nprint(math.factorial(20))")); check("esegui_python", "2432902008176640000" in t, t)
            t = testo(await call("file_leggi", percorso="/etc/passwd")); check("file_leggi fuori workspace bloccato", "fuori dal workspace" in t, t)
            os.symlink("/etc", DATA / "workspace" / "link_etc")
            t = testo(await call("file_leggi", percorso="link_etc/passwd")); check("symlink verso esterno bloccato", "fuori dal workspace" in t, t)

            # --- Home Assistant ---
            t = testo(await call("hass_cerca_entita", testo="temperatura soggiorno"))
            check("hass cerca: temperatura soggiorno", t.split("\n")[1].startswith("• sensor.temperatura_soggiorno") and "[area: Soggiorno]" in t, t)
            t = testo(await call("hass_cerca_entita", testo="autonomia mokka")); check("hass cerca: mokka", "sensor.mokka_autonomia" in t.split("\n")[1], t)
            t = testo(await call("hass_cerca_entita", testo="consumo energetico istantaneo")); check("hass cerca: consumo", "sensor.consumo_istantaneo" in t.split("\n")[1], t)
            t = testo(await call("hass_cerca_entita", dominio="light", area="cucina")); check("hass cerca per dominio+area", "light.cucina" in t and "light.soggiorno" not in t, t)
            t = testo(await call("hass_cerca_entita", testo="luci")); check("hass sinonimi italiani", "light.cucina" in t and "light.soggiorno" in t, t)
            t = testo(await call("hass_stato", entity_id="camera.giardino,light.soggiorno"))
            check("hass stato: segreti nascosti + brightness %", "SEGRETISSIMO" not in t and "50%" in t, t)
            t = testo(await call("hass_stato", entity_id="../../api/config")); check("hass stato: entity_id non valido", "non valido" in t, t)
            t = testo(await call("hass_stato", entity_id="light.inesistente")); check("hass stato: inesistente", "inesistente" in t, t)
            t = testo(await call("hass_panoramica")); check("hass panoramica", "Luci accese" in t and "Aree:" in t and "Sicurezza" in t, t)
            t = testo(await call("hass_storico", entity_id="sensor.temperatura_soggiorno", ore=6)); check("hass storico numerico", "Min: 20" in t and "Max: 24.5" in t, t)
            t = testo(await call("hass_servizi", dominio="homeassistant")); check("hass servizi filtra restart/reload", "restart" not in t and "reload" not in t and "turn_on" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="light", servizio="turn_on", entity_id="light.cucina", dati={"brightness_pct": 40}))
            check("hass accendi luce", "✅" in t and "light.cucina" in t and "= on" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="light", servizio="turn_off", entity_id="", dati='{"entity_id": "light.cucina"}'))
            check("hass dati come stringa JSON", "✅" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="homeassistant", servizio="restart")); check("hass blocca restart", "non e' consentito" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="shell_command", servizio="rm_rf")); check("hass blocca domini non ammessi", "non e' abilitato" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="camera", servizio="snapshot", entity_id="camera.giardino")); check("hass blocca camera", "non e' abilitato" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="light", servizio="turn_on", entity_id="all")); check("hass blocca 'all'", "non consentito" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="light", servizio="turn_on", entity_id="switch.caldaia")); check("hass dominio/entita' incoerenti", "non appartiene" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="light", servizio="turn_on", dati={"area_id": "cucina"})); check("hass blocca area_id", "area_id" in t, t)
            n0 = len(mocks.CALLS)
            t = testo(await call("hass_esegui_servizio", dominio="lock", servizio="unlock", entity_id="lock.porta_ingresso"))
            check("hass serratura richiede conferma", "AZIONE SENSIBILE" in t and len(mocks.CALLS) == n0, t)
            t = testo(await call("hass_esegui_servizio", dominio="cover", servizio="open_cover", entity_id="cover.garage"))
            check("hass garage richiede conferma", "AZIONE SENSIBILE" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="switch", servizio="turn_off", entity_id="switch.caldaia"))
            check("hass HASS_CONFIRM_ENTITIES", "AZIONE SENSIBILE" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="lock", servizio="unlock", entity_id="lock.porta_ingresso", conferma=True))
            check("hass serratura con conferma", "✅" in t and "unlocked" in t, t)
            t = testo(await call("hass_esegui_servizio", dominio="weather", servizio="get_forecasts", entity_id="weather.casa", dati={"type": "daily"}))
            check("hass servizio con risposta (return_response)", "forecast" in t, t)
            check("hass audit log", (DATA / "hass_audit.log").read_text().count("CALL") >= 4)

            # --- Timer ---
            t = testo(await call("timer_imposta", durata="2s", etichetta="pasta")); check("timer 2s", "pasta" in t, t)
            t = testo(await call("timer_imposta", durata="1h30m", etichetta="arrosto")); check("timer 1h30m", "1 h 30 min" in t, t)
            t = testo(await call("timer_imposta", orario="07:30", etichetta="sveglia")); check("sveglia 07:30", "Sveglia" in t and "07:30" in t, t)
            t = testo(await call("timer_imposta", durata="boh")); check("durata non valida", "non valida" in t, t)
            t = testo(await call("timer_lista")); check("timer lista (3 attivi)", "attivi: 3" in t, t)
            await asyncio.sleep(3.5)
            t = testo(await call("timer_lista")); check("timer scaduto", "attivi: 2" in t and "«pasta» scaduto" in t, t)
            notif = [c for c in mocks.CALLS if c[0] in ("persistent_notification", "notify")]
            check("timer notifica HA (persistent + notify)", len(notif) == 2 and "pasta" in notif[0][2]["message"], notif)
            t = testo(await call("timer_annulla", id_o_etichetta="arrosto")); check("timer annulla per etichetta", "arrosto" in t, t)
            t = testo(await call("timer_annulla", id_o_etichetta="tutti")); check("timer annulla tutti", "Sveglia" in t, t)

            # --- Musica: locale (mpv, uscita null) ---
            t = testo(await call("musica_riproduci", richiesta="bohemian rhapsody", dispositivo="locale"))
            check("musica locale avvio", "Bohemian Rhapsody" in t and "locale" in t, t)
            await asyncio.sleep(2.5)
            t = testo(await call("musica_stato")); check("musica stato playing + coda simili", "in riproduzione" in t and "Simile B" in t and "Simile A" not in t, t)
            t = testo(await call("musica_controlla", azione="volume", valore=35)); check("volume locale", "→ 35%" in t, t)
            t = testo(await call("musica_controlla", azione="abbassa_volume")); check("abbassa volume", "→ 25%" in t, t)
            t = testo(await call("musica_controlla", azione="pausa")); check("pausa", "pausa" in t, t)
            await asyncio.sleep(2.5)
            t = testo(await call("musica_stato")); check("stato in pausa", "in pausa" in t, t)
            await call("musica_controlla", azione="riprendi")
            await asyncio.sleep(10)  # brano da 8s: deve passare al successivo da solo
            t = testo(await call("musica_stato")); check("auto-avanzamento al brano simile", "Simile B" in t.split("\n")[0], t)
            t = testo(await call("musica_aggiungi_in_coda", richiesta="altro")); check("aggiungi in coda", "prossimo" in t, t)
            t = testo(await call("musica_controlla", azione="successivo")); check("salta", "Bohemian" in t, t)
            t = testo(await call("musica_controlla", azione="stop")); check("stop", "interrotta" in t, t)

            # --- Musica: su media_player HA (nome fuzzy) ---
            t = testo(await call("musica_riproduci", richiesta="queen", dispositivo="cucina"))
            check("musica su HA (nome 'cucina')", "media_player.cucina" in t, t)
            pm = [c for c in mocks.CALLS if c[1] == "play_media"]
            check("play_media con URL stream", pm and pm[-1][2]["media_content_id"] == "http://cdn.example/AAAAAAAAAAA.m4a", pm[-1:])
            await asyncio.sleep(9)  # il mock finisce il brano dopo 5s
            pm = [c for c in mocks.CALLS if c[1] == "play_media"]
            check("HA auto-avanzamento", pm[-1][2]["media_content_id"] == "http://cdn.example/BBBBBBBBBBB.m4a", pm[-1:])
            t = testo(await call("musica_controlla", azione="volume", valore=20)); check("volume HA", "40% → 20%" in t, t)
            t = testo(await call("musica_controlla", azione="stop")); check("stop HA", "interrotta" in t, t)
            t = testo(await call("musica_riproduci", richiesta="x", dispositivo="bagno")); check("dispositivo inesistente", "Nessun altoparlante" in t, t)

    # --- Domande con modulo interattivo (elicitation MCP, ENABLE_ELICITATION=true) ---
    from mcp import types as mtypes
    os.environ["ENABLE_ELICITATION"] = "true"
    srv_e = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv_e)
    os.environ.pop("ENABLE_ELICITATION")
    servers.append(await serve(srv_e.crea_app(), 18001))
    risposte = []
    async def risponde(context, params):
        risposte.append(params)
        schema = params.requestedSchema["properties"]
        if "Rifiuta" in params.message:
            return mtypes.ElicitResult(action="decline")
        if "scelte" in schema:
            return mtypes.ElicitResult(action="accept", content={"scelte": ["Cucina", "Bagno"]})
        if "scelta" in schema:
            return mtypes.ElicitResult(action="accept", content={"scelta": schema["scelta"]["enum"][1]})
        return mtypes.ElicitResult(action="accept", content={"risposta": "Micio"})
    async with streamablehttp_client("http://127.0.0.1:18001/", headers=hdr) as (rd, wr, _):
        async with ClientSession(rd, wr, elicitation_callback=risponde) as s2:
            await s2.initialize()
            t = testo(await s2.call_tool("chiedi_all_utente", {"domanda": "Quale versione?", "opzioni": "Originale|Live|Remaster"}))
            check("elicitation: scelta singola dal modulo", "ha scelto: Live" in t, t)
            sch = risposte[-1].requestedSchema["properties"]
            check("elicitation: schema con enum + campo libero", sch["scelta"]["enum"] == ["Originale", "Live", "Remaster"] and "risposta" in sch, sch)
            t = testo(await s2.call_tool("chiedi_all_utente", {"domanda": "Quali stanze?", "opzioni": ["Cucina", "Bagno", "Camera"],
                                                               "scelta_multipla": True, "risposta_libera": False}))
            check("elicitation: scelta multipla", "Cucina, Bagno" in t, t)
            t = testo(await s2.call_tool("chiedi_all_utente", {"domanda": "Come si chiama il gatto?"}))
            check("elicitation: risposta libera", "ha risposto: Micio" in t, t)
            t = testo(await s2.call_tool("chiedi_all_utente", {"domanda": "Rifiuta questa domanda"}))
            check("elicitation: utente rifiuta", "preferito non rispondere" in t, t)
    # client senza supporto elicitation sullo stesso server -> ripiego conversazionale
    async with streamablehttp_client("http://127.0.0.1:18001/", headers=hdr) as (rd, wr, _):
        async with ClientSession(rd, wr) as s3:
            await s3.initialize()
            t = testo(await s3.call_tool("chiedi_all_utente", {"domanda": "Quale versione?", "opzioni": "A|B"}))
            check("elicitation: client non compatibile -> domanda in chat", "DOMANDA DA PORRE" in t, t)

    # --- Parsing date/durate (unit test) ---
    from datetime import datetime as D
    rif = D(2026, 11, 17, 15, 0, tzinfo=srv.TZ)  # martedi
    casi = {"domani 9:00": (D(2026, 11, 18, 9, 0), True), "18/11/2026 15:30": (D(2026, 11, 18, 15, 30), True),
            "venerdì alle 20.30": (D(2026, 11, 20, 20, 30), True), "2026-12-24": (D(2026, 12, 24), False),
            "dopodomani": (D(2026, 11, 19), False), "16:45": (D(2026, 11, 17, 16, 45), True),
            "2026-11-20T10:00:00+00:00": (D(2026, 11, 20, 11, 0), True), "prossimo martedì 10": (D(2026, 11, 24, 10, 0), True)}
    for k, (atteso, ha) in casi.items():
        dt, h = srv.parse_data_ora(k, riferimento=rif)
        check(f"parse_data_ora '{k}'", dt.replace(tzinfo=None) == atteso and h == ha, (dt, h))
    for k, v in {"10m": 600, "1h30m": 5400, "1 ora e 15 minuti": 4500, "90s": 90, "25": 1500, "mezz'ora": 1800,
                 "00:05:00": 300, "2 ore": 7200, "1,5 ore": 5400}.items():
        check(f"parse_durata '{k}'", srv.parse_durata(k) == v, srv.parse_durata(k))

    # --- Calendar (servizio Google simulato) ---
    class Req:
        def __init__(self, v): self.v = v
        def execute(self): return self.v
    class Events:
        def __init__(self): self.db = {}
        def list(self, **p):
            return Req({"items": [e for e in self.db.values() if not p.get("q") or p["q"].lower() in e["summary"].lower()]})
        def insert(self, calendarId, body):
            e = dict(body, id=f"ev{len(self.db)}", htmlLink="https://calendar.google.com/x"); self.db[e["id"]] = e; return Req(e)
        def get(self, calendarId, eventId): return Req(self.db[eventId])
        def patch(self, calendarId, eventId, body): self.db[eventId].update(body); return Req(self.db[eventId])
        def delete(self, calendarId, eventId): self.db.pop(eventId); return Req("")
    class Svc:
        ev = Events()
        def events(self): return self.ev
        def calendarList(self): return type("C", (), {"list": lambda s: Req({"items": [{"id": "primary", "summary": "Io", "primary": True, "accessRole": "owner"}]})})()
    srv.CALENDARIO._svc = Svc()
    t = await srv.calendario_crea_evento(titolo="Dentista", inizio="domani 15:00", durata_minuti=45, luogo="Siracusa", promemoria_minuti=30)
    check("calendario crea", "Dentista" in t and "15:00–15:45" in t and "Siracusa" in t, t)
    body = Svc.ev.db["ev0"]
    check("calendario promemoria + timezone", body["reminders"]["overrides"][0]["minutes"] == 30 and body["start"]["timeZone"] == "Europe/Rome", body)
    t = await srv.calendario_crea_evento(titolo="Ferie", inizio="2026-12-24", fine="2026-12-26")
    check("calendario evento giornata intera", "tutto il giorno, 3 giorni" in t, t)
    t = await srv.calendario_eventi(giorni=60, cerca="dent"); check("calendario eventi + filtro", "Dentista" in t and "Ferie" not in t, t)
    t = await srv.calendario_modifica_evento(event_id="ev0", inizio="domani 17:00"); check("calendario modifica mantiene durata", "17:00–17:45" in t, t)
    t = await srv.calendario_elimina_evento(event_id="ev0"); check("calendario elimina chiede conferma", "Chiedi conferma" in t and "ev0" in Svc.ev.db, t)
    t = await srv.calendario_elimina_evento(event_id="ev0", conferma=True); check("calendario elimina", "eliminato" in t and "ev0" not in Svc.ev.db, t)
    t = await srv.calendario_lista(); check("calendario lista", "primary" in t, t)

    for sv, task in reversed(servers):
        sv.should_exit = True
        await task
    ok = sum(r for _, r in RIS)
    print(f"\n=== {ok}/{len(RIS)} test superati ===")
    # verifica pulizia: mpv chiuso
    import subprocess
    print("mpv ancora attivo:", subprocess.run(["pgrep", "-f", "mpv --idle"], capture_output=True).stdout.decode().strip() or "no")
    shutil.rmtree(S, ignore_errors=True)
    sys.exit(0 if ok == len(RIS) else 1)

asyncio.run(main())
