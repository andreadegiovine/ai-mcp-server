# Milestones

Registro delle attività svolte sul progetto e dei prossimi passi. Le voci più recenti sono in alto: aggiungi una nuova sezione a ogni sessione di lavoro.

Legenda: ✅ completato · 🔄 in corso · ⏳ da fare · 💡 idea da valutare

---

## 2026-11-18 · Titoli dei tool adatti alla voce

### Attività svolte
- ✅ Titoli dei tool resi leggibili da un sintetizzatore vocale: tolti parentesi, due punti e prefissi tecnici (OpenWebUI li mostra in chat come `Uso "<titolo>"...`). Esempi:
  - `Terminale (bash)` → `Terminale`
  - `Home Assistant: cerca entità` → `Cerca dispositivi della casa`
  - `Google Calendar: crea evento` → `Crea evento nel calendario`
- ✅ Nuovo test che controlla che ogni titolo contenga solo lettere e spazi (**124/124 superati**)

### Decisioni prese
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
- Endpoint sulla root `/` con `stateless_http` e `json_response`, per compatibilità con OpenWebUI
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
- 💡 Sandbox dei comandi del terminale con bubblewrap (visibilità limitata al solo workspace)
- 💡 Musica tramite Music Assistant, come alternativa a yt-dlp per gli altoparlanti di HA
- 💡 Pausa automatica della musica (o volume abbassato) quando scatta un timer
- 💡 Esecuzione dei test in GitHub Actions
- 💡 Integrazione con un servizio vocale: valutare se togliere emoji e simboli (✅, ⚠️, 🎵, •) anche dai testi restituiti dai tool
