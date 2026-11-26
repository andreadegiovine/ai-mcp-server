# AI MCP Server

[![Test](https://github.com/andreadegiovine/ai-mcp-server/actions/workflows/test.yml/badge.svg)](https://github.com/andreadegiovine/ai-mcp-server/actions/workflows/test.yml)

Server **MCP (Model Context Protocol)** in Python che dà a un LLM locale su llama.cpp una serie di strumenti reali: ricerca e lettura del web con immagini, un terminale con workspace dedicato, lettura e controllo sicuro di **Home Assistant**, musica da YouTube in stile Alexa, timer e sveglie multipli, e **Google Calendar**. L'agente si usa a voce tramite gli Echo e il [bridge vocale](#echo-bridge-vocale), oppure in chat con **OpenWebUI**. Home Assistant è solo uno dei sistemi su cui l'agente agisce con i suoi strumenti: voce, timer e musica passano dal bridge, non da HA.

Il punto di ingresso è `mcp_server.py` (moduli in `nucleo/` e `tools/`), pensato per girare in un container LXC su Proxmox dentro un virtualenv.

> Lo stato di avanzamento e le attività svolte sono in [`docs/MILESTONES.md`](docs/MILESTONES.md).

---

## Indice

- [Strumenti disponibili](#strumenti-disponibili)
- [Come funziona](#come-funziona)
- [Installazione (Proxmox LXC + venv)](#installazione-proxmox-lxc--venv)
- [Aggiornamento](#aggiornamento)
- [Configurazione (.env)](#configurazione-env)
- [Variabili fisse](#variabili-fisse)
- [Collegamento a OpenWebUI](#collegamento-a-openwebui)
- [Home Assistant](#home-assistant)
- [Musica](#musica)
- [Domande all'utente](#domande-allutente)
- [Email e WhatsApp](#email-e-whatsapp)
- [Cartelle per conversazione](#cartelle-per-conversazione)
- [Leggere documenti](#leggere-documenti)
- [Mostrare i file nella chat](#mostrare-i-file-nella-chat)
- [Ambienti di sviluppo](#ambienti-di-sviluppo)
- [GitHub](#github)
- [Timer e sveglie](#timer-e-sveglie)
- [Echo (bridge vocale)](#echo-bridge-vocale)
- [Google Calendar](#google-calendar)
- [Sicurezza](#sicurezza)
- [Test](#test)
- [Struttura del repository](#struttura-del-repository)
- [Problemi comuni](#problemi-comuni)

---

## Strumenti disponibili

Il server espone **53 tool**, divisi in gruppi. Un gruppo non configurato viene nascosto da solo: i tool di Home Assistant compaiono solo se c'è `HASS_TOKEN`, quelli del calendario solo se ci sono le credenziali Google, email, WhatsApp e GitHub solo se configurati. Puoi nasconderne altri con `DISABLED_TOOLS`.

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
| `ambiente_installa` | Installa Node.js (con npm), Python (con uv) o Go nella home persistente dell'agente, senza root (vedi [Ambienti di sviluppo](#ambienti-di-sviluppo)) |
| `file_leggi` | Legge file di testo e codice con numeri di riga, ed estrae il testo dai documenti: PDF (anche scansionati, con OCR), Word, Excel, PowerPoint, LibreOffice, RTF, EPUB, email, immagini e archivi (vedi [Leggere documenti](#leggere-documenti)) |
| `file_scrivi` / `file_modifica` | Scrittura e modifica tramite sostituzione esatta di testo |
| `link_file` | Link per mostrare nella chat o scaricare un file del workspace (vedi [Mostrare i file nella chat](#mostrare-i-file-nella-chat)) |
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
| `timer_imposta` / `timer_lista` / `timer_annulla` | Timer multipli con etichetta e sveglie a orario, anche ricorrenti (`giorni`), che suonano sull'Echo; restano attivi dopo un riavvio. Richiedono il bridge |
| `musica_riproduci` | Riproduce da YouTube sull'Echo e poi continua con brani simili. Richiede il bridge |
| `musica_controlla` | Pausa, riprendi, stop, successivo, precedente, volume, attiva o disattiva la radio, spegnimento programmato |
| `musica_aggiungi_in_coda` / `musica_stato` | Aggiunge un brano come prossimo; mostra cosa sta suonando e la coda |
| `calendario_eventi` / `calendario_crea_evento` / `calendario_modifica_evento` / `calendario_elimina_evento` / `calendario_lista` | Google Calendar completo |

### GitHub
| Tool | Cosa fa |
|---|---|
| `github_clona` | Clona nel workspace un repository autorizzato |
| `github_stato` | Ramo, file modificati, ultimi commit e commit da inviare |
| `github_commit` | Crea un commit locale (tutte le modifiche o solo i file indicati) |
| `github_push` | Invia i commit a GitHub, **sempre con conferma** |
| `github_crea_pull_request` | Apre una pull request, **sempre con conferma** |

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
- **Ciclo di vita:** all'avvio vengono ripristinati i timer e parte il monitor della musica. Allo spegnimento si chiudono browser e processi in background.
- **Endpoint `/health`:** risponde senza autenticazione con lo stato, la versione (commit) e il numero di tool.
- **Manutenzione automatica:** un minuto dopo l'avvio, e poi ogni 24 ore, il server cancella i file vecchi (log dei processi, screenshot, cache dell'agente) e scrive nel log quanto spazio occupa ogni cartella dei dati. I registri `invii.log` e `hass_audit.log` vengono ruotati oltre 5 MB. Lo spazio occupato si vede anche con `--check`.

### Cartella dati (`DATA_DIR`, default `./data`)
```
data/
├── workspace/            # unica area di lavoro di terminale e tool sui file
│   └── <id-chat>/        # una sottocartella per conversazione (vedi Cartelle per conversazione)
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
apt update && apt install -y python3-venv git bubblewrap
```

Facoltativi, per leggere PDF scansionati e immagini (OCR) e i vecchi formati Office `.doc`/`.ppt` (vedi [Leggere documenti](#leggere-documenti)):

```bash
apt install -y --no-install-recommends tesseract-ocr tesseract-ocr-ita libreoffice-writer-nogui libreoffice-impress-nogui libreoffice-calc-nogui
```

```bash
git clone https://github.com/andreadegiovine/ai-mcp-server /opt/mcp-server
```

```bash
python3 -m venv /opt/mcp-server/venv
```

```bash
/opt/mcp-server/venv/bin/pip install -r /opt/mcp-server/requirements.lock
```

Il browser per Playwright va installato in una cartella condivisa, così può usarlo anche l'utente non-root del servizio:

```bash
PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright /opt/mcp-server/venv/bin/playwright install --with-deps chromium
```

Per YouTube, yt-dlp richiede un runtime JavaScript (deno), da installare per l'utente che esegue il servizio. Dopo aver creato l'utente `mcp` (vedi [Sicurezza](#sicurezza)):

```bash
su - mcp -c "curl -fsSL https://deno.land/install.sh | sh"
```

Impostazioni generali (il `.env` contiene solo valori predefiniti e non va modificato):

```bash
cp /opt/mcp-server/.env.example /opt/mcp-server/.env
```

Le tue variabili (token, password, indirizzi, preferenze) vanno nel file delle variabili fisse, fuori dal repository: segui i passi 1 e 2 di [Variabili fisse](#variabili-fisse).

Prova manuale. Il comando mostra quali gruppi e tool sono attivi con le impostazioni del `.env` (non legge le variabili fisse, che carica systemd: per la configurazione completa guarda la riga `Tool attivi` nel log del servizio):

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

> La configurazione viene letta **solo all'avvio**: dopo ogni modifica esegui `systemctl restart mcp-server`.
> Per aggiornare il codice vedi [Aggiornamento](#aggiornamento).

---

## Aggiornamento

Comandi da eseguire come root nel container. La cartella `data/` e il file delle [variabili fisse](#variabili-fisse) sono fuori da git, quindi l'aggiornamento non tocca configurazione personale, memoria, timer e token.

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
/opt/mcp-server/venv/bin/pip install -r /opt/mcp-server/requirements.lock && /opt/mcp-server/venv/bin/pip install -U yt-dlp
```

`requirements.lock` contiene le versioni esatte provate con la suite di test: l'aggiornamento installa sempre una combinazione verificata. Solo yt-dlp va sempre all'ultima versione, perché YouTube cambia spesso.

**3. Aggiorna il browser**

Serve solo se nel passo 2 si è aggiornato Playwright, perché ogni versione usa la sua build di Chromium. Rilanciarlo comunque non fa danni:

```bash
PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright /opt/mcp-server/venv/bin/playwright install chromium
```

**4. Aggiorna le impostazioni generali e il servizio**

Il nuovo `.env.example` sostituisce il `.env`: così arrivano anche le variabili nuove, con il loro valore predefinito. Le tue variabili stanno nel file delle [variabili fisse](#variabili-fisse) e hanno la precedenza, quindi non si perdono.

```bash
cp /opt/mcp-server/.env.example /opt/mcp-server/.env
```

Copia anche il file del servizio, nel caso sia cambiato. Il file delle variabili fisse è fuori dal repository e resta intatto:

```bash
cp /opt/mcp-server/deploy/mcp-server.service /etc/systemd/system/ && systemctl daemon-reload
```

Se avevi modificato a mano `/etc/systemd/system/mcp-server.service`, sposta prima le tue modifiche in un drop-in (`systemctl edit mcp-server`), altrimenti vengono sovrascritte.

**5. Riavvia il servizio e controlla**

```bash
systemctl restart mcp-server && systemctl status mcp-server --no-pager
```

```bash
journalctl -u mcp-server -n 30 --no-pager
```

Nel log, la riga `Server MCP versione main@1a2b3c4` indica il commit in esecuzione (lo stesso di `git log --oneline -1`) e `Tool attivi (...)` elenca i tool caricati. La versione si vede anche su `http://IP:8000/health`.

**Tutto in un solo comando** (passi da 1 a 5):

```bash
cd /opt/mcp-server && git pull && venv/bin/pip install -r requirements.lock && venv/bin/pip install -U yt-dlp && PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright venv/bin/playwright install chromium && cp .env.example .env && cp deploy/mcp-server.service /etc/systemd/system/ && systemctl daemon-reload && systemctl restart mcp-server && journalctl -u mcp-server -n 30 --no-pager
```

> Usa questo comando solo dopo aver configurato le [variabili fisse](#variabili-fisse): altrimenti la copia di `.env.example` cancella le tue impostazioni dal `.env`.

**Aggiornare le dipendenze** (per chi sviluppa): modifica `requirements.txt` se serve, poi rigenera il lock in un ambiente pulito, esegui i test e pubblica entrambi i file:

```bash
python3 -m venv /tmp/lock && /tmp/lock/bin/pip install -r requirements.txt && /tmp/lock/bin/pip freeze > requirements.lock
```

Ricorda di rimettere in cima al lock le righe di commento.

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

La configurazione è su due livelli:

1. **Variabili fisse**, in `/etc/mcp-server/mcp-server.env`: segreti, indirizzi e preferenze personali. Le carica systemd e hanno sempre la precedenza. Vedi [Variabili fisse](#variabili-fisse).
2. **Impostazioni generali**, nel `.env` accanto a `mcp_server.py`: è una copia di [`.env.example`](.env.example) con i valori predefiniti, che viene sostituita a ogni aggiornamento. Non va modificata.

Il server legge il `.env`, ma non sovrascrive le variabili già impostate dal servizio. Le tabelle qui sotto elencano tutte le variabili: per cambiarne una, aggiungila al file delle variabili fisse.

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

### Echo (bridge vocale)
| Variabile | Default | Descrizione |
|---|---|---|
| `ECHO_BRIDGE_URL` | – | Indirizzo del bridge, es. `http://192.168.178.30:8124`. Vuoto = timer, sveglie e musica disattivati |
| `ECHO_BRIDGE_TOKEN` | – | `API_TOKEN` del bridge, se impostato (segreto: nelle variabili fisse) |
| `ECHO_BRIDGE_ECHO` | – | Echo predefinito; vuoto = quello che ha ricevuto l'ultima richiesta vocale |
| `ECHO_NOTIFY_IF_NOT_PLAYED` | `true` | Se un annuncio non viene detto (es. "non disturbare"), lo lascia come notifica (anello giallo) |

### Musica
| Variabile | Default | Descrizione |
|---|---|---|
| `MUSIC_START_TIMEOUT` | `45` | Secondi dopo i quali un brano che non parte viene saltato |

### Terminale
| Variabile | Default | Descrizione |
|---|---|---|
| `TERMINAL_MAX_OUTPUT` | `12000` | Caratteri massimi di output restituiti |
| `TERMINAL_MAX_TIMEOUT` | `900` | Timeout massimo di un comando, in secondi |
| `TERMINAL_SANDBOX` | `auto` | Sandbox bubblewrap per terminale ed `esegui_python`: `auto` (usala se disponibile), `obbligatoria` (senza sandbox il terminale non funziona), `disattivata`. Vedi [Sicurezza](#sicurezza) |
| `DOWNLOAD_MAX_MB` | `200` | Dimensione massima di `scarica_file` |

### Manutenzione
| Variabile | Default | Descrizione |
|---|---|---|
| `REGISTRI_MAX_MB` / `REGISTRI_COPIE` | `5` / `3` | Rotazione di `invii.log` e `hass_audit.log`: dimensione massima e copie tenute |
| `PULIZIA_GIORNI_PROCESSI` | `7` | Dopo quanti giorni cancellare i log dei processi in background |
| `PULIZIA_GIORNI_SCREENSHOT` | `30` | Dopo quanti giorni cancellare gli screenshot (come la validità dei link) |
| `PULIZIA_GIORNI_CACHE` | `30` | Dopo quanti giorni cancellare i file della cache nella home dell'agente |

Valori `0` disattivano la relativa pulizia.
| `OCR_LINGUE` | `ita+eng` | Lingue dell'OCR (pacchetti `tesseract-ocr-<lingua>`) |
| `OCR_MAX_PAGINE` | `30` | Pagine massime riconosciute con l'OCR per ogni PDF scansionato |

### Email (SMTP)
| Variabile | Default | Descrizione |
|---|---|---|
| `SMTP_HOST` | – | Server SMTP (Gmail: `smtp.gmail.com`). Senza, lo strumento email è nascosto |
| `SMTP_PORT` | `587` | `587` con STARTTLS, `465` con SSL |
| `SMTP_USER` / `SMTP_PASSWORD` | – | Credenziali (Gmail: indirizzo e *password per le app*) |
| `SMTP_NOME` | `Assistente` | Nome del mittente mostrato a chi riceve (es. `Assistente di casa`) |
| `SMTP_FROM` | `SMTP_USER` | Indirizzo email del mittente. Con Gmail deve essere l'account stesso o un alias verificato (vedi [Configurare l'email](#configurare-lemail-gmail)) |
| `SMTP_STARTTLS` | `true` | STARTTLS sulla porta 587 |
| `ALLEGATI_MAX_MB` | `20` | Dimensione massima totale degli allegati (vale anche per le immagini WhatsApp) |

### WhatsApp
| Variabile | Default | Descrizione |
|---|---|---|
| `OPENWA_URL` / `OPENWA_API_KEY` / `OPENWA_SESSION_ID` | – | Indirizzo, chiave e ID della sessione del gateway OpenWA. Senza URL e ID della sessione, lo strumento WhatsApp è nascosto |
| `WHATSAPP_PREFISSO` | `39` | Prefisso aggiunto ai numeri scritti senza `+` |

### Link ai file e GitHub
| Variabile | Default | Descrizione |
|---|---|---|
| `MCP_URL_PUBBLICO` | indirizzo della richiesta | Facoltativo. Indirizzo del server nei link ai file: di norma è quello con cui il client raggiunge il server; impostalo solo se il browser non raggiunge quell'indirizzo (es. `http://192.168.1.11:8000`) |
| `LINK_FILE_GIORNI` | `30` | Validità dei link ai file |
| `LINK_FILE_SEGRETO` | casuale | Chiave di firma dei link; se non impostata viene generata e salvata in `data/.chiave_link_file` |
| `GITHUB_TOKEN` | – | Token GitHub (fine-grained, limitato ai repository autorizzati). Senza, gli strumenti GitHub sono nascosti |
| `GITHUB_REPO_AUTORIZZATI` | – | Repository utilizzabili, separati da virgola: `utente/progetto`, anche con `*` (`utente/*`) |
| `GITHUB_AUTORE_NOME` / `GITHUB_AUTORE_EMAIL` | `Assistente MCP` / `assistente-mcp@users.noreply.github.com` | Autore dei commit |

### Google Calendar
| Variabile | Descrizione |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_FILE` | JSON del service account (metodo consigliato) |
| `GOOGLE_OAUTH_TOKEN_FILE` | Token OAuth (default `data/google_token.json`) |
| `GOOGLE_CALENDAR_IDS` | Uno o più ID di calendario; il primo è quello predefinito |
| `GOOGLE_DELEGATED_USER` | Solo per Google Workspace con delega a livello di dominio |

---

## Variabili fisse

Le variabili che personalizzi stanno in `/etc/mcp-server/mcp-server.env`, fuori dal repository. Il servizio [`deploy/mcp-server.service`](deploy/mcp-server.service) lo carica già con `EnvironmentFile=-/etc/mcp-server/mcp-server.env` (il `-` lo rende facoltativo): non serve configurare altro in systemd, e gli aggiornamenti non lo toccano.

**1. File con le variabili fisse**

```bash
mkdir -p /etc/mcp-server && chmod 700 /etc/mcp-server
```

```bash
nano /etc/mcp-server/mcp-server.env
```

Mettici **tutte** le variabili che hai personalizzato, non solo i segreti. Il formato è lo stesso del `.env` (una variabile per riga, commenti solo a inizio riga), per esempio:

```
MCP_API_KEY=una-chiave-lunga-e-casuale
HASS_URL=http://192.168.178.48:8123
HASS_TOKEN=il-tuo-long-lived-token
METEO_DEFAULT_LOCALITA=Priolo Gargallo
ECHO_BRIDGE_URL=http://192.168.178.30:8124
ECHO_BRIDGE_TOKEN=api-token-del-bridge
SMTP_HOST=smtp.gmail.com
SMTP_USER=tuoindirizzo@gmail.com
SMTP_PASSWORD=passwordperleapp
SMTP_NOME=Assistente di casa
```

Se hai già un `.env` personalizzato, puoi partire da quello: `cp /opt/mcp-server/.env /etc/mcp-server/mcp-server.env`, poi togli le righe lasciate al valore predefinito.

Rendilo leggibile solo da root. systemd lo legge come root prima di avviare il servizio con l'utente `mcp`, quindi il server riceve i valori ma l'agente, dal terminale, non può leggere il file:

```bash
chmod 600 /etc/mcp-server/mcp-server.env
```

**2. Come vengono caricate**

Il servizio carica prima il `.env` del repository e poi il file delle variabili fisse. Per systemd l'ultimo file vince: per le variabili presenti in entrambi, conta quella del file delle variabili fisse.

Se avevi creato il drop-in `/etc/systemd/system/mcp-server.service.d/override.conf` solo per caricare questo file, ora non serve più. Non fa danni (il file viene letto due volte), ma puoi toglierlo:

```bash
rm /etc/systemd/system/mcp-server.service.d/override.conf && systemctl daemon-reload
```

**3. Aggiornamento**

Da qui in poi aggiorna con il comando completo della sezione [Aggiornamento](#aggiornamento), che copia anche il nuovo `.env.example` sopra il `.env`.

**4. Verifica**

Controlla che il processo abbia ricevuto le variabili giuste. Il comando mostra solo variabili non segrete:

```bash
tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value mcp-server)/environ | grep -E "^(HASS_URL|SMTP_HOST|SMTP_USER|SMTP_NOME|OPENWA_URL|METEO|ECHO_BRIDGE_URL)="
```

In alternativa, la riga `Tool attivi (...)` nel log mostra se gli strumenti di Home Assistant, email e WhatsApp sono stati attivati.

Due attenzioni:
- **Non mettere `PATH` né `PLAYWRIGHT_BROWSERS_PATH` nel file.** Li imposta già il servizio, e una variabile in `EnvironmentFile` avrebbe la precedenza. Lo stesso vale per `DATA_DIR`, a meno che tu non voglia davvero spostare i dati: in quel caso aggiorna anche `ReadWritePaths` nel servizio.
- **Le variabili nuove delle prossime versioni** arrivano con il loro valore predefinito dal nuovo `.env`. Se vuoi cambiarne una, aggiungila a `/etc/mcp-server/mcp-server.env`, non al `.env`.

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
3. **Riproduzione sull'Echo:** il server ricava con yt-dlp l'URL diretto dell'audio e le intestazioni HTTP e li manda a `POST /api/music` del bridge, che lo suona sull'Echo. Parametro `echo`: vuoto per l'Echo che ha ricevuto la richiesta, oppure il nome dell'Echo ("cucina"). Vedi [Echo (bridge vocale)](#echo-bridge-vocale).
4. **Continuità stile Alexa:** un monitor controlla lo stato ogni 2 secondi. Quando un brano finisce passa al successivo; se un brano non parte entro `MUSIC_START_TIMEOUT` lo salta (al massimo 3 volte di fila); se sull'Echo parte altro, interrompe la coda.

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
3. Nelle [variabili fisse](#variabili-fisse):
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=tuoindirizzo@gmail.com
   SMTP_PASSWORD=la-password-per-le-app
   ```
   Con altri provider usa i loro dati SMTP.
4. Facoltativo, il mittente:
   ```
   SMTP_NOME=Assistente di casa
   SMTP_FROM=tuoindirizzo@gmail.com
   ```
   `SMTP_NOME` è il nome che vede chi riceve la mail. Se usi un indirizzo diverso da `SMTP_USER` in `SMTP_FROM`, Gmail lo accetta solo se è un alias verificato (Gmail → Impostazioni → Account → "Invia messaggio come"); altrimenti lo sostituisce con l'indirizzo dell'account.

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
4. Nelle [variabili fisse](#variabili-fisse) del server MCP:
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

## Cartelle per conversazione

Tutti gli strumenti che usano il workspace accettano un ID di conversazione facoltativo. Se c'è, lavorano in `data/workspace/<id>/`, che viene creata se non esiste. Così i file di una chat non si mescolano con quelli delle altre. Gli strumenti interessati sono `terminale_esegui`, `terminale_processi`, `esegui_python`, `file_*`, `scarica_file`, `screenshot_pagina`, `vedi_immagine` e, per gli allegati locali, `invia_email` e `invia_whatsapp`.

- I percorsi relativi partono dalla cartella della conversazione, e non si può uscirne: `../` e i symlink verso l'esterno vengono rifiutati.
- Il terminale parte da quella cartella, e la variabile `$WORKSPACE` punta lì.
- Senza ID, gli strumenti lavorano nella radice del workspace, come prima.

L'ID lo passa il modello nel parametro `id_conversazione`, e deve usare lo stesso valore per tutta la conversazione.


---

## Leggere documenti

`file_leggi` riconosce il tipo di file (dall'estensione e dal contenuto) e ne estrae il testo, che poi si legge a pezzi con `da_riga` e `num_righe` come un normale file di testo. Per i documenti scaricati da internet: prima `scarica_file`, poi `file_leggi`.

| Formato | Cosa restituisce | Requisiti |
|---|---|---|
| PDF | Testo pagina per pagina | `pypdf` (incluso) |
| PDF scansionato | Testo riconosciuto con l'OCR (fino a `OCR_MAX_PAGINE` pagine) | `tesseract-ocr`, `tesseract-ocr-ita` |
| Word `.docx` | Titoli (`#`), elenchi, tabelle (`colonna \| colonna`) | `python-docx` (incluso) |
| Excel `.xlsx`, `.xls` | Un blocco per foglio, una riga per riga, con date e numeri leggibili | `openpyxl`, `xlrd` (inclusi) |
| PowerPoint `.pptx` | Testo slide per slide, tabelle e note del relatore | `python-pptx` (incluso) |
| LibreOffice `.odt`, `.ods`, `.odp` | Come Word, Excel e PowerPoint | nessuno |
| Vecchi Office `.doc`, `.ppt` | Convertiti con LibreOffice, poi letti come `.docx`/`.pptx` | `libreoffice-writer-nogui`, `libreoffice-impress-nogui` |
| `.xls` esportati dai gestionali (in realtà HTML) | Convertiti con LibreOffice | `libreoffice-calc-nogui` |
| RTF, EPUB, email `.eml` | Testo; per le email anche intestazioni ed elenco degli allegati | `striprtf` (incluso) |
| Immagini (`.png`, `.jpg`, ...) | Testo riconosciuto con l'OCR; per guardarle c'è `vedi_immagine` | `tesseract-ocr` |
| Archivi `.zip`, `.tar.gz`, ... | Elenco dei file contenuti (per estrarli: `terminale_esegui`) | nessuno |

File di testo, codice, `.csv`, `.json` e `.html` vengono letti così come sono, per poterli modificare con `file_modifica`.

Se manca un programma facoltativo, lo strumento non va in errore: risponde con il comando `apt` da eseguire sul server. I PDF protetti da password e i file danneggiati danno un messaggio chiaro. LibreOffice viene usato in modalità senza interfaccia, con un profilo dedicato in `data/.libreoffice`, una conversione alla volta.

---

## Mostrare i file nella chat

Un client come OpenWebUI mostra le immagini nel browser dell'utente, quindi non può aprire un percorso del server (`screenshot/pagina.png`). Il server pubblica allora i file del workspace con **link firmati**:

```
http://IP_DEL_SERVER:8000/file/<token>/nome.png
```

- Il token contiene il percorso e la scadenza (`LINK_FILE_GIORNI`, 30 giorni) ed è firmato: non si può modificare per leggere altri file, e funziona solo per file del workspace.
- I link non richiedono la chiave API, perché un tag `<img>` non può inviarla. Tutte le altre rotte la richiedono ancora.
- `screenshot_pagina`, `vedi_immagine` (per i file locali) e `scarica_file` restituiscono già il markdown pronto (`![...](link)` per le immagini, `[...](link)` per gli altri file). Per qualsiasi altro file, per esempio un grafico creato con `esegui_python`, l'agente usa `link_file`.
- Le immagini si aprono nel browser, gli altri file vengono scaricati.

Non serve configurare nulla: per i link il server usa l'indirizzo con cui il client lo ha raggiunto (quello inserito in OpenWebUI, es. `http://192.168.1.11:8000/`). Il browser deve poter raggiungere lo stesso indirizzo, e in una rete locale di solito è così.

Se OpenWebUI raggiunge il server con un indirizzo che il browser non conosce, per esempio un nome interno di Docker come `http://mcp-server:8000`, indica nelle [variabili fisse](#variabili-fisse) l'indirizzo per il browser:

```
MCP_URL_PUBBLICO=http://192.168.1.11:8000
```

---

## Ambienti di sviluppo

Nella sandbox l'agente non ha permessi di amministratore: `apt` e `sudo` non funzionano, ed è voluto. Per eseguire codice e test può installare gli strumenti **nella propria home persistente**:

- nella sandbox la home è `/home/agente`, sul server `DATA_DIR/home_agente`. Resta tra un comando e l'altro e tra le conversazioni, ed è separata dal workspace;
- `~/.local/bin`, `~/go/bin` e `~/.cargo/bin` sono nel `PATH`; `npm install -g` installa in `~/.local`;
- lo strumento **`ambiente_installa`** scarica le versioni ufficiali di:
  - **Node.js** con npm (`versione`: `lts`, `22`, `20.11.1`, `latest`), da nodejs.org;
  - **Python** tramite [uv](https://github.com/astral-sh/uv) (`3.12`, `3.13`...), poi `uv venv -p 3.12 .venv` per un ambiente isolato;
  - **Go** (`1.23`, `latest`), da go.dev.

Esempio: *"clona il repository, installa Node.js 22 ed esegui i test"*. L'agente usa `ambiente_installa(strumento="node", versione="22")`, poi `npm ci && npm test` nel terminale.

Le librerie di sistema (pacchetti `apt` come `libpq-dev` o `ffmpeg`) restano fuori dalla portata dell'agente: se servono, vanno installate dall'amministratore nel container. Il server deve poter raggiungere nodejs.org, go.dev e github.com (per uv e le versioni di Python).

---

## GitHub

L'agente può clonare un repository, modificarlo, fare commit e push, e aprire pull request, **solo sui repository autorizzati**.

### Configurazione
1. Crea su GitHub un token **fine-grained** (Settings → Developer settings → Personal access tokens → Fine-grained tokens), limitato ai soli repository che vuoi far usare all'agente, con i permessi **Contents: Read and write** e, per le pull request, **Pull requests: Read and write**.
2. Nelle [variabili fisse](#variabili-fisse):
   ```
   GITHUB_TOKEN=github_pat_...
   GITHUB_REPO_AUTORIZZATI=tuoutente/progetto,tuoutente/altro-*
   GITHUB_AUTORE_NOME=Assistente di casa
   ```
   Serve anche `git` sul server (già incluso nei pacchetti di installazione).

### Flusso
1. `github_clona(repo="tuoutente/progetto")`: il repository finisce nel workspace (o nella cartella della conversazione).
2. L'agente modifica i file con `file_scrivi`, `file_modifica` o il terminale.
3. `github_commit(cartella="progetto", messaggio="...")`: commit locale, nella sandbox.
4. `github_push(cartella="progetto")`: chiede "Faccio il push di 2 commit sul ramo main del repository tuoutente/progetto?". Solo dopo il tuo sì invia i commit, sul ramo corrente o su un ramo nuovo.
5. Facoltativo: `github_crea_pull_request(repo=..., ramo=..., titolo=...)`, anche questo con conferma.

### Sicurezza
- **Il token resta nel server.** Non viene salvato nel repository né passato ai comandi dell'agente: il server lo usa solo nella riga di comando di clone e push e nelle chiamate alle API.
- **Commit e comandi locali** girano nella sandbox del terminale, senza token.
- **Controllo della configurazione git.** L'agente può modificarla, quindi prima di ogni push il server la verifica: se trova impostazioni che eseguono programmi o reindirizzano le connessioni (`core.fsmonitor`, `http.proxy`, hook, filtri, `include`...), oppure un remote verso un repository non autorizzato, il push viene bloccato.
- **Conferma legata al commit.** Il codice di conferma vale per il commit preparato: se il repository cambia prima della conferma, il push viene annullato.
- **Niente push forzato:** non è previsto. Push e pull request finiscono nel registro `data/invii.log`.

---

## Timer e sveglie

- **Formati:** `timer_imposta(durata="10m")`, `"1h30m"`, `"1 ora e 15 minuti"`, `"mezz'ora"`, `"00:05:00"`; un numero senza unità vale minuti.
- **Sveglie:** `timer_imposta(orario="07:30")` (se l'ora è già passata, il giorno dopo), `"domani 9:00"`, `"venerdì 20:30"`, `"2026-12-24 18:00"`.
- **Più timer insieme:** fino a 50, distinti da un'etichetta ("pasta", "forno"), e si annullano per id, per etichetta o tutti insieme.
- **Persistenza:** sono salvati in `data/timers.json`. Al riavvio vengono ripristinati; quelli scaduti da meno di 5 minuti scattano subito.
- **Sull'Echo:** ogni timer e sveglia viene registrato sull'Echo, che mostra il conto alla rovescia sull'anello e suona con la sua suoneria finché non dici "Alexa, stop". Se l'Echo non era raggiungibile alla creazione, alla scadenza il timer viene annunciato a voce. `timer_lista` mostra anche quelli scaduti nelle ultime 6 ore.
- **Sveglie ricorrenti:** `giorni="feriali"`, `"weekend"`, `"tutti"` o `"lun,mer,ven"`; vivono solo sull'Echo.
- **Senza bridge** (`ECHO_BRIDGE_URL` vuoto) gli strumenti dei timer non vengono registrati, perché non avrebbero modo di avvisare.

---

## Echo (bridge vocale)

Con [ai-echo-dot-bridge](https://github.com/andreadegiovine/ai-echo-dot-bridge) gli Echo diventano la voce dell'agente su llama.cpp, e sono anche l'unico canale per timer, sveglie e musica. Basta impostare nelle [variabili fisse](#variabili-fisse) `ECHO_BRIDGE_URL` (e `ECHO_BRIDGE_TOKEN` se nel bridge è impostato `API_TOKEN`) e riavviare: `python mcp_server.py --check` mostra gli Echo trovati.

| Servizio | Cosa usa del bridge | Comportamento |
|---|---|---|
| Timer | `POST /api/timers` (`start`, `cancel`) | Il timer parte sull'Echo: anello con il tempo che resta e suoneria nativa |
| Sveglie | `POST /api/alarms` (`set`, `cancel`, `list`) | Sveglia sull'Echo all'ora e data indicate; con `giorni` diventa ricorrente |
| Timer non registrati | `POST /api/announce` | Se l'Echo non era raggiungibile quando è stato creato il timer, alla scadenza viene annunciato a voce |
| Musica | `POST /api/music` (`play`, `pause`, `resume`, `stop`, `status`, `sleep`) | La coda resta del server: quando il bridge passa a `idle` parte il brano successivo, sullo stesso Echo |
| Volume della musica | strumento MCP `set_volume` del bridge, volume letto da `GET /api/echos` | `musica_controlla(azione="volume")` |
| Spegnimento programmato | `POST /api/music` con `sleep` | `musica_controlla(azione="spegni_tra", valore=30)`: si ferma tra 30 minuti abbassandosi piano |
| Processi lunghi | `POST /api/announce` | `terminale_esegui(in_background=true, avvisa_al_termine=true)`: alla fine l'Echo dice com'è andata |

- **Quale Echo:** il campo `echo` vuoto indica al bridge l'Echo che ha ricevuto l'ultima richiesta vocale. Il server ricorda il nome restituito dal bridge, così i comandi successivi (pausa, annullamento di un timer) vanno allo stesso Echo anche se poi parli con un altro.
- **"Non disturbare":** il bridge scarta gli annunci; il server li lascia allora come notifica (anello giallo), che si ascolta più tardi. Timer e sveglie suonano comunque.
- **URL di YouTube:** valgono solo per l'IP pubblico che li ha ricavati: server e bridge devono uscire su internet dalla stessa connessione.
- **Rete:** se usi il [firewall del container](#firewall-del-container), consenti l'uscita verso l'IP del bridge sulla porta 8124.

---

## Google Calendar

### Metodo 1: service account (consigliato per un server senza interfaccia grafica)
1. Google Cloud Console → crea un progetto → abilita **Google Calendar API**.
2. Crea un **service account** e scarica la chiave JSON, ad esempio in `/opt/mcp-server/service_account.json` (`chmod 600`).
3. In Google Calendar → impostazioni del tuo calendario → *Condividi con persone specifiche* → aggiungi l'email del service account con il permesso **"Apporta modifiche agli eventi"**.
4. Nelle [variabili fisse](#variabili-fisse):
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
- I **tool web** accettano solo `http/https`, non raggiungono la rete locale (anche tramite redirect) e hanno limiti di dimensione. Il server si collega all'IP già verificato, quindi un DNS che cambia risposta tra il controllo e la connessione (*DNS rebinding*) non porta in rete locale.
- I **comandi del terminale** girano in una sandbox (vedi sotto) e ricevono un ambiente senza `HASS_TOKEN`, `MCP_API_KEY`, `GOOGLE_*` e senza altre variabili con token o password.
- La **calcolatrice** non usa `eval` e limita le espressioni troppo costose.

### Il terminale gira in una sandbox
`terminale_esegui`, `esegui_python` e i processi in background eseguono comandi veri. Per impedire che l'agente esca dal workspace, ogni comando gira in una sandbox [bubblewrap](https://github.com/containers/bubblewrap), dove esistono solo:
- la **cartella di lavoro**, in lettura e scrittura: il workspace oppure, con `id_conversazione`, solo la cartella della conversazione;
- **in sola lettura**: i programmi di sistema (`/usr`), `/etc` (senza `/etc/mcp-server`) e l'interprete Python del server con le sue librerie;
- una `/tmp` e una home temporanee, cancellate a fine comando.

Il resto del server non esiste per i comandi: `/mnt`, `/opt` (codice, `.env`, venv esclusi i file Python), `data/` fuori dal workspace, le altre cartelle delle conversazioni. La rete resta disponibile per `git clone`, `pip` e `curl`.

Verifica che la sandbox funzioni nel container, con lo stesso utente del servizio:

```bash
runuser -u mcp -- bwrap --unshare-all --share-net --ro-bind / / --dev /dev --proc /proc true && echo "sandbox OK"
```

All'avvio il log dice se è attiva: `Terminale in sandbox (bubblewrap)`. Se invece compare `Terminale SENZA sandbox`:
- installa bubblewrap: `apt install -y bubblewrap`;
- se è installato ma non parte, in Proxmox abilita per il container **Opzioni → Funzionalità → Nesting** e riavvia il container.

Con `TERMINAL_SANDBOX=auto` (predefinito), se la sandbox non è disponibile il terminale funziona lo stesso, senza confinamento. Per avere la certezza del confinamento, metti `TERMINAL_SANDBOX=obbligatoria` nelle [variabili fisse](#variabili-fisse): senza sandbox, terminale ed `esegui_python` si rifiutano di eseguire comandi.

### Altre protezioni
1. **Container LXC unprivileged.** Si controlla sull'host Proxmox con `pct config <ID> | grep unprivileged`.
2. **Utente dedicato, non root.** Così il codice del server non è modificabile e i segreti, nel file delle [variabili fisse](#variabili-fisse) (`/etc/mcp-server/mcp-server.env`, permessi 600), non sono leggibili:
   ```bash
   useradd -r -m -s /bin/bash mcp && mkdir -p /mnt/hdd-esterno && chown -R mcp:mcp /mnt/hdd-esterno
   ```
   Il servizio in [`deploy/mcp-server.service`](deploy/mcp-server.service) usa `User=mcp`, `ProtectSystem=strict` e `NoNewPrivileges`; i segreti arrivano tramite `EnvironmentFile=-/etc/mcp-server/mcp-server.env`, che systemd legge come root.
3. **Cartella dati e punti di montaggio.** Il server può scrivere solo in `/mnt`, dove sta la cartella dei dati (`DATA_DIR`, nelle variabili fisse), e nella home di `mcp`. `/media` e `/srv` sono nascoste all'intero processo del server (`TemporaryFileSystem=`). Vedi [Cartella dei dati](#cartella-dei-dati).
4. **Firewall del container:** vedi [Firewall del container](#firewall-del-container).
5. **`MCP_API_KEY` sempre impostata**, altrimenti chiunque sulla rete può usare il terminale.
6. Se il terminale non ti serve: `ENABLE_TERMINAL=false`.

### Cartella dei dati
La cartella dei dati si imposta con `DATA_DIR` nelle [variabili fisse](#variabili-fisse), per esempio `DATA_DIR=/mnt/hdd-esterno`. Il servizio [`deploy/mcp-server.service`](deploy/mcp-server.service) rende scrivibile tutta `/mnt` (`ReadWritePaths=/mnt`), quindi per cambiare cartella o disco sotto `/mnt` basta modificare `DATA_DIR`, senza toccare il servizio.

- **Al primo avvio** la cartella deve esistere ed essere dell'utente `mcp`:
  ```bash
  mkdir -p /mnt/hdd-esterno && chown -R mcp:mcp /mnt/hdd-esterno
  ```
  Se `chown` risponde `Operation not permitted`, il disco è montato dall'host Proxmox in un container unprivileged: la proprietà va impostata sull'host, con uid e gid pari a 100000 più l'uid di `mcp` nel container (`id -u mcp`).
- **Per spostare i dati** da una cartella precedente (es. `/opt/mcp-server/data`):
  ```bash
  systemctl stop mcp-server && cp -a /opt/mcp-server/data/. /mnt/hdd-esterno/ && chown -R mcp:mcp /mnt/hdd-esterno && systemctl start mcp-server
  ```
- **Il server** può scrivere in tutta `/mnt`. **L'agente** invece vede solo il workspace (`DATA_DIR/workspace`), grazie alla sandbox del terminale e al confinamento dei tool sui file: gli altri file del disco restano fuori dalla sua portata.
- **Se il disco non è montato,** il server scriverebbe nella cartella vuota sul disco del container: controlla che il disco sia montato prima di avviare il servizio.
- **Per una cartella fuori da `/mnt`:** oltre a `DATA_DIR`, aggiungila a `ReadWritePaths` con un drop-in (`systemctl edit mcp-server`), che gli aggiornamenti non toccano.

### Firewall del container
Il terminale dell'agente ha accesso alla rete (serve per `git clone`, `pip`, `npm`), e senza firewall potrebbe raggiungere anche la tua rete locale: router, interfaccia di Proxmox, altri dispositivi. Il firewall di Proxmox limita il container a internet più i soli servizi della LAN che servono al server.

**1. Abilita il firewall.** In Proxmox servono tre interruttori:
- **Datacenter → Firewall → Opzioni → Firewall: Sì.** Proxmox continua a consentire interfaccia web e SSH dalla rete locale, ma prima di attivarlo verifica di avere un accesso di riserva (per esempio la console fisica).
- **Container → Firewall → Opzioni → Firewall: Sì**, con *Criterio di ingresso* `DROP` e *Criterio di uscita* `ACCEPT`.
- **Container → Rete → `net0` → spunta Firewall.**

**2. Regole del container.** Sull'host Proxmox, in `/etc/pve/firewall/<ID>.fw` (dove `<ID>` è il numero del container), oppure dall'interfaccia (Container → Firewall → Aggiungi). L'esempio usa la rete `192.168.178.0/24`, il router `192.168.178.1` e Home Assistant su `192.168.178.48`: **adatta gli indirizzi alla tua rete**.

```
[OPTIONS]
enable: 1
policy_in: DROP
policy_out: ACCEPT

[RULES]
IN ACCEPT -source 192.168.178.0/24 -p tcp -dport 8000 -log nolog # server MCP (OpenWebUI e browser)
IN ACCEPT -source 192.168.178.0/24 -p tcp -dport 22 -log nolog # SSH per l'amministrazione
OUT ACCEPT -dest 192.168.178.1 -p udp -dport 53 -log nolog # DNS del router
OUT ACCEPT -dest 192.168.178.1 -p tcp -dport 53 -log nolog # DNS del router
OUT ACCEPT -dest 192.168.178.48 -p tcp -dport 8123 -log nolog # Home Assistant
OUT ACCEPT -dest 192.168.178.50 -p tcp -dport 2785 -log nolog # gateway OpenWA (se lo usi)
OUT ACCEPT -dest 192.168.178.30 -p tcp -dport 8124 -log nolog # bridge Echo (se lo usi)
OUT DROP -dest 10.0.0.0/8 -log nolog # resto della rete locale
OUT DROP -dest 172.16.0.0/12 -log nolog
OUT DROP -dest 192.168.0.0/16 -log nolog
OUT DROP -dest 169.254.0.0/16 -log nolog
```

Le regole si applicano in ordine: le eccezioni (DNS, Home Assistant, OpenWA, bridge Echo) vengono prima dei blocchi. Se il DNS del container non è il router, sostituisci il suo indirizzo, altrimenti il server non risolve più i nomi (errore `Could not resolve host`).

**3. Verifica** dal terminale dell'agente, o come utente `mcp`:
```bash
runuser -u mcp -- curl -s -m 5 -o /dev/null -w "%{http_code}\n" https://github.com
```
```bash
runuser -u mcp -- curl -s -m 5 -o /dev/null -w "%{http_code}\n" http://192.168.178.1 || echo "LAN bloccata"
```
Il primo deve rispondere `200` (o un altro codice HTTP), il secondo deve fallire.

**Limite:** il firewall vale per tutto il container e non distingue il server dall'agente. Le porte aperte per il server (Home Assistant, OpenWA, bridge Echo) sono raggiungibili anche dal terminale dell'agente, che però non ha i token e riceve solo errori di autenticazione: per questo nel bridge Echo conviene impostare `API_TOKEN` (e qui `ECHO_BRIDGE_TOKEN`). Il resto della rete locale è bloccato per entrambi.

---

## Test

La suite avvia in locale un **Home Assistant finto**, un **sito web finto** (pagine, PDF, immagini, meteo, news, cambi), API e gateway finti e il server, poi chiama i tool con un vero client MCP Streamable HTTP, come fa OpenWebUI. Non serve accesso a internet.

```bash
pip install pytest && pytest -v
```

pytest riporta un test per ogni area (web, GitHub, Home Assistant, sandbox, documenti...), con l'elenco dei controlli non superati. In alternativa, senza pytest e con tutti i dettagli:

```bash
/opt/mcp-server/venv/bin/python tests/run_tests.py
```

A ogni push GitHub Actions esegue lint (`ruff`), formattazione e tutta la suite su Python 3.11 e 3.12 ([`.github/workflows/test.yml`](.github/workflows/test.yml)); lo stato è nel badge in cima a questo README.

Copre 264 controlli: autenticazione, calcolatrice e protezioni, meteo, notizie, valute, memoria, lettura di pagine e PDF, blocchi anti-SSRF e anti DNS rebinding, immagini e screenshot, ripiego della ricerca, terminale (timeout, background, sandbox bubblewrap, segreti), ambienti di sviluppo (Node.js, Python con uv, Go, home persistente), tool sui file, Home Assistant (ricerca, stato, storico, regole di sicurezza, conferme, audit), timer e sveglie sull'Echo, domande all'utente (in chat e con modulo interattivo), cartelle per conversazione (creazione, confinamento, terminale, allegati), link firmati ai file (indirizzo ricavato dalla richiesta), GitHub (clone, commit, push con conferma, blocchi di sicurezza, pull request), lettura di documenti (Word, Excel, PowerPoint, OpenDocument, vecchi formati Office con LibreOffice, PDF anche protetti o scansionati con OCR, RTF, EPUB, email, archivi), rubrica, email e WhatsApp (richiesta del recapito, conferma obbligatoria, codici monouso, allegati, gateway OpenWA), musica sull'Echo con passaggio automatico al brano successivo, bridge Echo (annunci, "non disturbare", avviso dei processi), Google Calendar (con un servizio simulato) e il parsing di date e durate.

Richiede Chromium per Playwright. Se sono installati LibreOffice e tesseract, vengono provati anche i vecchi formati Office, gli OpenDocument e l'OCR; altrimenti quei test vengono saltati. Se Playwright non trova il browser, imposta `CHROMIUM_PATH`.

Non è coperto dai test automatici, perché richiede servizi esterni reali: le pagine reali di Google e Bing, YouTube e YouTube Music, le API reali di Google Calendar, un server SMTP vero, OpenWA e GitHub.

---

## Struttura del repository

```
.
├── mcp_server.py              # punto di ingresso: app HTTP, autenticazione, avvio, riga di comando
├── nucleo/                    # servizi comuni
│   ├── configurazione.py      # variabili d'ambiente, cartelle dati, logging
│   ├── server.py              # server MCP, decoratore dei tool, gestione degli errori
│   ├── utilita.py             # date e durate in italiano, formattazione, archivio JSON
│   ├── rete.py                # download sicuri e controllo degli URL (anti-SSRF)
│   ├── browser.py             # Chromium condiviso dai tool web
│   ├── workspace.py           # cartelle di lavoro e percorsi confinati
│   ├── sandbox.py             # esecuzione dei comandi in bubblewrap
│   ├── invii.py               # conferme obbligatorie, allegati, registro degli invii
│   ├── echo_bridge.py         # client del bridge Echo (annunci, timer, sveglie, musica, volume)
│   └── link_file.py           # link firmati per mostrare i file nella chat
├── tools/                     # un modulo per gruppo di strumenti, con le proprie costanti
│   ├── data_ora.py  calcolatrice.py  meteo.py  valute.py  memoria.py  domande_utente.py
│   ├── ricerca_web.py  pagine_web.py  immagini.py
│   ├── terminale.py  ambienti.py  file_workspace.py  lettura_documenti.py  condivisione_file.py
│   ├── rubrica.py  email_smtp.py  whatsapp.py  github.py
│   ├── home_assistant.py  timer.py  musica.py  calendario_google.py
│   └── __init__.py            # ordine di registrazione degli strumenti
├── requirements.txt           # dipendenze dirette
├── requirements.lock          # versioni esatte provate (usate per installare)
├── pyproject.toml             # configurazione di ruff e pytest
├── .github/workflows/test.yml # test automatici su GitHub
├── .env.example               # impostazioni generali (copiate nel .env a ogni aggiornamento)
├── deploy/
│   └── mcp-server.service     # unit systemd
├── tests/
│   ├── test_e2e.py            # un test pytest per ogni area
│   ├── run_tests.py           # suite end-to-end
│   ├── mocks.py               # Home Assistant, sito web, API, gateway e bridge Echo finti
│   └── documenti.py           # documenti di prova (Word, Excel, PowerPoint, PDF, ...)
└── docs/
    └── MILESTONES.md          # attività svolte e prossimi passi
```

Lo stile del codice è controllato con `ruff` (`ruff check . && ruff format .`). Per aggiungere uno strumento: crea un modulo in `tools/` con le sue costanti (lette con `env_str`, `env_int`, `env_bool` da `nucleo.configurazione`), decora le funzioni con `@tool(...)` da `nucleo.server` e aggiungi il modulo a `tools/__init__.py`.

## Problemi comuni

| Problema | Soluzione |
|---|---|
| OpenWebUI: 404 o redirect | Usa l'URL con lo slash finale: `http://IP:8000/` |
| 401 Unauthorized | La chiave Bearer in OpenWebUI deve coincidere con `MCP_API_KEY` |
| OpenWebUI non riesce a usare gli strumenti (errori di sessione o di connessione) | Imposta `ENABLE_ELICITATION=false` per tornare alla modalità senza sessione |
| Mancano i tool di HA, timer, musica o del calendario | Manca `HASS_TOKEN`, `ECHO_BRIDGE_URL` o mancano le credenziali Google nelle [variabili fisse](#variabili-fisse), oppure il servizio installato non è aggiornato (deve contenere `EnvironmentFile=-/etc/mcp-server/mcp-server.env`): controlla la riga `Tool attivi` nel log e la verifica della sezione Variabili fisse |
| La ricerca restituisce "captcha" | È normale ogni tanto: il server passa da solo a DuckDuckGo o Bing |
| Nel log: `Terminale SENZA sandbox` | Installa `bubblewrap`; se non basta, in Proxmox abilita Opzioni → Funzionalità → Nesting per il container (vedi [Sicurezza](#sicurezza)) |
| Il browser non parte come utente `mcp` | Installa Chromium con `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright` |
| `PermissionError: [Errno 13] Permission denied: '/mnt/hdd-esterno/...'` | Alcuni file della cartella dati appartengono a root, di solito perché `mcp_server.py` è stato eseguito come root. Esegui `chown -R mcp:mcp /mnt/hdd-esterno` e riavvia il servizio. All'avvio il server segnala nel log i file non scrivibili |
| Nel log: `Executable doesn't exist at /opt/ms-playwright/...` | Playwright è stato aggiornato ma il suo browser no: esegui `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright /opt/mcp-server/venv/bin/playwright install chromium` e riavvia il servizio (passo 3 di [Aggiornamento](#aggiornamento)) |
| Le aree non compaiono nella ricerca HA | Il token deve appartenere a un utente admin (per `/api/template`) |
| La musica non parte | Aggiorna `yt-dlp`, installa deno; controlla che `--check` elenchi gli Echo collegati |
| L'email non parte: "Credenziali SMTP rifiutate" | Con Gmail serve una password per le app, non la password dell'account |
| WhatsApp: errore dal gateway | Controlla nella dashboard del gateway che la sessione sia collegata al telefono, che la chiave sia giusta e che `OPENWA_SESSION_ID` sia l'ID e non il nome della sessione |
| L'Echo non suona i timer o non riproduce | `python mcp_server.py --check` deve elencare gli Echo; nel log cerca `[echo]` o `registrazione sull'Echo non riuscita`. 401 = `ECHO_BRIDGE_TOKEN` diverso da `API_TOKEN` del bridge |
