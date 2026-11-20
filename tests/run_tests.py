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
    "SMTP_HOST": "smtp.test", "SMTP_USER": "bot@test.it", "SMTP_PASSWORD": "pw",
    "OPENWA_URL": "http://127.0.0.1:18080", "OPENWA_API_KEY": "owa-key", "OPENWA_SESSION_ID": "3f6c1a2e-sessione",
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

# --- SMTP finto ---
SMTP_LOG = []
class FakeSMTP:
    def __init__(self, host, port, timeout=None):
        SMTP_LOG.append(("connect", host, port))
    def starttls(self, context=None):
        SMTP_LOG.append(("starttls",))
    def login(self, utente, password):
        if password != "pw":
            raise srv.smtplib.SMTPAuthenticationError(535, b"bad credentials")
        SMTP_LOG.append(("login", utente))
    def send_message(self, msg):
        SMTP_LOG.append(("send", msg))
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
srv.smtplib.SMTP = FakeSMTP

import re as re_t
def codice(t):
    m = re_t.search(r"codice_conferma='([0-9A-F]{6})'", t)
    return m.group(1) if m else ""

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
            check("chiedi_all_utente: opzioni da testo con | e frase pronta",
                  "Quale versione vuoi ascoltare: Originale, Live 1985 oppure Remaster? Oppure dimmi tu cosa preferisci." in t
                  and "Originale | Live 1985 | Remaster" in t and "risposta libera" in t and "aspettare" in t, t)
            t = testo(await call("chiedi_all_utente", domanda="Scegli un numero da 1 a 5:", opzioni=["1", "2", "3", "4", "5"], risposta_libera=False))
            check("chiedi_all_utente: numeri senza elenco ridondante", "Scegli un numero da 1 a 5: 1, 2, 3, 4 oppure 5?" in t
                  and "1. 1" not in t and "solo tra queste" in t and "solo in italiano" in t, t)
            t = testo(await call("chiedi_all_utente", domanda="Quali stanze?", opzioni="Cucina|Bagno", scelta_multipla=True, risposta_libera=False))
            check("chiedi_all_utente: scelta multipla a voce", "Quali stanze: Cucina e Bagno? Puoi sceglierne anche più di una." in t, t)
            t = testo(await call("chiedi_all_utente", domanda="Accendo tutte le luci?", opzioni=["Sì", "No"], risposta_libera=False))
            check("chiedi_all_utente: opzioni come lista, solo scelte", "Accendo tutte le luci: Sì oppure No?" in t and "solo tra queste" in t, t)
            t = testo(await call("chiedi_all_utente", domanda="Come si chiama il tuo gatto?"))
            check("chiedi_all_utente: domanda aperta", "Risposte valide" not in t and "Come si chiama il tuo gatto?" in t, t)
            t = testo(await call("chiedi_all_utente", domanda="  "))
            check("chiedi_all_utente: domanda vuota", "vuota" in t, t)

            # --- rubrica, email, whatsapp (conferma in chat) ---
            t = testo(await call("rubrica_cerca")); check("rubrica vuota", "Nessun contatto" in t, t)
            t = testo(await call("invia_email", destinatario="io", oggetto="Perdita d'acqua", testo="Gentile amministratore, segnalo una perdita."))
            check("email: senza indirizzo chiede a quale email", "A quale indirizzo email te la mando?" in t and "rubrica_salva(nome='io'" in t, t)
            t = testo(await call("rubrica_salva", nome="io", email="non-valida")); check("rubrica: email non valida", "non valido" in t, t)
            t = testo(await call("rubrica_salva", nome="io", email="andrea@example.com")); check("rubrica: salva email utente", "andrea@example.com" in t, t)
            t = testo(await call("invia_email", destinatario="me", oggetto="Perdita d'acqua", testo="Gentile amministratore, segnalo una perdita.",
                                 allegati=[W + "/img/pasta.png"]))
            cod = codice(t)
            check("email: chiede conferma 'Mando la mail a ...?' senza inviare",
                  "Mando la mail a andrea@example.com?" in t and cod and not any(x[0] == "send" for x in SMTP_LOG), t)
            t = testo(await call("invia_email", codice_conferma=cod))
            inviati = [x[1] for x in SMTP_LOG if x[0] == "send"]
            check("email: inviata dopo conferma", "Email inviata a andrea@example.com" in t and len(inviati) == 1, t)
            m = inviati[-1]
            allegati_m = [(a.get_filename(), a.get_content_type()) for a in m.iter_attachments()]
            check("email: intestazioni, STARTTLS, login e allegato immagine",
                  m["To"] == "andrea@example.com" and m["Subject"] == "Perdita d'acqua" and ("starttls",) in SMTP_LOG
                  and ("login", "bot@test.it") in SMTP_LOG and allegati_m == [("pasta.png", "image/png")], (dict(m.items()), allegati_m))
            t = testo(await call("invia_email", codice_conferma=cod)); check("email: codice non riutilizzabile", "non valido" in t, t)
            t = testo(await call("invia_email", codice_conferma="ABC123")); check("email: codice inventato rifiutato", "non valido" in t, t)
            t = testo(await call("invia_email", destinatario="amministratore condominio", testo="Buongiorno"))
            check("email: contatto sconosciuto -> chiede l'indirizzo", "Qual è l'indirizzo email di amministratore condominio?" in t, t)
            await call("rubrica_salva", nome="Amministratore Condominio", email="admin@condominio.it")
            t = testo(await call("invia_email", destinatario="amministratore", testo="Buongiorno"))
            check("email: contatto trovato per nome parziale", "Mando la mail a amministratore condominio all'indirizzo admin@condominio.it?" in t, t)
            t = testo(await call("invia_email", destinatario="io", testo="x", allegati=["/etc/passwd"]))
            check("email: allegati fuori workspace bloccati", "fuori dal workspace" in t, t)
            srv.SMTP_PASSWORD = "sbagliata"
            t = testo(await call("invia_email", destinatario="io", testo="prova"))
            t = testo(await call("invia_email", codice_conferma=codice(t)))
            check("email: credenziali errate -> messaggio chiaro", "password per le app" in t, t)
            srv.SMTP_PASSWORD = "pw"

            t = testo(await call("invia_whatsapp", destinatario="io", testo="Ecco le immagini", immagini=[W + "/img/pasta.png"]))
            check("whatsapp: senza numero chiede a quale numero", "A quale numero WhatsApp te lo mando?" in t and "telefono=" in t, t)
            t = testo(await call("rubrica_salva", nome="io", telefono="340 343 4334"))
            check("rubrica: numero normalizzato, email mantenuta", "telefono 3403434334" in t and "andrea@example.com" in t, t)
            t = testo(await call("invia_whatsapp", destinatario="io", testo="Ecco le immagini", immagini=[W + "/img/pasta.png"]))
            cod = codice(t)
            check("whatsapp: chiede conferma 'Mando il messaggio ... al numero ...?'",
                  "Mando il messaggio con un'immagine al numero 3403434334?" in t and cod and not mocks.WA, t)
            t = testo(await call("invia_whatsapp", codice_conferma=cod))
            import base64 as b64
            check("whatsapp (OpenWA): testo + immagine base64 sui percorsi della sessione",
                  "inviato al numero 3403434334" in t and len(mocks.WA) == 2
                  and mocks.WA[0][0] == "/api/sessions/3f6c1a2e-sessione/messages/send-text"
                  and mocks.WA[0][1] == {"chatId": "393403434334@c.us", "text": "Ecco le immagini"}
                  and mocks.WA[1][0].endswith("/messages/send-image") and mocks.WA[1][1]["mimetype"] == "image/png"
                  and b64.b64decode(mocks.WA[1][1]["base64"])[:4] == b"\x89PNG", (t, [x[0] for x in mocks.WA]))
            t = testo(await call("invia_whatsapp", destinatario="+44 7700 900123", testo="ciao"))
            check("whatsapp: numero estero", "al numero +447700900123?" in t, t)
            srv.OPENWA_SESSION_ID = "sbagliata"
            t = testo(await call("invia_whatsapp", destinatario="io", testo="x"))
            t = testo(await call("invia_whatsapp", codice_conferma=codice(t)))
            check("whatsapp (OpenWA): sessione inesistente -> messaggio chiaro", "non trova la sessione 'sbagliata'" in t, t)
            srv.OPENWA_SESSION_ID, srv.OPENWA_API_KEY = "3f6c1a2e-sessione", "chiave-errata"
            t = testo(await call("invia_whatsapp", destinatario="io", testo="x"))
            t = testo(await call("invia_whatsapp", codice_conferma=codice(t)))
            check("whatsapp (OpenWA): chiave errata -> messaggio chiaro", "OPENWA_API_KEY" in t, t)
            srv.OPENWA_API_KEY = "owa-key"
            check("invii registrati nel log", (DATA / "invii.log").read_text().count("\n") == 2, (DATA / "invii.log").read_text())

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

            # browser mancante (es. Playwright aggiornato senza 'playwright install') -> messaggio chiaro
            percorso_ok = srv.CHROMIUM_PATH
            srv.CHROMIUM_PATH = "/opt/non-esiste/chrome"
            try:
                await srv.BrowserManager().contesto()
                errore = ""
            except srv.UserError as e:
                errore = str(e)
            srv.CHROMIUM_PATH = percorso_ok
            check("browser mancante: messaggio con il comando per installarlo", "playwright install chromium" in errore, errore)

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

            # --- cartella per conversazione (parametro id_conversazione) ---
            sch = tools["file_scrivi"].inputSchema["properties"]
            check("id_conversazione nello schema con descrizione, ctx nascosto",
                  "id_conversazione" in sch and "sottocartella" in str(sch["id_conversazione"]) and "ctx" not in sch, sch)
            t = testo(await call("file_scrivi", percorso="note.txt", contenuto="appunti chat 123", id_conversazione="chat-123"))
            check("conv: file creato nella cartella della conversazione", "Creato note.txt" in t
                  and (DATA / "workspace" / "chat-123" / "note.txt").read_text() == "appunti chat 123", t)
            t = testo(await call("file_leggi", percorso="note.txt", id_conversazione="chat-123")); check("conv: lettura", "appunti chat 123" in t, t)
            t = testo(await call("file_leggi", percorso="note.txt")); check("conv: senza id si lavora nella radice", "non trovato" in t, t)
            t = testo(await call("file_elenca", id_conversazione="chat-123"))
            check("conv: elenco relativo alla cartella", "Cartella della conversazione chat-123" in t and "note.txt" in t and "prog/" not in t, t)
            t = testo(await call("file_elenca", id_conversazione="chat-nuova"))
            check("conv: cartella creata se non esiste", "(vuota)" in t and (DATA / "workspace" / "chat-nuova").is_dir(), t)
            t = testo(await call("file_leggi", percorso="../prog/app.py", id_conversazione="chat-123"))
            check("conv: non si esce dalla cartella della conversazione", "fuori dal workspace della conversazione" in t, t)
            t = testo(await call("file_leggi", percorso="note.txt", id_conversazione="../etc"))
            check("conv: ID non valido rifiutato", "ID conversazione non valido" in t, t)
            t = testo(await call("terminale_esegui", comando="pwd && echo $WORKSPACE && ls", id_conversazione="chat-123"))
            check("conv: terminale parte dalla cartella della conversazione", t.count("/workspace/chat-123") == 2 and "note.txt" in t and "[cartella: ." in t, t)
            t = testo(await call("esegui_python", codice="open('da_python.txt','w').write('ok'); print('fatto')", id_conversazione="chat-123"))
            check("conv: python scrive nella cartella della conversazione", "fatto" in t and (DATA / "workspace" / "chat-123" / "da_python.txt").exists(), t)
            t = testo(await call("file_cerca", regex="appunti", id_conversazione="chat-123")); check("conv: ricerca nei file", "note.txt:1:" in t, t)
            t = testo(await call("file_modifica", percorso="note.txt", testo_vecchio="123", testo_nuovo="centoventitre", id_conversazione="chat-123"))
            check("conv: modifica", "Modificato note.txt" in t, t)
            t = testo(await call("invia_email", destinatario="io", testo="allegato locale", allegati=["note.txt"], id_conversazione="chat-123"))
            t = testo(await call("invia_email", codice_conferma=codice(t)))
            m = [x[1] for x in SMTP_LOG if x[0] == "send"][-1]
            check("conv: allegato preso dalla cartella della conversazione", "Email inviata" in t
                  and [a.get_filename() for a in m.iter_attachments()] == ["note.txt"], t)
            t = testo(await call("scarica_file", url=W + "/file.bin", id_conversazione="chat-123"))
            check("conv: scarica_file nella cartella della conversazione", (DATA / "workspace" / "chat-123" / "file.bin").exists(), t)
            r = await call("screenshot_pagina", url=W + "/articolo", id_conversazione="chat-123")
            check("conv: screenshot nella cartella della conversazione", any((DATA / "workspace" / "chat-123" / "screenshot").glob("*.png")), testo(r))
            t = testo(await call("terminale_esegui", comando="sleep 2", in_background=True, id_conversazione="chat-123"))
            t1 = testo(await call("terminale_processi", azione="lista", id_conversazione="chat-123"))
            t2 = testo(await call("terminale_processi", azione="lista", id_conversazione="chat-nuova"))
            check("conv: lista processi filtrata per conversazione", "sleep 2" in t1 and "Nessun processo" in t2, (t1, t2))

            # --- file_leggi: documenti (Word, Excel, PowerPoint, OpenDocument, PDF, OCR, ...) ---
            import documenti
            disp = await asyncio.to_thread(documenti.crea, DATA / "workspace" / "docs", mocks._pdf())
            leggi = lambda f: call("file_leggi", percorso=f, id_conversazione="docs")  # noqa: E731
            t = testo(await leggi("relazione.docx"))
            check("documenti: Word .docx (titoli, elenchi, tabelle)", "[Documento word: relazione.docx" in t
                  and "# Relazione condominio" in t and "- Primo punto" in t and "Tetto | 12500" in t, t)
            t = testo(await leggi("spese.xlsx"))
            check("documenti: Excel .xlsx (fogli, date, numeri)", "## Foglio: Spese" in t and "15/03/2026 | Pulizia scale | 120.5" in t
                  and "01/04/2026 | Ascensore | 300" in t and "## Foglio: Note" in t, t)
            t = testo(await leggi("lavori.pptx"))
            check("documenti: PowerPoint .pptx (slide, tabelle, note)", "--- Slide 1 ---" in t and "Piano lavori 2027" in t
                  and "Facciata | 40000" in t and "[Note: Chiedere tre preventivi]" in t, t)
            t = testo(await leggi("testo.pdf"))
            check("documenti: PDF con testo", "--- Pagina 1 ---" in t and "4200 kWh" in t, t)
            t = testo(await leggi("protetto.pdf")); check("documenti: PDF protetto da password", "password" in t, t)
            t = testo(await leggi("nota.rtf")); check("documenti: RTF", "Ciao mondo, è una nota RTF." in t and "Seconda riga" in t, t)
            t = testo(await leggi("libro.epub"))
            check("documenti: EPUB nell'ordine dei capitoli", "Capitolo uno" in t and "Capitolo due" in t
                  and t.index("Capitolo uno") < t.index("Capitolo due"), t)
            t = testo(await leggi("convocazione.eml"))
            check("documenti: email .eml", "Subject: Convocazione assemblea" in t and "10 dicembre" in t and "Allegati: odg.txt" in t, t)
            t = testo(await leggi("pacchetto.zip")); check("documenti: zip (elenco file)", "dentro/uno.txt" in t and "2 file" in t, t)
            t = testo(await leggi("pacchetto.tar.gz")); check("documenti: tar.gz (elenco file)", "nota.rtf" in t, t)
            t = testo(await leggi("rotto.docx")); check("documenti: file danneggiato -> messaggio chiaro", "danneggiato" in t, t)
            t = testo(await leggi("pagina.html")); check("documenti: HTML letto come sorgente", "<p>Sorgente HTML</p>" in t, t)
            t = testo(await leggi("dati.bin")); check("documenti: binario sconosciuto", "formato non supportato" in t, t)
            t = testo(await call("file_leggi", percorso="spese.xlsx", id_conversazione="docs", da_riga=3, num_righe=1))
            check("documenti: paginazione con da_riga", "    3  " in t and "[righe 3-3 di" in t, t)
            if disp["libreoffice"]:
                t = testo(await leggi("relazione.doc"))
                check("documenti: Word .doc (LibreOffice)", "Relazione condominio" in t and "Tetto | 12500" in t, t)
                t = testo(await leggi("relazione.odt"))
                check("documenti: OpenDocument .odt", "# Relazione condominio" in t and "Tetto | 12500" in t, t)
                t = testo(await leggi("spese.xls")); check("documenti: Excel .xls (xlrd)", "Pulizia scale" in t and "## Foglio: Spese" in t, t)
                t = testo(await leggi("spese.ods")); check("documenti: OpenDocument .ods", "Pulizia scale" in t and "Spese" in t, t)
                t = testo(await leggi("lavori.ppt")); check("documenti: PowerPoint .ppt (LibreOffice)", "Piano lavori 2027" in t and "Facciata" in t, t)
                t = testo(await leggi("lavori.odp")); check("documenti: OpenDocument .odp", "--- Slide 1 ---" in t and "Piano lavori 2027" in t, t)
                t = testo(await leggi("esportato.xls")); check("documenti: falso .xls (HTML) via LibreOffice", "Rossi | 350" in t, t)
            else:
                print("   (LibreOffice non installato: test dei formati vecchi e OpenDocument saltati)")
            if disp["ocr"]:
                t = testo(await leggi("scansione.pdf")); check("documenti: PDF scansionato con OCR", "4521" in t and "OCR" in t, t)
                t = testo(await leggi("scritta.png")); check("documenti: immagine con OCR", "FATTURA" in t and "4521" in t, t)
            else:
                print("   (tesseract non installato: test OCR saltati)")
            originale_which = srv.shutil.which
            srv.shutil.which = lambda n, *a, **k: None if n in ("tesseract", "soffice", "libreoffice") else originale_which(n, *a, **k)
            try:
                shutil.copy(DATA / "workspace" / "docs" / "scansione.pdf", DATA / "workspace" / "docs" / "scansione2.pdf")
                t = testo(await leggi("scansione2.pdf"))
                check("documenti: senza tesseract indica come installarlo", "tesseract-ocr" in t and "scansionato" in t, t)
                t = testo(await leggi("scritta.png")); check("documenti: immagine senza OCR -> vedi_immagine", "vedi_immagine" in t, t)
                if disp["libreoffice"]:
                    shutil.copy(DATA / "workspace" / "docs" / "relazione.doc", DATA / "workspace" / "docs" / "relazione2.doc")
                    t = testo(await leggi("relazione2.doc"))
                    check("documenti: senza LibreOffice indica come installarlo", "libreoffice-writer-nogui" in t, t)
            finally:
                srv.shutil.which = originale_which

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

    # --- Domande con modulo interattivo (elicitation MCP, attiva di default sul server principale) ---
    from mcp import types as mtypes
    check("elicitation attiva di default", srv.ENABLE_ELICITATION is True)
    risposte = []
    async def risponde(context, params):
        risposte.append(params)
        schema = params.requestedSchema["properties"]
        if "Rifiuta" in params.message:
            return mtypes.ElicitResult(action="decline")
        if "conferma" in schema:
            return mtypes.ElicitResult(action="accept", content={"conferma": "NON CONFERMARE" not in params.message})
        if "scelte" in schema:
            return mtypes.ElicitResult(action="accept", content={"scelte": ["Cucina", "Bagno"]})
        if "scelta" in schema:
            return mtypes.ElicitResult(action="accept", content={"scelta": schema["scelta"]["enum"][1]})
        return mtypes.ElicitResult(action="accept", content={"risposta": "Micio"})
    async with streamablehttp_client("http://127.0.0.1:18000/", headers=hdr) as (rd, wr, _):
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
            n_inv = len([x for x in SMTP_LOG if x[0] == "send"])
            t = testo(await s2.call_tool("invia_email", {"destinatario": "io", "oggetto": "Test", "testo": "Ciao"}))
            check("elicitation: conferma invio email con modulo", "Email inviata" in t and len([x for x in SMTP_LOG if x[0] == "send"]) == n_inv + 1
                  and "Mando la mail a andrea@example.com?" in risposte[-1].message, t)
            t = testo(await s2.call_tool("invia_email", {"destinatario": "io", "testo": "NON CONFERMARE"}))
            check("elicitation: invio non confermato dal modulo", "annullato" in t and len([x for x in SMTP_LOG if x[0] == "send"]) == n_inv + 1, t)
    # client senza supporto elicitation -> ripiego sulla domanda in chat
    async with streamablehttp_client("http://127.0.0.1:18000/", headers=hdr) as (rd, wr, _):
        async with ClientSession(rd, wr) as s3:
            await s3.initialize()
            t = testo(await s3.call_tool("chiedi_all_utente", {"domanda": "Quale versione?", "opzioni": "A|B"}))
            check("elicitation: client non compatibile -> domanda in chat", "Frase da dire" in t, t)

    # --- Server con ENABLE_ELICITATION=false (modalita' stateless + JSON, quella originale) ---
    os.environ["ENABLE_ELICITATION"] = "false"
    srv_off = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv_off)
    os.environ.pop("ENABLE_ELICITATION")
    servers.append(await serve(srv_off.crea_app(), 18001))
    async with streamablehttp_client("http://127.0.0.1:18001/", headers=hdr) as (rd, wr, _):
        async with ClientSession(rd, wr, elicitation_callback=risponde) as s4:
            await s4.initialize()
            n_tool = len((await s4.list_tools()).tools)
            check("ENABLE_ELICITATION=false: server stateless funzionante", n_tool == len(tools), n_tool)
            t = testo(await s4.call_tool("calcola", {"espressione": "6*7"}))
            check("ENABLE_ELICITATION=false: tool normali", t.endswith("= 42"), t)
            n_prima = len(risposte)
            t = testo(await asyncio.wait_for(s4.call_tool("chiedi_all_utente", {"domanda": "Quale?", "opzioni": "A|B"}), 10))
            check("ENABLE_ELICITATION=false: sempre domanda in chat, anche con client compatibile",
                  "Frase da dire" in t and len(risposte) == n_prima, t)

    # --- '--check' non deve creare cartelle (eseguito da root le renderebbe non scrivibili dal servizio) ---
    import subprocess as sp
    vuota = S / "mai-creata"
    r = sp.run([sys.executable, str(ROOT / "mcp_server.py"), "--check"], env={**os.environ, "DATA_DIR": str(vuota)},
               capture_output=True, text=True, timeout=60)
    check("--check non crea la cartella dati", r.returncode == 0 and "Tool registrati" in r.stdout and not vuota.exists(),
          (r.returncode, r.stdout[-300:], r.stderr[-300:]))
    check("all'avvio la cartella workspace esiste", (DATA / "workspace").is_dir())

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
