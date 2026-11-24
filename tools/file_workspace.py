"""Strumenti: lettura (anche documenti), scrittura, modifica, elenco e ricerca dei file del workspace."""
import asyncio
import contextlib
import re

from nucleo.configurazione import WORKSPACE_DIR
from nucleo.server import UserError, tool
from nucleo.utilita import tronca
from nucleo.workspace import IdConversazione, percorso_workspace, cartella_lavoro, relativo
from tools.lettura_documenti import _NOMI_TIPI, estrai_testo_documento, tipo_documento


@tool(
    "file_leggi",
    title="Leggi file",
    description=(
        "Legge un file del workspace con numeri di riga. Oltre ai file di testo e al codice estrae il testo da "
        "documenti: PDF (con OCR per quelli scansionati, se disponibile), Word (.docx, .doc), Excel (.xlsx, .xls), "
        "PowerPoint (.pptx, .ppt), LibreOffice (.odt, .ods, .odp), RTF, EPUB, email (.eml), immagini (OCR) e "
        "archivi zip/tar (elenco dei file). Per file lunghi usa da_riga e num_righe."
        " Facoltativo: id_conversazione per lavorare nella cartella della conversazione."
    ),
    group="terminal",
    read_only=True,
)
async def file_leggi(percorso: str, da_riga: int = 1, num_righe: int = 400,
                     id_conversazione: IdConversazione = None) -> str:
    p = percorso_workspace(percorso, cartella_lavoro(id_conversazione))
    if not p.is_file():
        raise UserError(f"File non trovato: {percorso}")
    if p.stat().st_size > 50 * 1024 * 1024:
        raise UserError("File troppo grande (>50MB): usa terminale_esegui con head/tail/grep.")
    with open(p, "rb") as fh:
        inizio_file = fh.read(8192)
    tipo = tipo_documento(p, inizio_file)
    intestazione = ""
    if tipo:
        testo = await asyncio.to_thread(estrai_testo_documento, p, tipo)
        intestazione = f"[{_NOMI_TIPI[tipo].capitalize()}: {p.name} — testo estratto]\n"
        if not testo.strip():
            return intestazione + "Nessun testo estraibile."
        righe = testo.splitlines()
    elif b"\x00" in inizio_file:
        return (f"{percorso}: file binario di {p.stat().st_size} byte in un formato non supportato "
                f"(per le immagini usa vedi_immagine).")
    else:
        righe = p.read_bytes().decode("utf-8", "replace").splitlines()
    inizio = max(1, int(da_riga or 1))
    num = max(1, min(int(num_righe or 400), 2000))
    sel = righe[inizio - 1: inizio - 1 + num]
    corpo = "\n".join(f"{i:>5}  {r[:1000]}" for i, r in enumerate(sel, inizio))
    fine = inizio + len(sel) - 1
    nota = f"\n[righe {inizio}-{fine} di {len(righe)}]" + (
        f" — continua con da_riga={fine + 1}" if fine < len(righe) else "")
    return intestazione + tronca(corpo, 60000) + nota


@tool(
    "file_scrivi",
    title="Scrivi file",
    description="Crea o sovrascrive un file di testo nel workspace (crea le cartelle mancanti). Con aggiungi=true accoda al file esistente." + " Facoltativo: id_conversazione per lavorare nella cartella della conversazione.",
    group="terminal",
    destructive=True,
)
async def file_scrivi(percorso: str, contenuto: str, aggiungi: bool = False,
                      id_conversazione: IdConversazione = None) -> str:
    base = cartella_lavoro(id_conversazione)
    p = percorso_workspace(percorso, base)
    if p.is_dir():
        raise UserError(f"'{percorso}' e' una cartella.")
    p.parent.mkdir(parents=True, exist_ok=True)
    esisteva = p.exists()
    with open(p, "a" if aggiungi else "w", encoding="utf-8") as fh:
        fh.write(contenuto)
    azione = "Aggiunto a" if aggiungi else ("Sovrascritto" if esisteva else "Creato")
    return f"{azione} {relativo(p, base)} ({len(contenuto)} caratteri, {contenuto.count(chr(10)) + 1} righe)."


@tool(
    "file_modifica",
    title="Modifica file",
    description=(
        "Modifica un file del workspace sostituendo testo_vecchio (che deve comparire esattamente, una sola volta, "
        "spazi inclusi) con testo_nuovo. Con tutte_le_occorrenze=true sostituisce ogni occorrenza. "
        "Leggi prima il file con file_leggi."
        " Facoltativo: id_conversazione per lavorare nella cartella della conversazione."
    ),
    group="terminal",
    destructive=True,
)
async def file_modifica(percorso: str, testo_vecchio: str, testo_nuovo: str,
                        tutte_le_occorrenze: bool = False, id_conversazione: IdConversazione = None) -> str:
    base = cartella_lavoro(id_conversazione)
    p = percorso_workspace(percorso, base)
    if not p.is_file():
        raise UserError(f"File non trovato: {percorso}")
    if not testo_vecchio:
        raise UserError("testo_vecchio non puo' essere vuoto.")
    contenuto = p.read_text(encoding="utf-8")
    n = contenuto.count(testo_vecchio)
    if n == 0:
        raise UserError("testo_vecchio non trovato nel file (controlla spazi e a capo; rileggi con file_leggi).")
    if n > 1 and not tutte_le_occorrenze:
        raise UserError(f"testo_vecchio compare {n} volte: aggiungi contesto per renderlo unico o usa tutte_le_occorrenze=true.")
    nuovo = contenuto.replace(testo_vecchio, testo_nuovo) if tutte_le_occorrenze \
        else contenuto.replace(testo_vecchio, testo_nuovo, 1)
    p.write_text(nuovo, encoding="utf-8")
    riga = contenuto[: contenuto.index(testo_vecchio)].count("\n") + 1
    return f"Modificato {relativo(p, base)}: {n if tutte_le_occorrenze else 1} sostituzione/i (prima alla riga {riga})."


@tool(
    "file_elenca",
    title="Elenca file",
    description="Elenca file e cartelle del workspace. 'pattern' e' un glob (es. '*.py', '**/*.md' per ricorsivo)." + " Facoltativo: id_conversazione per lavorare nella cartella della conversazione.",
    group="terminal",
    read_only=True,
)
async def file_elenca(percorso: str = ".", pattern: str = "*", id_conversazione: IdConversazione = None) -> str:
    radice = cartella_lavoro(id_conversazione)
    base = percorso_workspace(percorso, radice)
    if not base.is_dir():
        raise UserError(f"Cartella non trovata: {percorso}")
    voci = []
    for p in sorted(base.glob(pattern or "*")):
        if any(part in (".git", "__pycache__", "node_modules", ".tmp") for part in p.relative_to(base).parts[:-1]):
            continue
        rel = relativo(p, radice)
        if p.is_dir():
            voci.append(f"📁 {rel}/")
        else:
            with contextlib.suppress(OSError):
                voci.append(f"   {rel}  ({p.stat().st_size / 1024:.1f} KB)")
        if len(voci) >= 500:
            voci.append("[... elenco troncato a 500 voci ...]")
            break
    nome_radice = "Workspace" if radice == WORKSPACE_DIR else f"Cartella della conversazione {radice.name}"
    return f"{nome_radice}/{relativo(base, radice)} — {len(voci)} voci:\n" + ("\n".join(voci) or "(vuota)")


@tool(
    "file_cerca",
    title="Cerca nei file",
    description="Cerca un'espressione regolare nel contenuto dei file del workspace (come grep -rn). 'filtro_file' e' un glob (es. '*.py')." + " Facoltativo: id_conversazione per lavorare nella cartella della conversazione.",
    group="terminal",
    read_only=True,
)
async def file_cerca(regex: str, percorso: str = ".", filtro_file: str = "*",
                     ignora_maiuscole: bool = False, id_conversazione: IdConversazione = None) -> str:
    radice = cartella_lavoro(id_conversazione)
    base = percorso_workspace(percorso, radice)
    try:
        rx = re.compile(regex, re.I if ignora_maiuscole else 0)
    except re.error as e:
        raise UserError(f"Regex non valida: {e}")

    def _cerca() -> list[str]:
        trovati = []
        files = [base] if base.is_file() else base.rglob(filtro_file or "*")
        for f in files:
            if not f.is_file() or any(x in f.parts for x in (".git", "node_modules", "__pycache__")):
                continue
            with contextlib.suppress(OSError):
                if f.stat().st_size > 5 * 1024 * 1024:
                    continue
                with open(f, "r", encoding="utf-8", errors="strict") as fh:
                    try:
                        for n, riga in enumerate(fh, 1):
                            if rx.search(riga):
                                trovati.append(f"{relativo(f, radice)}:{n}: {riga.rstrip()[:300]}")
                                if len(trovati) >= 200:
                                    return trovati
                    except UnicodeDecodeError:
                        continue
        return trovati

    trovati = await asyncio.to_thread(_cerca)
    if not trovati:
        return f"Nessuna corrispondenza per /{regex}/."
    return f"{len(trovati)} corrispondenze{' (limite 200)' if len(trovati) >= 200 else ''}:\n" + "\n".join(trovati)
