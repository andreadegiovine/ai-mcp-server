"""
Strumento: installazione di ambienti di sviluppo nella home persistente dell'agente (senza root ne' apt).

L'agente lavora in una sandbox senza permessi di amministratore: apt non e' disponibile. Gli strumenti si
installano quindi nella sua home (/home/agente nella sandbox, DATA_DIR/home_agente sul server), scaricando i
pacchetti ufficiali; restano disponibili per tutti i comandi e le conversazioni successive.
"""
import re
import shlex
from typing import Literal

from nucleo.configurazione import _env
from nucleo.sandbox import esegui_comando
from nucleo.server import UserError, tool
from nucleo.utilita import tronca
from nucleo.workspace import IdConversazione, cartella_lavoro

NODE_DIST_URL = _env("NODE_DIST_URL", "https://nodejs.org/dist").rstrip("/")
GO_URL = _env("GO_URL", "https://go.dev").rstrip("/")
UV_URL = _env("UV_URL", "https://github.com/astral-sh/uv/releases/latest/download").rstrip("/")
INSTALLAZIONE_TIMEOUT = 900

_RE_VERSIONE = re.compile(r"^[A-Za-z0-9.]{0,20}$")

_ARCH = '''case "$(uname -m)" in
  x86_64|amd64) ARCH_NODE=x64; ARCH_GO=amd64; ARCH_UV=x86_64 ;;
  aarch64|arm64) ARCH_NODE=arm64; ARCH_GO=arm64; ARCH_UV=aarch64 ;;
  *) echo "Architettura non supportata: $(uname -m)"; exit 1 ;;
esac
mkdir -p "$HOME/.local/bin" "$HOME/.local/opt"
'''

_NODE = '''RICHIESTA={versione}
V=$(curl -fsSL {url}/index.json | python3 -c '
import json, sys
r = sys.argv[1].lstrip("v")
for e in json.load(sys.stdin):
    v = e["version"].lstrip("v")
    if (r in ("", "lts") and e["lts"]) or r == "latest" or v == r or v.startswith(r + "."):
        print("v" + v); break
' "$RICHIESTA")
[ -n "$V" ] || {{ echo "Versione di Node.js non trovata: $RICHIESTA"; exit 1; }}
D="$HOME/.local/opt/node-$V"
if [ ! -x "$D/bin/node" ]; then
  mkdir -p "$D" && curl -fsSL "{url}/$V/node-$V-linux-$ARCH_NODE.tar.gz" | tar -xz -C "$D" --strip-components=1
fi
for f in "$D"/bin/*; do ln -sf "$f" "$HOME/.local/bin/"; done
echo "Installato: node $(node --version), npm $(npm --version)"
echo "I pacchetti globali (npm install -g) vanno in ~/.local e restano installati."
'''

_GO = '''V={versione}
if [ -z "$V" ] || [ "$V" = latest ]; then V=$(curl -fsSL "{url}/VERSION?m=text" | head -n 1); fi
case "$V" in go*) ;; *) V="go$V" ;; esac
D="$HOME/.local/opt/$V"
if [ ! -x "$D/bin/go" ]; then
  mkdir -p "$D" && curl -fsSL "{url}/dl/$V.linux-$ARCH_GO.tar.gz" | tar -xz -C "$D" --strip-components=1
fi
for f in "$D"/bin/*; do ln -sf "$f" "$HOME/.local/bin/"; done
echo "Installato: $(go version)"
'''

_PYTHON = '''if [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -fsSL "{url}/uv-$ARCH_UV-unknown-linux-gnu.tar.gz" | tar -xz -C "$HOME/.local/bin" --strip-components=1
fi
V={versione}
[ -n "$V" ] || V=3.12
uv python install "$V"
echo "Installato: Python $V ($(uv python find "$V"))"
echo "Per un ambiente isolato: uv venv -p $V .venv && . .venv/bin/activate && uv pip install <pacchetti>"
'''


@tool(
    "ambiente_installa",
    title="Installa un ambiente di sviluppo",
    description=(
        "Installa Node.js (con npm), Python (tramite uv) o Go nella home persistente dell'agente, scaricando le "
        "versioni ufficiali. Usalo quando servono per eseguire codice o test: nella sandbox non ci sono permessi "
        "di amministratore e apt non funziona. Gli strumenti installati restano disponibili nel terminale per "
        "tutte le conversazioni. versione: es. '22', '20.11.1', 'lts' (Node.js); '3.12' (Python); '1.23' o "
        "'latest' (Go); vuota = versione consigliata. Anche nel terminale puoi installare altro senza root, "
        "per esempio con npm install -g, uv pip in un venv o go install."
    ),
    group="terminal",
)
async def ambiente_installa(strumento: Literal["node", "python", "go"], versione: str = "",
                            id_conversazione: IdConversazione = None) -> str:
    versione = versione.strip()
    if not _RE_VERSIONE.match(versione):
        raise UserError(f"Versione non valida: '{versione}' (es. '22', '3.12', '1.23', 'lts').")
    modello, url = {"node": (_NODE, NODE_DIST_URL), "go": (_GO, GO_URL), "python": (_PYTHON, UV_URL)}[strumento]
    script = "set -euo pipefail\n" + _ARCH + modello.format(versione=shlex.quote(versione), url=url)
    base = cartella_lavoro(id_conversazione)
    codice, uscita, durata = await esegui_comando(script, base, INSTALLAZIONE_TIMEOUT, base)
    if codice != 0:
        raise UserError(f"Installazione di {strumento} non riuscita (codice {codice}):\n{tronca(uscita.strip(), 2000)}")
    return f"{tronca(uscita.strip(), 3000)}\n[{durata:.0f}s · disponibile nel terminale, anche nelle prossime conversazioni]"
