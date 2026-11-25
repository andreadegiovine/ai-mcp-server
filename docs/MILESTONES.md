# Milestones

Registro delle attività svolte sul progetto e dei prossimi passi. Le voci più recenti sono in alto: aggiungi una nuova sezione a ogni sessione di lavoro.

Legenda: ✅ completato · 🔄 in corso · ⏳ da fare · 💡 idea da valutare

---

## 2026-11-25 · Integrazione con il bridge vocale Echo (ai-echo-dot-bridge)

### Obiettivo
Il proxy llama.cpp viene usato a voce tramite [ai-echo-dot-bridge](https://github.com/andreadegiovine/ai-echo-dot-bridge). Analizzate le richieste accettate dal bridge (`REQUESTS.md`) e adattati i servizi del server che devono parlare con l'Echo.

### Attività svolte
- ✅ **Client del bridge** `nucleo/echo_bridge.py`: richieste REST con token, errori del bridge mostrati in chiaro, elenco e ricerca degli Echo per nome, annunci con ripiego su notifica (anello giallo) quando l'Echo non parla ("non disturbare"), volume tramite lo strumento MCP `set_volume` del bridge
- ✅ **Timer e sveglie**: registrati anche sull'Echo (`/api/timers`, `/api/alarms`) per anello e suoneria nativi, annullamento propagato, Echo ricordato per i comandi successivi; nuove sveglie ricorrenti (`giorni`) e parametro `echo`; annuncio vocale alla scadenza per i timer che non è stato possibile registrare
- ✅ **Musica sull'Echo**: nuovo backend `BackendEcho` su `/api/music` con URL diretto, intestazioni HTTP di yt-dlp e titolo; la coda e la radio di brani simili restano del server (passaggio al brano successivo quando il bridge torna `idle`); dispositivo `echo` o nome dell'Echo, predefinito quando il bridge è configurato; volume e nuova azione `spegni_tra` (sleep del bridge, con dissolvenza; timer interno per gli altri dispositivi)
- ✅ **Processi lunghi**: `terminale_esegui(in_background=true, avvisa_al_termine=true)` annuncia sull'Echo la fine e l'esito
- ✅ `--check` elenca gli Echo trovati; firewall del container e documentazione aggiornati
- ✅ **Test**: bridge finto (REST + MCP) e nuova area `echo_bridge` (timer, sveglie, ricorrenti, ripiego, "non disturbare", musica, volume, spegnimento, avviso dei processi): **275/275 controlli superati**, 21 aree pytest

### Decisioni prese
- Campo `echo` vuoto di default: il bridge usa l'Echo dell'ultima richiesta vocale; il server memorizza il nome restituito
- Niente webhook in ingresso per gli eventi musicali: il server controlla lo stato ogni 2 secondi (come per gli altri dispositivi), così non serve aprire una rotta senza chiave API
- Le notifiche di Home Assistant dei timer restano attive accanto all'Echo

### Prossimi passi
- ⏳ Prova dal vivo con il bridge e un Echo vero (in particolare URL di YouTube e volume via MCP)
- 💡 Promemoria vocali (`/api/reminders`) e notifiche per gli eventi del calendario
- 💡 Webhook del bridge (`finished`, `error`) per un passaggio di brano più rapido

---

## 2026-11-25 · Revisione del codice: sicurezza, affidabilità, voce, qualità

### Attività svolte
- ✅ **Firewall del container** (proposta 1a): README con la configurazione del firewall di Proxmox che limita il container a internet più DNS, Home Assistant e OpenWA, e con la verifica; documentato il limite (server e agente condividono la rete del container)
- ✅ **Anti DNS rebinding**: le richieste dei tool web si collegano all'IP già verificato (header Host, SNI e certificato restano sul nome originale); trovato e corretto un difetto per cui con `NO_PROXY` impostato la protezione restava inattiva
- ✅ **Versioni bloccate**: `requirements.lock` (provato con la suite, installabile su Python 3.11, 3.12 e 3.13), usato da installazione e aggiornamento; solo yt-dlp sempre all'ultima versione
- ✅ **Nessun blocco del server**: letture, scritture e scansioni di file dei tool spostate in thread
- ✅ **Manutenzione automatica**: rotazione di `invii.log` e `hass_audit.log`, pulizia giornaliera di log dei processi, screenshot e cache dell'agente, spazio occupato nel log e in `--check`
- ✅ **Testi per la voce**: accenti veri al posto degli apostrofi (`è`, `più`, `già`...), niente emoji né punti elenco nei testi restituiti al modello
- ✅ **Qualità del codice**: nomi condivisi resi pubblici (`env_str`, `accetta_cookie`, `client_hass`...), `hass_esegui_servizio` divisa in funzioni, `ruff` per lint e formattazione (`pyproject.toml`)
- ✅ **Test**: pytest con un test per ogni area (20 aree), GitHub Actions su Python 3.11 e 3.12 con lint, formattazione e sandbox attiva (**255/255 controlli superati**)
- ✅ **Versione visibile**: commit in esecuzione nel log di avvio, in `/health` e in `--check`

### Decisioni prese
- La suite resta un'unica sequenza (gli scenari dipendono l'uno dall'altro); pytest la divide per aree invece che per file
- La versione si legge dai file di `.git` senza eseguire git, che rifiuterebbe un repository di proprietà di un altro utente

### Prossimi passi
- ⏳ **Chiave API obbligatoria per tutto il server** (proposta 2), da attivare al termine dello sviluppo
- ⏳ Verificare il primo esito di GitHub Actions e il firewall sul container reale
- 💡 Isolare la rete della sandbox dal resto del container (namespace di rete dedicato), così l'agente non raggiunge nemmeno Home Assistant e OpenWA

---

## 2026-11-24 · Ambienti di sviluppo per l'agente

### Problema
L'agente non riusciva a installare Node.js per eseguire dei test: nella sandbox non ha root (apt non funziona, ed è voluto) e la home era temporanea, quindi anche gli strumenti scaricati a mano andavano persi.

### Attività svolte
- ✅ Home persistente dell'agente: `DATA_DIR/home_agente`, vista come `/home/agente` nella sandbox, con `~/.local/bin`, `~/go/bin` e `~/.cargo/bin` nel PATH e `npm install -g` in `~/.local`; separata dal workspace ed esclusa dal controllo dei permessi all'avvio
- ✅ Nuovo strumento `ambiente_installa`: Node.js (con npm), Python tramite uv e Go dalle distribuzioni ufficiali, per architettura x86_64 e arm64
- ✅ Descrizione del terminale aggiornata: niente apt/sudo, come installare senza root
- ✅ 7 nuovi test con distribuzioni finte servite in locale (**252/252 superati**)

### Da verificare
- ⏳ Installazione reale da nodejs.org, go.dev e github.com (uv) sul server

---

## 2026-11-24 · Struttura a moduli, link ai file, GitHub

### Attività svolte
- ✅ **Codice diviso in moduli**: `mcp_server.py` (punto di ingresso, ~240 righe), `nucleo/` (configurazione, server e decoratore dei tool, utilità, rete, browser, workspace, sandbox, conferme degli invii, link ai file) e `tools/` con un file dal nome parlante per ogni gruppo di strumenti, ciascuno con le proprie costanti. Comportamento invariato: stessi 218 test superati prima delle novità
- ✅ **Immagini e file visibili nella chat**: il server pubblica i file del workspace su `/file/<token>/<nome>` con link firmati (HMAC) e a scadenza (`LINK_FILE_GIORNI`), senza chiave API. `screenshot_pagina`, `vedi_immagine` e `scarica_file` restituiscono il markdown con il link; nuovo strumento `link_file`; istruzioni al modello di non usare mai percorsi locali come link. L'indirizzo del server nei link è quello della richiesta del client, senza configurazione; `MCP_URL_PUBBLICO` è solo un'alternativa facoltativa
- ✅ **GitHub**: `github_clona`, `github_stato`, `github_commit`, `github_push`, `github_crea_pull_request`, solo sui repository in `GITHUB_REPO_AUTORIZZATI`. Token mai salvato su disco né visibile ai comandi dell'agente; commit nella sandbox; push e pull request con conferma obbligatoria e codice legato al commit; controllo della configurazione git prima del push (hook, proxy, fsmonitor, include, remote non autorizzati bloccano il push); niente push forzato
- ✅ 28 nuovi test, con repository GitHub finti (`file://`) e API simulate (**245/245 superati**)

### Decisioni prese
- Le parti condivise stanno in `nucleo/`, gli strumenti in `tools/`; per aggiungere uno strumento basta un modulo in `tools/` registrato in `tools/__init__.py`
- I link ai file sono capability firmate invece che protetti dalla chiave API, perché il browser non può inviarla con un tag `<img>`
- Il push usa il token solo dalla riga di comando del server, dopo aver verificato la configurazione del repository: l'agente non può usarlo per altro

- ✅ Servizio systemd: `/mnt` scrivibile (`ReadWritePaths=/mnt`) e non più nascosta (restano nascoste `/media` e `/srv`), così la cartella dei dati può stare su un disco montato lì; `DATA_DIR` si imposta nelle variabili fisse. README con la procedura per la cartella dei dati

### Da verificare
- ⏳ Link ai file visualizzati in OpenWebUI (il browser deve raggiungere l'indirizzo con cui OpenWebUI si collega al server)
- ⏳ Clone, push e pull request su GitHub reale con un token fine-grained

---

## 2026-11-21 · Sandbox del terminale

### Problema
Dal terminale l'agente ha trovato e potuto leggere file fuori dal workspace (`find / -name .env`: `/opt/mcp-server/.env`, `/mnt/hdd-esterno/...`). I tool sui file erano confinati, ma `terminale_esegui` ed `esegui_python` eseguivano comandi con i permessi dell'utente del servizio.

### Attività svolte
- ✅ Terminale, `esegui_python` e processi in background girano in una sandbox **bubblewrap**: vedono solo la cartella di lavoro (workspace o cartella della conversazione) e, in sola lettura, `/usr`, `/etc` (senza `/etc/mcp-server`) e il Python del server; `/tmp` e home temporanee; rete disponibile
- ✅ `TERMINAL_SANDBOX` = `auto` (predefinito) / `obbligatoria` / `disattivata`; all'avvio il log dice se la sandbox è attiva e, se no, perché
- ✅ Servizio systemd: `TemporaryFileSystem=/mnt:ro /media:ro /srv:ro`, che nasconde i dischi montati all'intero server anche senza sandbox
- ✅ `bubblewrap` tra i pacchetti di installazione; README (sezione Sicurezza riscritta) e problemi comuni aggiornati
- ✅ 9 nuovi test: niente `/opt`, `/mnt` né file fuori dal workspace, scritture solo nella cartella di lavoro, rete, Python, modalità obbligatoria e auto (**218/218 superati**)

### Da verificare
- ⏳ bubblewrap nel container LXC di Proxmox come utente `mcp` (potrebbe richiedere la funzionalità Nesting)
- ⏳ `TemporaryFileSystem=` nel servizio sul container reale

---

## 2026-11-20 · Lettura dei documenti

### Attività svolte
- ✅ `file_leggi` estrae il testo dai documenti del workspace: PDF (anche protetti senza password o scansionati, con OCR), Word `.docx`/`.doc`, Excel `.xlsx`/`.xls`, PowerPoint `.pptx`/`.ppt`, LibreOffice `.odt`/`.ods`/`.odp`, RTF, EPUB, email `.eml`, immagini (OCR) e archivi zip/tar (elenco dei file); paginazione con `da_riga`/`num_righe`, cache del testo estratto
- ✅ Dipendenze Python aggiunte: `python-docx`, `openpyxl`, `xlrd`, `python-pptx`, `striprtf`, `pypdfium2`. Facoltativi sul sistema: `tesseract-ocr` (+ `tesseract-ocr-ita`) per l'OCR, LibreOffice `-nogui` per i vecchi formati Office e per gli `.xls` che in realtà sono HTML; se mancano, lo strumento indica il comando `apt`
- ✅ Nuove variabili `OCR_LINGUE` e `OCR_MAX_PAGINE`
- ✅ 26 nuovi test con documenti generati al momento, anche convertiti con LibreOffice e riconosciuti con l'OCR (**209/209 superati**)

### Decisioni prese
- HTML, CSV, JSON e codice restano testo grezzo, per poterli modificare con `file_modifica`
- LibreOffice viene usato solo per i formati binari vecchi, una conversione alla volta e con un profilo dedicato in `data/.libreoffice`

### Da verificare
- ⏳ LibreOffice e tesseract eseguiti dal servizio come utente `mcp` con `ProtectSystem=strict` (provati solo come root nei test)

---

## 2026-11-19 · Cartelle per conversazione

### Attività svolte
- ✅ **Variabili fisse fuori dal repository**: segreti e valori personali in `/etc/mcp-server/mcp-server.env` (permessi 600), caricati direttamente dal servizio con `EnvironmentFile=-/etc/mcp-server/mcp-server.env` (facoltativo, ha la precedenza sul `.env`); inizialmente tramite un drop-in di systemd, poi reso il modo standard della repo. Il `.env` diventa una copia di `.env.example`, sostituita a ogni aggiornamento; nessuna modifica al codice
- ✅ `.env.example`: segnaposto di segreti e preferenze commentati (`MCP_API_KEY`, `HASS_TOKEN`, `HASS_URL`, `METEO_DEFAULT_LOCALITA`, `MUSIC_DEFAULT_DEVICE`), perché copiati nel `.env` diventerebbero valori attivi
- ✅ README: sezione "Variabili fisse" (file, caricamento, verifica) e comando di aggiornamento completo che copia anche `.env.example` e il servizio
- ✅ Tutti gli strumenti che usano il workspace accettano `id_conversazione` (facoltativo): lavorano in `data/workspace/<id>/`, creata se non esiste. Riguarda terminale, processi in background, Python, `file_*`, download, screenshot, `vedi_immagine` e allegati locali di email e WhatsApp
- ✅ Confinamento nella cartella della conversazione, validazione dell'ID, `$WORKSPACE` del terminale puntato sulla cartella, lista dei processi filtrata per conversazione, log alla creazione di ogni cartella (**183/183 superati**)
- ✅ Provata e poi rimossa, su richiesta, la lettura automatica dell'ID dall'header `X-OpenWebUI-Chat-Id`

### Decisioni prese
- L'ID arriva solo dal parametro `id_conversazione`, passato dal modello; senza ID gli strumenti lavorano nella radice del workspace come prima

---

## 2026-11-18 · Titoli adatti alla voce, guida all'aggiornamento, domande all'utente

### Attività svolte
- ✅ Titoli dei tool resi leggibili da un sintetizzatore vocale: tolti parentesi, due punti e prefissi tecnici (OpenWebUI li mostra in chat come `Uso "<titolo>"...`). Esempi:
  - `Terminale (bash)` → `Terminale`
  - `Home Assistant: cerca entità` → `Cerca dispositivi della casa`
  - `Google Calendar: crea evento` → `Crea evento nel calendario`
- ✅ Nuovo test che controlla che ogni titolo contenga solo lettere e spazi (**124/124 superati**)
- ✅ README: nuova sezione **Aggiornamento** (git pull, dipendenze, browser Playwright, confronto delle variabili del `.env`, riavvio del servizio, ritorno alla versione precedente)

- ✅ Nuovo strumento `chiedi_all_utente`: domande con opzioni prefissate (anche a scelta multipla) e/o risposta libera
  - Modalità predefinita "domanda in chat": il modello pone la domanda in modo naturale e senza simboli (adatta alla voce) e attende la risposta; funziona con qualsiasi client
  - Modulo interattivo (elicitation MCP, `ENABLE_ELICITATION`, attivo di default), con ripiego automatico sulla domanda in chat se il client non la supporta
  - Filtrato dal log un errore innocuo che la libreria MCP registra alla chiusura delle sessioni
  - 15 nuovi test, tra cui un client MCP che compila il modulo e un server con `ENABLE_ELICITATION=false`; tutta la suite gira ora in modalità con sessione (**139/139 superati**)

- ✅ Primo collaudo con OpenWebUI: la modalità con sessione funziona; OpenWebUI non mostra i moduli interattivi, quindi le domande passano da sole alla chat
- ✅ Domande in chat più adatte ai modelli locali: il server prepara la frase già pronta da dire (es. "Scegli un numero da 1 a 5: 1, 2, 3, 4 oppure 5?"), istruzioni più brevi, niente elenco ridondante "1. 1"; regola "rispondi sempre in italiano" anche nelle istruzioni del server (il modello aveva inserito parole in cinese); log che indica quando la domanda passa in chat (**141/141 superati**)

- ✅ **Rubrica, email e WhatsApp**
  - `rubrica_salva` / `rubrica_cerca` / `rubrica_elimina`: contatti con email e numero; `io` indica l'utente; numeri normalizzati (prefisso `39` predefinito)
  - `invia_email` (SMTP, allegati da URL o workspace) e `invia_whatsapp` (WAHA self-hosted o CallMeBot)
  - Flusso: recapito mancante → il modello chiede "A quale indirizzo email te la mando?" e lo salva in rubrica; recapito presente → "Mando la mail a ...?"; invio solo dopo la conferma, con codice monouso che blocca destinatario e contenuto
  - Conferma con modulo interattivo quando il client lo supporta; registro degli invii in `data/invii.log`
  - 23 nuovi test con SMTP, WAHA e CallMeBot simulati (**164/164 superati**)

- ✅ **Gateway WhatsApp OpenWA** (open source MIT, self-hosted, gratuito senza funzioni a pagamento) aggiunto come opzione consigliata; endpoint `send-text` / `send-image` per sessione, immagini in base64, errori chiari per sessione o chiave sbagliate (**167/167 superati**)

- ✅ **Rimossi i gateway WAHA e CallMeBot**: WhatsApp passa solo da OpenWA; non serve piu' `WHATSAPP_BACKEND`, lo strumento si attiva con `OPENWA_URL` e `OPENWA_SESSION_ID` (**164/164 superati**)

- ✅ **Correzioni dopo il primo uso reale**
  - Messaggio chiaro, con il comando da eseguire, quando Playwright viene aggiornato ma il suo Chromium non è installato (`Executable doesn't exist`)
  - Le cartelle dati si creano solo all'avvio del servizio, non con `--check`: eseguito come root, creava un workspace di proprietà di root, non scrivibile dall'utente `mcp` (`PermissionError`)
  - All'avvio il server segnala nel log i file di `data/` non scrivibili e il comando `chown` per sistemarli; gli errori di permesso nei tool hanno un messaggio comprensibile
  - README: due nuove voci nei problemi comuni (**167/167 superati**)

### Decisioni prese
- Email via SMTP (universale; con Gmail basta una password per le app) invece delle API Gmail
- OpenWA preferito a WAHA: licenza MIT, nessuna funzione a pagamento, progetto molto diffuso. Il suo server MCP non viene collegato direttamente a OpenWebUI, per mantenere rubrica e conferma obbligatoria
- WhatsApp solo tramite OpenWA (WAHA e CallMeBot valutati e poi rimossi): qualsiasi numero e immagini, ma non ufficiale, con rischio di blocco del numero. Le API ufficiali Meta sono escluse per complessità (account business, modelli di messaggio)
- La conferma obbligatoria usa un codice monouso legato al messaggio preparato: il modello non puo' cambiare destinatario o contenuto dopo la conferma
- L'elicitation funziona solo con server con sessione e risposte in streaming (verificato: con `stateless_http` + `json_response` la richiesta al client non arriva)
- `ENABLE_ELICITATION` **attiva di default**: se il client non supporta i moduli, le domande tornano testuali da sole. Il cambio di trasporto (sessione + streaming) non è ancora stato provato con OpenWebUI; in caso di problemi `ENABLE_ELICITATION=false` ripristina la modalità originale
- I titoli sono frasi naturali in italiano, pensate per l'integrazione futura con un servizio vocale; i nomi tecnici dei tool (`hass_cerca_entita`, ...) non cambiano, quindi le configurazioni esistenti restano valide

---

## 2026-11-17 · v2.0: riscrittura completa del server

### Punto di partenza
Server MCP v1 con 6 tool: data/ora, ricerca Google con Playwright, lettura pagine, meteo per coordinate, calcolatrice, lettura stati di Home Assistant (con token scritto nel codice e dump di tutte le entità).

### Attività svolte
- ✅ **Base del server**
  - Tutti i tool resi asincroni (le chiamate bloccanti girano in thread), così le richieste non si bloccano a vicenda
  - Decoratore `tool()` comune: gestione uniforme degli errori, messaggi leggibili per il modello, gruppi di tool attivabili e disattivabili, `DISABLED_TOOLS`
  - Configurazione da variabili d'ambiente e file `.env`, nessun segreto nel codice
  - Autenticazione opzionale `MCP_API_KEY` (Bearer o `X-API-Key`), endpoint `/health`
  - Ciclo di vita: ripristino dei timer e avvio del monitor musicale all'avvio; chiusura di browser, mpv e processi allo spegnimento
  - Opzione `--check` per vedere quali gruppi e tool sono attivi
- ✅ **Servizi esistenti migliorati**
  - Ricerca: Google, poi DuckDuckGo, poi Bing; filtro di annunci e duplicati; decodifica dei redirect di Google e Bing
  - Lettura pagine: supporto PDF, estrazione in markdown, divisione in parti, immagini principali, cache di 10 minuti
  - Browser: sessione persistente con cookie salvati, pagine in parallelo, riavvio automatico dopo un crash, gestione dei banner cookie
  - Meteo: geocodifica per nome della località, previsioni fino a 16 giorni, descrizioni in italiano
  - Calcolatrice: funzioni, costanti, percentuali, protezione da espressioni troppo costose
  - Data e ora: giorno della settimana, numero di settimana, fuso orario opzionale
- ✅ **Home Assistant affidabile e sicuro**
  - Client con retry, timeout, messaggi d'errore chiari, cache breve
  - `hass_cerca_entita` con sinonimi italiani, aree e tolleranza agli errori di battitura (al posto del dump completo)
  - `hass_stato` in tempo reale, `hass_panoramica`, `hass_storico` (statistiche), `hass_servizi`
  - `hass_esegui_servizio` con allowlist di domini, blocco di restart/stop/reload, validazione degli entity_id, rifiuto di `all` e `area_id`, conferma obbligatoria per serrature, allarmi, garage e cancelli, blocklist e allowlist di entità, sola lettura, audit log
  - Attributi sensibili (token, password) nascosti
- ✅ **Immagini:** `cerca_immagini` con anteprime visibili al modello e markdown per la chat, `vedi_immagine`, `screenshot_pagina`
- ✅ **Terminale e workspace:** `terminale_esegui` (con processi in background), `terminale_processi`, `esegui_python`, `file_leggi`, `file_scrivi`, `file_modifica`, `file_elenca`, `file_cerca`, `scarica_file`; confinamento dei percorsi e rimozione dei segreti dall'ambiente dei comandi
- ✅ **Musica stile Alexa:** ricerca su YouTube Music e radio di brani simili; riproduzione in locale (mpv via IPC) o su un media_player di HA; passaggio automatico al brano successivo; controlli e coda
- ✅ **Timer e sveglie:** multipli, con etichetta, persistenti al riavvio, notifiche tramite HA (persistent notification, push, TTS, script) e suono locale
- ✅ **Google Calendar:** elenco, creazione, modifica ed eliminazione (con conferma) di eventi e lista dei calendari; autenticazione con service account o OAuth (`--google-auth`)
- ✅ **Altri strumenti:** `cerca_notizie` (Google News RSS), `converti_valuta` (BCE), memoria permanente
- ✅ **Sicurezza web:** protezione anti-SSRF (niente rete locale, niente `file://`, controllo su ogni redirect e su ogni richiesta del browser), limiti di dimensione
- ✅ **Test:** suite end-to-end con un client MCP reale, un Home Assistant finto e un sito finto (**123/123 superati**)
- ✅ **Deploy e documentazione:** guida per Proxmox LXC + venv, gestione del `.env`, servizio systemd con hardening (utente `mcp`, `EnvironmentFile`, `ProtectSystem=strict`), analisi del confinamento dell'agente, README

### Decisioni prese
- Endpoint sulla root `/` con `stateless_http` e `json_response`, per compatibilità con OpenWebUI (dal 2026-11-18 solo con `ENABLE_ELICITATION=false`)
- Dipendenza fissata a `mcp<2`: la versione 2.x rinomina FastMCP in MCPServer
- Il confine di sicurezza del terminale è affidato al sistema operativo (container unprivileged, utente dedicato, systemd, firewall), non al codice
- Il calendario usa il service account come metodo principale, adatto a un server senza interfaccia grafica

### Non verificato (rete esterna non disponibile durante lo sviluppo)
- ⏳ Ricerca su Google e Bing reali (parser provati solo su HTML di esempio)
- ⏳ YouTube e YouTube Music reali (ricerca, radio, estrazione degli stream)
- ⏳ Google Calendar reale
- ⏳ Servizio systemd con hardening su un container vero (in particolare Chromium con `ProtectSystem=strict` e utente `mcp`)

---

## Prossimi passi

- ⏳ Primo deploy su Proxmox e verifica dal vivo di ricerca, musica e calendario
- ⏳ Collaudo con OpenWebUI: visualizzazione delle immagini restituite dai tool e scelta dei tool da tenere attivi per il modello in uso
- ⏳ Provare l'invio reale di email (Gmail) e WhatsApp (OpenWA)
- ⏳ Verificare con OpenWebUI la modalità con sessione (default) e se mostra i moduli interattivi; in caso di problemi usare `ENABLE_ELICITATION=false`
- 💡 Sandbox dei comandi del terminale con bubblewrap (visibilità limitata al solo workspace)
- 💡 Musica tramite Music Assistant, come alternativa a yt-dlp per gli altoparlanti di HA
- 💡 Pausa automatica della musica (o volume abbassato) quando scatta un timer
- 💡 Esecuzione dei test in GitHub Actions
- 💡 Integrazione con un servizio vocale: valutare se togliere emoji e simboli (✅, ⚠️, 🎵, •) anche dai testi restituiti dai tool
