# AI MCP Server

Server **MCP (Model Context Protocol)** in Python che dà a un LLM locale (tramite **OpenWebUI**) una serie di strumenti reali: ricerca e lettura del web con immagini, un terminale con workspace dedicato, il controllo sicuro di **Home Assistant**, musica da YouTube in stile Alexa, timer e sveglie multipli, e **Google Calendar**.

È un unico file (`mcp_server.py`), pensato per girare in un container LXC su Proxmox dentro un virtualenv.

> Lo stato di avanzamento e le attività svolte sono in [`docs/MILESTONES.md`](docs/MILESTONES.md).

---

## Indice

- [Strumenti disponibili](#strumenti-disponibili)
- [Come funziona](#come-funziona)
- [Installazione (Proxmox LXC + venv)](#installazione-proxmox-lxc--venv)
- [Aggiornamento](#aggiornamento)
- [Configurazione (.env)](#configurazione-env)
- [Collegamento a OpenWebUI](#collegamento-a-openwebui)
- [Home Assistant](#home-assistant)
- [Musica](#musica)
- [Domande all'utente](#domande-allutente)
- [Email e WhatsApp](#email-e-whatsapp)
- [Timer e sveglie](#timer-e-sveglie)
- [Google Calendar](#google-calendar)
- [Sicurezza](#sicurezza)
- [Test](#test)
- [Struttura del repository](#struttura-del-repository)
- [Problemi comuni](#problemi-comuni)

---

## Strumenti disponibili

Il server espone **46 tool**, divisi in gruppi. Un gruppo non configurato viene nascosto da solo: i tool di Home Assistant compaiono solo se c'è `HASS_TOKEN`, quelli del calendario solo se ci sono le credenziali Google, email e WhatsApp solo se configurati. Puoi nasconderne altri con `DISABLED_TOOLS`.

### Generali
| Tool | Cosa fa |
|---|---|
| `data_ora_corrente` | Data, ora, giorno della settimana, numero di settimana; fuso orario opzionale |
| `calcola` | Calcolatrice sicura: operatori, `^`, percentuali (`250*15%`), funzioni (`sqrt`, `sin`, `log`, `factorial`…), costanti |
| `meteo` | Meteo attuale, prossime ore e previsioni fino a 16 giorni, per **nome della località** o per coordinate (Open-Meteo) |
| `converti_valuta` | Conversione con i tassi ufficiali BCE (Frankfurter) |
| `memoria_salva` / `memoria_leggi` / `memoria_elimina` | Memoria permanente per preferenze e informazioni sull'utente |
| `chiedi_all_utente` | Fa una domanda all'utente, con opzioni prefissate e/o risposta libera, e ne attende la risposta (vedi [Domande all'utente](#domande-allutente)) |

### Web
| Tool | Cosa fa |
|---|---|
| `cerca_online` | Ricerca web: Google, con ripiego automatico su DuckDuckGo e poi Bing; filtra annunci e duplicati |
| `cerca_notizie` | Ultime notizie da Google News (RSS), con fonte e data |
| `leggi_pagina_web` | Apre una pagina con un browser reale (Playwright) o un **PDF** ed estrae il testo pulito in markdown; i testi lunghi sono divisi in parti |
| `cerca_immagini` | Cerca immagini e **allega le anteprime**, così un modello con visione può vederle; restituisce il markdown per mostrarle in chat |
| `vedi_immagine` | Mostra al modello un'immagine da un URL o dal workspace |
| `screenshot_pagina` | Screenshot di una pagina web, restituito come immagine e salvato nel workspace |
| `scarica_file` | Scarica un file nel workspace, con limite di dimensione |

### Workspace (terminale e file)
| Tool | Cosa fa |
|---|---|
| `terminale_esegui` | Esegue comandi bash nel workspace (git, pip, script, build…), con timeout ed esecuzione in background |
| `terminale_processi` | Elenca, legge l'output o termina i processi in background |
| `esegui_python` | Esegue codice Python con l'interprete del server |
| `file_leggi` / `file_scrivi` / `file_modifica` | Lettura con numeri di riga, scrittura, modifica tramite sostituzione esatta di testo |
| `file_elenca` / `file_cerca` | Elenco con glob e ricerca regex nei file (come `grep -rn`) |

### Home Assistant
| Tool | Cosa fa |
|---|---|
| `hass_cerca_entita` | Trova entità per nome, stanza, tipo o dominio; capisce l'italiano ("autonomia mokka", "luci cucina") |
| `hass_stato` | Stato in tempo reale e attributi di una o più entità |
| `hass_panoramica` | Riepilogo della casa: domini, aree, luci accese, porte aperte, clima, media, sicurezza |
| `hass_storico` | Storico: min, max, media e andamento per i sensori numerici; cambi di stato per gli altri |
| `hass_servizi` | Servizi disponibili per un dominio, con i parametri |
| `hass_esegui_servizio` | Controlla i dispositivi, con le regole di sicurezza descritte sotto |

### Timer, musica, calendario
| Tool | Cosa fa |
|---|---|
| `timer_imposta` / `timer_lista` / `timer_annulla` | Timer multipli con etichetta e sveglie a orario; restano attivi dopo un riavvio |
| `musica_riproduci` | Riproduce da YouTube e poi continua con brani simili, in locale (mpv) o su un altoparlante di Home Assistant |
| `musica_controlla` | Pausa, riprendi, stop, successivo, precedente, volume, attiva o disattiva la radio |
| `musica_aggiungi_in_coda` / `musica_stato` | Aggiunge un brano come prossimo; mostra cosa sta suonando e la coda |
| `calendario_eventi` / `calendario_crea_evento` / `calendario_modifica_evento` / `calendario_elimina_evento` / `calendario_lista` | Google Calendar completo |

### Rubrica, email e WhatsApp
| Tool | Cosa fa |
|---|---|
| `rubrica_salva` / `rubrica_cerca` / `rubrica_elimina` | Rubrica di contatti con email e numero WhatsApp; `io` indica l'utente stesso |
| `invia_email` | Invia un'email con eventuali allegati (immagini da URL o file del workspace), **sempre con conferma** |
| `invia_whatsapp` | Invia un messaggio WhatsApp con testo e/o immagini, **sempre con conferma** |

---

## Come funziona

- **Protocollo:** MCP *Streamable HTTP*, montato sulla **root `/`**, non su `/mcp`. Così si evita il bug di OpenWebUI che toglie lo slash finale dagli URL e produce un redirect 307 seguito da un 404. Di default il server usa sessioni e risposte in streaming, necessarie per i moduli interattivi (vedi [Domande all'utente](#domande-allutente)); con `ENABLE_ELICITATION=false` torna alla modalità senza sessione con risposte JSON (`stateless_http` + `json_response`).
- **Concorrenza:** tutti i tool sono asincroni. Le chiamate bloccanti (HTTP verso le API, yt-dlp, Google API) girano in thread separati, quindi una richiesta lenta non ferma le altre.
- **Browser:** una sessione Chromium persistente gestita da Playwright. I cookie, ad esempio il consenso di Google, vengono salvati in `data/browser_state.json`. Ogni richiesta apre la propria pagina, fino a `BROWSER_MAX_PAGES` pagine in parallelo, e il browser si riavvia da solo se va in crash.
- **Gestione degli errori:** ogni tool restituisce un messaggio leggibile dal modello, ad esempio "⚠️ Entità inesistente, usa hass_cerca_entita", invece di un traceback.
- **Ciclo di vita:** all'avvio vengono ripristinati i timer e parte il monitor della musica. Allo spegnimento si chiudono browser, mpv e processi in background.
- **Endpoint `/health`:** risponde senza autenticazione con lo stato e il numero di tool.

### Cartella dati (`DATA_DIR`, default `./data`)
```
data/
├── workspace/            # unica area di lavoro di terminale e tool sui file
├── memoria.json          # memoria permanente
├── timers.json           # timer e sveglie (ripristinati al riavvio)
├── hass_audit.log        # registro di tutti i comandi inviati a Home Assistant
├── browser_state.json    # cookie della sessione browser
├── google_token.json     # token OAuth di Google (se usi questo metodo)
└── jobs/                 # output dei processi in background
```

---

## Installazione (Proxmox LXC + venv)

Esempio con il server in `/opt/mcp-server`, dentro un container LXC Debian o Ubuntu **unprivileged**.

```bash
apt update && apt install -y python3-venv git mpv
```

```bash
git clone https://github.com/andreadegiovine/ai-mcp-server /opt/mcp-server
```

```bash
python3 -m venv /opt/mcp-server/venv
```

```bash
/opt/mcp-server/venv/bin/pip install -r /opt/mcp-server/requirements.txt
```

Il browser per Playwright va installato in una cartella condivisa, così può usarlo anche l'utente non-root del servizio:

```bash
PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright /opt/mcp-server/venv/bin/playwright install --with-deps chromium
```

`mpv` serve solo per riprodurre musica sul server stesso. Per YouTube, yt-dlp richiede anche un runtime JavaScript (deno), da installare per l'utente che esegue il servizio. Dopo aver creato l'utente `mcp` (vedi [Sicurezza](#sicurezza)):

```bash
su - mcp -c "curl -fsSL https://deno.land/install.sh | sh"
```

```bash
cp /opt/mcp-server/.env.example /opt/mcp-server/.env && nano /opt/mcp-server/.env
```

Prova manuale. Il comando mostra quali gruppi e tool sono attivi:

```bash
/opt/mcp-server/venv/bin/python /opt/mcp-server/mcp_server.py --check
```

Avvio come servizio. Prima crea l'utente `mcp`, come descritto in [Sicurezza](#sicurezza), poi:

```bash
cp /opt/mcp-server/deploy/mcp-server.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now mcp-server
```

```bash
journalctl -u mcp-server -f
```

> Il file `.env` viene letto **solo all'avvio**: dopo ogni modifica esegui `systemctl restart mcp-server`.
> Per aggiornare il codice vedi [Aggiornamento](#aggiornamento).

---

## Aggiornamento

Comandi da eseguire come root nel container. I file `.env` e `data/` sono esclusi da git, quindi l'aggiornamento non tocca configurazione, memoria, timer e token.

**1. Scarica il codice nuovo**

```bash
cd /opt/mcp-server && git pull
```

Se `git pull` si ferma per modifiche locali, controlla cosa hai cambiato con `git status` e `git diff`. Per scartarle e tornare alla versione del repository:

```bash
cd /opt/mcp-server && git stash && git pull
```

**2. Aggiorna le dipendenze**

```bash
/opt/mcp-server/venv/bin/pip install -U -r /opt/mcp-server/requirements.txt
```

`-U` aggiorna anche yt-dlp, che va tenuto aggiornato perché YouTube cambia spesso.

**3. Aggiorna il browser**

Serve solo se nel passo 2 si è aggiornato Playwright, perché ogni versione usa la sua build di Chromium. Rilanciarlo comunque non fa danni:

```bash
PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright /opt/mcp-server/venv/bin/playwright install chromium
```

**4. Controlla eventuali nuove variabili**

Le variabili presenti in `.env.example` e assenti dal tuo `.env` usano il valore predefinito. Per vederle:

```bash
cd /opt/mcp-server && diff <(grep -oE '^#? ?[A-Z_]+=' .env.example | tr -d '# ' | sort -u) <(grep -oE '^[A-Z_]+=' .env | sort -u) | grep '^<'
```

Se il comando non stampa nulla, il tuo `.env` contiene già tutte le variabili dell'esempio.

**5. Riavvia il servizio e controlla**

```bash
systemctl restart mcp-server && systemctl status mcp-server --no-pager
```

```bash
journalctl -u mcp-server -n 30 --no-pager
```

Nel log, la riga `Tool attivi (...)` elenca i tool caricati.

**Tutto in un solo comando** (passi 1, 2, 3 e 5):

```bash
cd /opt/mcp-server && git pull && venv/bin/pip install -U -r requirements.txt && PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright venv/bin/playwright install chromium && systemctl restart mcp-server && journalctl -u mcp-server -n 30 --no-pager
```

**Tornare alla versione precedente** se qualcosa non va:

```bash
cd /opt/mcp-server && git log --oneline -5
```

```bash
cd /opt/mcp-server && git checkout <commit> && systemctl restart mcp-server
```

Per tornare all'ultima versione dopo un `checkout`: `git checkout main && git pull`.

---

## Configurazione (.env)

Il server legge il `.env` dalla stessa cartella di `mcp_server.py`. Le variabili già impostate nell'ambiente, ad esempio dal servizio systemd con `EnvironmentFile=`, hanno la precedenza. Un esempio completo è in [`.env.example`](.env.example).

### Generale
| Variabile | Default | Descrizione |
|---|---|---|
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | Indirizzo di ascolto |
| `MCP_API_KEY` | – | **Consigliata.** Se impostata, ogni richiesta deve avere `Authorization: Bearer <chiave>` (o `X-API-Key`) |
| `DATA_DIR` | `./data` | Dati persistenti |
| `WORKSPACE_DIR` | `DATA_DIR/workspace` | Area di lavoro dell'agente |
| `TIMEZONE` | `Europe/Rome` | Fuso orario |
| `DISABLED_TOOLS` | – | Tool da nascondere, separati da virgola |
| `ENABLE_TERMINAL` / `ENABLE_HASS` / `ENABLE_MUSIC` / `ENABLE_CALENDAR` / `ENABLE_EMAIL` / `ENABLE_WHATSAPP` | `true` | Attivano o disattivano interi gruppi |
| `ENABLE_ELICITATION` | `true` | Domande all'utente con modulo interattivo se il client lo supporta, altrimenti testuali; `false` = modalità senza sessione (vedi [Domande all'utente](#domande-allutente)) |
| `ELICITATION_TIMEOUT` | `300` | Secondi di attesa della risposta nel modulo interattivo |

### Web
| Variabile | Default | Descrizione |
|---|---|---|
| `WEB_ALLOW_PRIVATE_URLS` | `false` | Permette ai tool web di aprire indirizzi della rete locale. Attenzione: rischio SSRF |
| `CHROMIUM_PATH` | – | Eseguibile Chromium alternativo |
| `BROWSER_PROXY` | `HTTPS_PROXY` | Proxy per il browser |
| `BROWSER_MAX_PAGES` | `3` | Pagine aperte in parallelo |
| `WEB_CHUNK_CHARS` | `8000` | Dimensione di ciascuna parte in `leggi_pagina_web` |
| `METEO_DEFAULT_LOCALITA` | – | Località usata se il modello non ne indica una |

### Home Assistant
| Variabile | Default | Descrizione |
|---|---|---|
| `HASS_URL` | `http://192.168.178.48:8123` | URL di Home Assistant |
| `HASS_TOKEN` | – | Long-lived access token; se appartiene a un utente admin funziona anche la ricerca per aree |
| `HASS_READ_ONLY` | `false` | Solo lettura |
| `HASS_ALLOWED_DOMAINS` | lista interna | Domini controllabili |
| `HASS_CONFIRM_DOMAINS` | `lock,alarm_control_panel` | Domini che richiedono conferma esplicita |
| `HASS_CONFIRM_ENTITIES` | – | Pattern di entità che richiedono conferma (es. `cover.cancello*`) |
| `HASS_BLOCKED_ENTITIES` | – | Pattern di entità mai controllabili |
| `HASS_ALLOWED_ENTITIES` | – | Se impostata, solo queste entità sono controllabili |
| `HASS_VERIFY_SSL` | `true` | Verifica del certificato |
| `HASS_CACHE_TTL` | `10` | Secondi di cache dell'elenco stati |

### Timer
| Variabile | Descrizione |
|---|---|
| `TIMER_PERSISTENT_NOTIFICATION` | Notifica nell'interfaccia di HA (default `true`) |
| `TIMER_NOTIFY_SERVICE` | Es. `notify.mobile_app_telefono` (push sul telefono) |
| `TIMER_TTS_ENTITY` + `TIMER_TTS_PLAYER` | Annuncio vocale su un altoparlante (`tts.speak`) |
| `TIMER_HASS_SCRIPT` | Script di HA da eseguire; riceve le variabili `messaggio` ed `etichetta` |
| `TIMER_LOCAL_SOUND` | Suono sul server stesso (default `false`) |

### Musica
| Variabile | Default | Descrizione |
|---|---|---|
| `MUSIC_DEFAULT_DEVICE` | `locale` | `locale` (mpv) oppure un `media_player.*` di HA |
| `MUSIC_DEFAULT_VOLUME` | `60` | Volume iniziale di mpv |
| `MUSIC_MPV_AO` / `MUSIC_MPV_AUDIO_DEVICE` | – | Uscita audio di mpv (es. `pulse`, `alsa`) |
| `MUSIC_START_TIMEOUT` | `45` | Secondi dopo i quali un brano che non parte viene saltato |

### Terminale
| Variabile | Default | Descrizione |
|---|---|---|
| `TERMINAL_MAX_OUTPUT` | `12000` | Caratteri massimi di output restituiti |
| `TERMINAL_MAX_TIMEOUT` | `900` | Timeout massimo di un comando, in secondi |
| `DOWNLOAD_MAX_MB` | `200` | Dimensione massima di `scarica_file` |

### Email (SMTP)
| Variabile | Default | Descrizione |
|---|---|---|
| `SMTP_HOST` | – | Server SMTP (Gmail: `smtp.gmail.com`). Senza, lo strumento email è nascosto |
| `SMTP_PORT` | `587` | `587` con STARTTLS, `465` con SSL |
| `SMTP_USER` / `SMTP_PASSWORD` | – | Credenziali (Gmail: indirizzo e *password per le app*) |
| `SMTP_FROM` / `SMTP_NOME` | `SMTP_USER` / `Assistente` | Mittente e nome visualizzato |
| `SMTP_STARTTLS` | `true` | STARTTLS sulla porta 587 |
| `ALLEGATI_MAX_MB` | `20` | Dimensione massima totale degli allegati (vale anche per le immagini WhatsApp) |

### WhatsApp
| Variabile | Default | Descrizione |
|---|---|---|
| `OPENWA_URL` / `OPENWA_API_KEY` / `OPENWA_SESSION_ID` | – | Indirizzo, chiave e ID della sessione del gateway OpenWA. Senza URL e ID della sessione, lo strumento WhatsApp è nascosto |
| `WHATSAPP_PREFISSO` | `39` | Prefisso aggiunto ai numeri scritti senza `+` |

### Google Calendar
| Variabile | Descrizione |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_FILE` | JSON del service account (metodo consigliato) |
| `GOOGLE_OAUTH_TOKEN_FILE` | Token OAuth (default `data/google_token.json`) |
| `GOOGLE_CALENDAR_IDS` | Uno o più ID di calendario; il primo è quello predefinito |
| `GOOGLE_DELEGATED_USER` | Solo per Google Workspace con delega a livello di dominio |

---

## Collegamento a OpenWebUI

1. *Impostazioni → Strumenti / Connessioni → aggiungi un server MCP* (tipo **MCP Streamable HTTP**).
2. URL: `http://<IP_CONTAINER>:8000/`, con lo slash finale.
3. Autenticazione **Bearer**, con lo stesso valore di `MCP_API_KEY`.
4. Abilita gli strumenti sul modello. Per le immagini serve un modello con visione.

Le istruzioni generali per il modello (usare le fonti, mostrare le immagini con `![descrizione](url)`, chiedere conferma per le azioni sensibili) sono incluse nel server. Se il client non le trasmette, conviene copiarle nel prompt di sistema del modello.

> Con molti tool un modello locale piccolo può confondersi. Nascondi quelli che non ti servono con `DISABLED_TOOLS`.

---

## Home Assistant

**Flusso consigliato per il modello:** `hass_cerca_entita` per trovare l'ID, poi `hass_stato` per leggerlo o `hass_esegui_servizio` per controllarlo.

- **Ricerca:** cerca in entity_id, nome, area, `device_class` e dominio. Accetta sinonimi italiani (luce → light, tapparella → cover, temperatura → temperature, consumo → power…) e tollera errori di battitura. Le aree vengono lette tramite `/api/template`, che richiede un token di un utente admin.
- **Letture:** `hass_stato` interroga sempre lo stato in tempo reale. Gli attributi con token o password vengono nascosti, ad esempio l'`access_token` delle telecamere.
- **Controllo**, con queste regole di sicurezza:
  - solo i domini in `HASS_ALLOWED_DOMAINS`. Sono esclusi per default `shell_command`, `hassio`, `camera` e simili;
  - sono sempre bloccati `homeassistant.restart`, `homeassistant.stop`, `reload*` e i servizi di configurazione; di `automation` sono permessi solo `trigger`, `turn_on`, `turn_off` e `toggle`;
  - `entity_id` è obbligatorio e validato. Vengono rifiutati `all`, `area_id`, `device_id`, `floor_id`, `label_id` ed entità di un dominio diverso da quello del servizio;
  - per **serrature, allarmi, garage, cancelli** e per le entità in `HASS_CONFIRM_ENTITIES` il server non esegue nulla e chiede al modello di ottenere il consenso dell'utente, poi di richiamare con `conferma=true`;
  - esistono una blocklist e una allowlist di entità, oltre alla modalità sola lettura;
  - ogni comando viene scritto in `data/hass_audit.log`;
  - per i servizi che restituiscono dati (es. `weather.get_forecasts`) la richiesta della risposta è automatica.

---

## Musica

1. **Ricerca:** YouTube Music (`ytmusicapi`, filtro brani), con ripiego su una ricerca YouTube tramite `yt-dlp`. Accetta anche link YouTube.
2. **Brani simili:** la "radio" di YouTube Music per il brano, con ripiego sul mix `RD<videoId>` di YouTube. La coda si allunga da sola quando restano pochi brani, senza ripetizioni.
3. **Riproduzione:**
   - **Locale:** un processo `mpv` in background controllato tramite socket IPC. mpv riproduce direttamente l'URL di YouTube usando yt-dlp.
   - **Home Assistant:** il server ricava con yt-dlp l'URL diretto dello stream audio (m4a) e lo invia a `media_player.play_media`, con titolo e copertina. Il dispositivo si può indicare per entity_id o per nome ("cucina").
4. **Continuità stile Alexa:** un monitor controlla lo stato ogni 2 secondi. Quando un brano finisce passa al successivo; se un brano non parte entro `MUSIC_START_TIMEOUT` lo salta (al massimo 3 volte di fila); se sul dispositivo parte altro, ad esempio un altro cast, interrompe la coda.

> YouTube cambia spesso: tieni aggiornato `yt-dlp` (`pip install -U yt-dlp`) e installa **deno**.

---

## Domande all'utente

Con `chiedi_all_utente` l'agente può far scegliere l'utente tra alternative ("quale versione del brano?", "quale dei due eventi sposto?"), chiedere un'informazione che gli manca o una conferma prima di un'azione importante.

Parametri:
- `domanda`: il testo della domanda;
- `opzioni`: fino a 10 scelte, come lista oppure come testo separato da `|` (es. `"Originale | Live | Remaster"`);
- `risposta_libera`: se `false`, l'utente deve scegliere tra le opzioni (default `true`);
- `scelta_multipla`: permette di scegliere più opzioni.

Lo strumento funziona in due modalità.

**1. Domanda in chat (funziona sempre).** Lo strumento restituisce al modello la domanda e le opzioni numerate, con l'istruzione di porla all'utente in modo naturale, senza simboli (quindi adatta anche alla lettura vocale), e di fermarsi ad aspettare. La risposta arriva come messaggio normale dell'utente, che può dire anche "la seconda" o riformulare l'opzione. Funziona con qualsiasi client, compreso un futuro servizio vocale.

**2. Modulo interattivo (attivo di default).** Con `ENABLE_ELICITATION=true` il server usa l'*elicitation* MCP: se il client la supporta, mostra all'utente un modulo con le opzioni (menu a tendina, scelta multipla, campo libero) e la risposta torna direttamente al modello, nella stessa chiamata. Se il client non la supporta, lo strumento ripiega da solo sulla domanda in chat.

> Per l'elicitation il server deve inviare una richiesta al client durante l'esecuzione di un tool, cosa possibile solo con sessione e risposte in streaming. Per questo, con `ENABLE_ELICITATION=true`, il server non usa le modalità `stateless_http` e `json_response`. Il ripiego automatico copre la domanda, non il cambio di trasporto: se OpenWebUI ha problemi a collegarsi o a usare gli strumenti, imposta `ENABLE_ELICITATION=false` e riavvia il servizio. Le domande diventano solo testuali e il server torna alla modalità senza sessione, la più conservativa.

---

## Email e WhatsApp

### Come funziona l'invio
1. **Destinatario.** `destinatario` può essere `io` (tu stesso: "mandamela per email"), il nome di un contatto ("amministratore condominio") o direttamente un indirizzo o un numero.
2. **Recapito mancante.** Se in rubrica non c'è, lo strumento non invia nulla e dice al modello di chiederlo: "A quale indirizzo email te la mando?", "A quale numero WhatsApp te lo mando?", "Qual è l'indirizzo email di amministratore condominio?". Quando rispondi, il modello lo salva con `rubrica_salva` e riprova.
3. **Conferma obbligatoria.** Con il recapito in rubrica, la prima chiamata **non invia**: prepara il messaggio e chiede "Mando la mail a tuoindirizzo@gmail.com?" oppure "Mando il messaggio con 3 immagini al numero 3403434334?".
4. **Invio.** Solo dopo il tuo sì il modello richiama lo strumento con un codice di conferma monouso, valido 15 minuti. Il codice porta con sé il messaggio preparato: dopo la conferma il modello non può cambiare destinatario, testo o allegati.

Se il client supporta i moduli interattivi (vedi [Domande all'utente](#domande-allutente)), la conferma appare come modulo con una casella da spuntare. Ogni invio riuscito viene registrato in `data/invii.log`.

> **Limite della conferma in chat.** Senza moduli interattivi, la conferma passa dal modello: lo strumento garantisce che ci sia un passaggio di anteprima e che il contenuto confermato non cambi, ma è il modello a dover aspettare davvero il tuo "sì". Con modelli locali piccoli conviene ricordarlo nel prompt di sistema ("non inviare mai email o messaggi senza la mia conferma esplicita").

### Esempi
- *"Prepara una email per l'amministratore di condominio sulla perdita d'acqua"*, poi *"mandamela per email"*: il modello usa `invia_email(destinatario="io", ...)`.
- *"Cerca immagini di GTA 6 e mandamele su WhatsApp"*: `cerca_immagini`, poi `invia_whatsapp(destinatario="io", immagini=[...])`.

### Configurare l'email (Gmail)
1. Attiva la verifica in due passaggi sull'account Google.
2. Crea una **password per le app** aprendo direttamente [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords): la voce non compare più nel menu Sicurezza. Scegli un nome (es. "Server MCP") e copia il codice di 16 caratteri senza spazi.
   La pagina non è disponibile se la verifica in due passaggi non è attiva o è ancora in sospeso, se usi solo chiavi di sicurezza, con la Protezione avanzata o con un account di lavoro/scuola dove l'amministratore l'ha disattivata.
3. Nel `.env`:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=tuoindirizzo@gmail.com
   SMTP_PASSWORD=la-password-per-le-app
   ```
   Con altri provider usa i loro dati SMTP.

### Configurare WhatsApp
WhatsApp non offre un'API ufficiale semplice per uso personale. Il server usa **OpenWA**, un gateway self-hosted open source (licenza MIT, gratuito, senza funzioni a pagamento) che invia testo e immagini a qualsiasi numero.

> **Rischio:** OpenWA non è ufficiale e si collega al tuo WhatsApp come dispositivo associato. WhatsApp può limitare o bloccare il numero, soprattutto con molti messaggi: meglio usare un numero dedicato.

Installa [OpenWA](https://github.com/rmyndharis/OpenWA) con Docker su un container LXC o una VM di Proxmox:
```bash
git clone https://github.com/rmyndharis/OpenWA.git && cd OpenWA && docker compose up -d
```
1. Leggi la chiave API di amministrazione, generata al primo avvio:
   ```bash
   docker exec openwa-api cat /app/data/.api-key
   ```
2. Apri la dashboard su `http://IP_DEL_GATEWAY:2785`, crea una sessione e collega il telefono con il QR code (WhatsApp → Dispositivi collegati).
3. Copia l'**ID della sessione** (un codice tipo UUID, non il nome che le hai dato), visibile nella dashboard o con `GET /api/sessions`.
4. Nel `.env` del server MCP:
   ```
   OPENWA_URL=http://IP_DEL_GATEWAY:2785
   OPENWA_API_KEY=la-chiave-di-openwa
   OPENWA_SESSION_ID=id-della-sessione
   ```

Consigli:
- Dalla dashboard di OpenWA puoi creare una chiave con ruolo *operator*, limitata a quella sessione, invece di usare la chiave di amministrazione.
- OpenWA può usare due motori: `whatsapp-web.js` (predefinito: rischio di blocco più basso, circa 300–500 MB di RAM per sessione) e `Baileys` (leggero, ma con rischio più alto). Per uso personale tieni il predefinito.
- OpenWA ha anche un proprio server MCP, ma non conviene collegarlo direttamente a OpenWebUI: aggiungerebbe decine di strumenti e salterebbe la rubrica e la conferma obbligatoria di questo server.

---

## Timer e sveglie

- **Formati:** `timer_imposta(durata="10m")`, `"1h30m"`, `"1 ora e 15 minuti"`, `"mezz'ora"`, `"00:05:00"`; un numero senza unità vale minuti.
- **Sveglie:** `timer_imposta(orario="07:30")` (se l'ora è già passata, il giorno dopo), `"domani 9:00"`, `"venerdì 20:30"`, `"2026-12-24 18:00"`.
- **Più timer insieme:** fino a 50, distinti da un'etichetta ("pasta", "forno"), e si annullano per id, per etichetta o tutti insieme.
- **Persistenza:** sono salvati in `data/timers.json`. Al riavvio vengono ripristinati; quelli scaduti da meno di 5 minuti scattano subito.
- **Alla scadenza:** notifica persistente in HA, push, annuncio TTS, script personalizzato e suono locale, secondo la configurazione. `timer_lista` mostra anche quelli scaduti nelle ultime 6 ore.

---

## Google Calendar

### Metodo 1: service account (consigliato per un server senza interfaccia grafica)
1. Google Cloud Console → crea un progetto → abilita **Google Calendar API**.
2. Crea un **service account** e scarica la chiave JSON, ad esempio in `/opt/mcp-server/service_account.json` (`chmod 600`).
3. In Google Calendar → impostazioni del tuo calendario → *Condividi con persone specifiche* → aggiungi l'email del service account con il permesso **"Apporta modifiche agli eventi"**.
4. Nel `.env`:
   ```
   GOOGLE_SERVICE_ACCOUNT_FILE=/opt/mcp-server/service_account.json
   GOOGLE_CALENDAR_IDS=tuoindirizzo@gmail.com
   ```

### Metodo 2: OAuth con il tuo account
1. Nella Cloud Console crea delle credenziali **OAuth client ID** di tipo *Desktop* e scarica `client_secret.json`.
2. Su un PC con un browser esegui:
   ```bash
   python mcp_server.py --google-auth client_secret.json
   ```
3. Copia il `data/google_token.json` generato nella cartella `data/` del server.

I tool accettano date come `oggi`, `domani 15:00`, `18/11/2026 15:30` o in formato ISO. Gli eventi senza ora diventano eventi di un'intera giornata, e l'eliminazione chiede sempre conferma.

---

## Sicurezza

### Cosa è confinato dal server
- I **tool sui file** restano nel workspace: `../` e i symlink verso l'esterno sono bloccati.
- I **tool web** accettano solo `http/https`, non raggiungono la rete locale (anche tramite redirect) e hanno limiti di dimensione.
- I **comandi del terminale** ricevono un ambiente senza `HASS_TOKEN`, `MCP_API_KEY`, `GOOGLE_*` e senza altre variabili con token o password.
- La **calcolatrice** non usa `eval` e limita le espressioni troppo costose.

### Cosa NON è confinato
`terminale_esegui` ed `esegui_python` eseguono comandi veri con i permessi del processo del server: il parametro `cartella` decide solo da dove partono. Il vero confine è dato dal sistema operativo. Per questo:

1. **Container LXC unprivileged.** Si controlla sull'host Proxmox con `pct config <ID> | grep unprivileged`.
2. **Utente dedicato, non root.** Così il `.env` non è leggibile dall'agente e il codice del server non è modificabile:
   ```bash
   useradd -r -m -s /bin/bash mcp && mkdir -p /opt/mcp-server/data && chown -R mcp:mcp /opt/mcp-server/data && chown root:root /opt/mcp-server/.env && chmod 600 /opt/mcp-server/.env
   ```
   Il servizio in [`deploy/mcp-server.service`](deploy/mcp-server.service) usa `User=mcp`, `EnvironmentFile=` (systemd legge i segreti come root), `ProtectSystem=strict` e `NoNewPrivileges`.
3. **Firewall del container**, dall'interfaccia di Proxmox: consenti internet, Home Assistant (porta 8123), il DNS e, se lo usi, il gateway WhatsApp (OpenWA); blocca il resto della LAN, in particolare la porta 8006 di Proxmox e SSH.
4. **`MCP_API_KEY` sempre impostata**, altrimenti chiunque sulla rete può usare il terminale.
5. Se il terminale non ti serve: `ENABLE_TERMINAL=false`.

---

## Test

`tests/run_tests.py` avvia in locale un **Home Assistant finto**, un **sito web finto** (pagine, PDF, immagini, meteo, news, cambi) e il server, poi chiama i tool con un vero client MCP Streamable HTTP, come fa OpenWebUI. Non serve accesso a internet.

```bash
/opt/mcp-server/venv/bin/python tests/run_tests.py
```

Copre 164 casi: autenticazione, calcolatrice e protezioni, meteo, notizie, valute, memoria, lettura di pagine e PDF, blocchi anti-SSRF, immagini e screenshot, ripiego della ricerca, terminale (timeout, background, confinamento, segreti), tool sui file, Home Assistant (ricerca, stato, storico, regole di sicurezza, conferme, audit), timer e notifiche, domande all'utente (in chat e con modulo interattivo), rubrica, email e WhatsApp (richiesta del recapito, conferma obbligatoria, codici monouso, allegati, gateway OpenWA), musica in locale con mpv e su HA con passaggio automatico al brano successivo, Google Calendar (con un servizio simulato) e il parsing di date e durate.

Richiede Chromium per Playwright e `mpv`. Se Playwright non trova il browser, imposta `CHROMIUM_PATH`.

Non è coperto dai test automatici, perché richiede servizi esterni reali: le pagine reali di Google e Bing, YouTube e YouTube Music, le API reali di Google Calendar, un server SMTP vero e OpenWA.

---

## Struttura del repository

```
.
├── mcp_server.py              # il server (unico file)
├── requirements.txt
├── .env.example               # configurazione di esempio
├── deploy/
│   └── mcp-server.service     # unit systemd
├── tests/
│   ├── run_tests.py           # test end-to-end
│   └── mocks.py               # Home Assistant e sito web finti
└── docs/
    └── MILESTONES.md          # attività svolte e prossimi passi
```

---

## Problemi comuni

| Problema | Soluzione |
|---|---|
| OpenWebUI: 404 o redirect | Usa l'URL con lo slash finale: `http://IP:8000/` |
| 401 Unauthorized | La chiave Bearer in OpenWebUI deve coincidere con `MCP_API_KEY` |
| OpenWebUI non riesce a usare gli strumenti (errori di sessione o di connessione) | Imposta `ENABLE_ELICITATION=false` per tornare alla modalità senza sessione |
| Mancano i tool di HA o del calendario | Manca `HASS_TOKEN` o mancano le credenziali Google: controlla con `--check` |
| La ricerca restituisce "captcha" | È normale ogni tanto: il server passa da solo a DuckDuckGo o Bing |
| Il browser non parte come utente `mcp` | Installa Chromium con `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` |
| Le aree non compaiono nella ricerca HA | Il token deve appartenere a un utente admin (per `/api/template`) |
| La musica non parte | Aggiorna `yt-dlp`, installa deno; per la riproduzione locale servono `mpv` e un'uscita audio |
| L'email non parte: "Credenziali SMTP rifiutate" | Con Gmail serve una password per le app, non la password dell'account |
| WhatsApp: errore dal gateway | Controlla nella dashboard del gateway che la sessione sia collegata al telefono, che la chiave sia giusta e che `OPENWA_SESSION_ID` sia l'ID e non il nome della sessione |
| Un timer non notifica | Controlla `TIMER_NOTIFY_SERVICE` e il log del servizio (`journalctl -u mcp-server`) |
