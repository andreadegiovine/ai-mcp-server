"""Estrazione del testo dai documenti (PDF, Office, OpenDocument, RTF, EPUB, email, OCR)."""
import contextlib
import email
import email.policy
import functools
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from nucleo.configurazione import _env, _env_int, DATA_DIR, logger
from nucleo.server import UserError
from nucleo.workspace import env_sicuro

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None


# OCR (tesseract) per PDF scansionati e immagini, LibreOffice per i vecchi formati Office
OCR_LINGUE = _env("OCR_LINGUE", "ita+eng")
OCR_MAX_PAGINE = _env_int("OCR_MAX_PAGINE", 30)


# --- Estrazione del testo dai documenti (usata da file_leggi) ------------------
_TIPI_DOCUMENTO = {
    ".pdf": "pdf",
    ".docx": "docx", ".docm": "docx", ".dotx": "docx", ".dotm": "docx",
    ".xlsx": "xlsx", ".xlsm": "xlsx", ".xltx": "xlsx", ".xltm": "xlsx",
    ".xls": "xls",
    ".pptx": "pptx", ".pptm": "pptx", ".ppsx": "pptx", ".potx": "pptx",
    ".odt": "odf", ".ott": "odf", ".ods": "odf", ".ots": "odf", ".odp": "odf", ".otp": "odf",
    ".rtf": "rtf", ".epub": "epub", ".eml": "eml",
    ".doc": "office_vecchio", ".dot": "office_vecchio", ".ppt": "office_vecchio", ".pps": "office_vecchio",
    ".wpd": "office_vecchio", ".wps": "office_vecchio", ".pub": "office_vecchio",
    ".png": "immagine", ".jpg": "immagine", ".jpeg": "immagine", ".gif": "immagine", ".bmp": "immagine",
    ".tif": "immagine", ".tiff": "immagine", ".webp": "immagine",
    ".zip": "archivio", ".tar": "archivio", ".gz": "archivio", ".tgz": "archivio", ".bz2": "archivio",
    ".xz": "archivio",
}
_NOMI_TIPI = {
    "pdf": "PDF", "docx": "documento Word", "xlsx": "foglio Excel", "xls": "foglio Excel (formato vecchio)",
    "pptx": "presentazione PowerPoint", "odf": "documento OpenDocument (LibreOffice)", "rtf": "documento RTF",
    "epub": "libro EPUB", "eml": "email", "office_vecchio": "documento Office (formato vecchio)",
    "immagine": "immagine", "archivio": "archivio",
}
_MAX_TESTO_DOCUMENTO = 5_000_000
_MAX_ZIP_MEMBRO = 100 * 1024 * 1024
_cache_documenti: dict[tuple, str] = {}


def tipo_documento(p: Path, inizio: bytes) -> Optional[str]:
    """Tipo di documento da convertire in testo; None = file di testo da leggere cosi' com'e'."""
    if inizio.startswith(b"%PDF"):
        return "pdf"
    if inizio.startswith(b"{\\rtf"):
        return "rtf"
    nome = p.name.lower()
    if nome.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return "archivio"
    return _TIPI_DOCUMENTO.get(p.suffix.lower())


def _leggi_zip(zf: zipfile.ZipFile, nome: str) -> bytes:
    info = zf.getinfo(nome)
    if info.file_size > _MAX_ZIP_MEMBRO:
        raise UserError(f"Contenuto compresso troppo grande ({info.file_size / 1e6:.0f} MB).")
    return zf.read(nome)


def _cella(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y %H:%M") if (v.hour or v.minute) else v.strftime("%d/%m/%Y")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).replace("\n", " ").strip()


def _riga_tabella(valori: list) -> Optional[str]:
    celle = [_cella(v) for v in valori]
    while celle and not celle[-1]:
        celle.pop()
    return " | ".join(celle) if any(celle) else None


def _html_a_testo(html: str) -> str:
    testo = None
    if trafilatura is not None:
        with contextlib.suppress(Exception):
            testo = trafilatura.extract(html, include_tables=True, include_comments=False,
                                        output_format="markdown", favor_recall=True)
    if not testo:
        testo = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html)
        testo = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|li|tr)>", "\n", testo)
        testo = re.sub(r"<[^>]+>", " ", testo)
        import html as html_mod
        testo = html_mod.unescape(testo)
        testo = "\n".join(re.sub(r"[ \t]+", " ", r).strip() for r in testo.splitlines())
    return re.sub(r"\n{3,}", "\n\n", testo).strip()


@functools.lru_cache(maxsize=1)
def _lingue_ocr() -> str:
    """Lingue di OCR_LINGUE effettivamente installate per tesseract (es. 'ita+eng')."""
    try:
        uscita = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return "eng"
    disponibili = {r.strip() for r in uscita.splitlines()[1:]}
    valide = [lingua for lingua in OCR_LINGUE.split("+") if lingua in disponibili]
    if len(valide) < len(OCR_LINGUE.split("+")):
        logger.warning(f"Lingue OCR mancanti ({OCR_LINGUE}); disponibili: {', '.join(sorted(disponibili))}")
    return "+".join(valide) or "eng"


def _ocr_immagine(percorso_immagine: Path) -> str:
    r = subprocess.run(["tesseract", str(percorso_immagine), "stdout", "-l", _lingue_ocr()],
                       capture_output=True, text=True, timeout=120)
    return r.stdout.strip()


def _comando_installa_ocr() -> str:
    return "apt install -y tesseract-ocr tesseract-ocr-ita"


def _pdf_testo(p: Path) -> str:
    if PdfReader is None:
        raise UserError("pypdf non installato: impossibile leggere PDF.")
    reader = PdfReader(str(p))
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise UserError("Il PDF e' protetto da password.")
        except UserError:
            raise
        except Exception:
            raise UserError("Il PDF e' protetto da password o cifrato in un formato non supportato.")
    pagine: list[str] = []
    vuote: list[int] = []
    for i, pagina in enumerate(reader.pages, 1):
        try:
            t = (pagina.extract_text() or "").strip()
        except Exception:
            t = ""
        pagine.append(t)
        if len(t) < 20:
            vuote.append(i)
    note = ""
    if vuote and len(vuote) >= max(1, len(pagine) // 2):
        if shutil.which("tesseract"):
            ocr = _pdf_ocr(p, vuote[:OCR_MAX_PAGINE])
            for i, t in ocr.items():
                pagine[i - 1] = f"[testo riconosciuto con OCR]\n{t}" if t else pagine[i - 1]
            if len(vuote) > OCR_MAX_PAGINE:
                note = f"\n[OCR limitato alle prime {OCR_MAX_PAGINE} pagine senza testo (OCR_MAX_PAGINE)]"
        else:
            note = (f"\n[{len(vuote)} pagine senza testo: probabilmente e' un PDF scansionato. Per leggerlo serve "
                    f"l'OCR, da installare sul server: {_comando_installa_ocr()}]")
    corpo = "\n\n".join(f"--- Pagina {i} ---\n{t}" for i, t in enumerate(pagine, 1) if t)
    return f"{len(pagine)} pagine\n\n{corpo}{note}".strip()


def _pdf_ocr(p: Path, numeri: list[int]) -> dict[int, str]:
    risultati: dict[int, str] = {}
    with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
        for n in numeri:
            immagine = Path(tmp) / f"p{n}.png"
            try:
                import pypdfium2 as pdfium
                doc = pdfium.PdfDocument(str(p))
                try:
                    doc[n - 1].render(scale=2.5).to_pil().save(immagine)
                finally:
                    doc.close()
            except ImportError:
                if not shutil.which("pdftoppm"):
                    raise UserError("Per l'OCR dei PDF serve pypdfium2 (pip) oppure pdftoppm (apt install poppler-utils).")
                subprocess.run(["pdftoppm", "-f", str(n), "-l", str(n), "-r", "200", "-png", "-singlefile",
                                str(p), str(immagine.with_suffix(""))], capture_output=True, timeout=120)
            if immagine.exists():
                risultati[n] = _ocr_immagine(immagine)
    return risultati


def _docx_testo(p: Path) -> str:
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        raise UserError("Libreria python-docx non installata (pip install python-docx).")
    documento = docx.Document(str(p))
    righe: list[str] = []
    for elemento in documento.element.body.iterchildren():
        tag = elemento.tag.rsplit("}", 1)[-1]
        if tag == "p":
            par = Paragraph(elemento, documento)
            testo = par.text.strip()
            if not testo:
                righe.append("")
                continue
            stile = (par.style.name if par.style is not None else "") or ""
            m = re.match(r"(?:Heading|Titolo)\s*(\d)", stile, re.I)
            if m:
                testo = "#" * int(m.group(1)) + " " + testo
            elif stile.lower() in ("title", "titolo"):
                testo = "# " + testo
            elif "list" in stile.lower() or "elenco" in stile.lower():
                testo = "- " + testo
            righe.append(testo)
        elif tag == "tbl":
            righe.append("")
            for riga in Table(elemento, documento).rows:
                r = _riga_tabella([c.text for c in riga.cells])
                if r:
                    righe.append(r)
            righe.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(righe)).strip()


def _xlsx_testo(p: Path) -> str:
    try:
        import openpyxl
    except ImportError:
        raise UserError("Libreria openpyxl non installata (pip install openpyxl).")
    wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    parti = []
    try:
        for ws in wb.worksheets:
            if not hasattr(ws, "iter_rows"):
                continue
            righe = []
            for valori in ws.iter_rows(values_only=True):
                r = _riga_tabella(list(valori))
                if r:
                    righe.append(r)
                if len(righe) >= 20000:
                    righe.append("[... foglio troncato a 20000 righe ...]")
                    break
            parti.append(f"## Foglio: {ws.title} ({len(righe)} righe)\n" + ("\n".join(righe) or "(vuoto)"))
    finally:
        wb.close()
    return "\n\n".join(parti)


def _xls_testo(p: Path) -> str:
    try:
        import xlrd
    except ImportError:
        raise UserError("Libreria xlrd non installata (pip install xlrd).")
    try:
        libro = xlrd.open_workbook(str(p), on_demand=True)
    except Exception:
        # molti ".xls" esportati dai gestionali sono in realta' HTML o XML: li converte LibreOffice
        if shutil.which("soffice") or shutil.which("libreoffice"):
            return _office_vecchio_testo(p)
        raise UserError("Il file .xls non e' un foglio Excel valido (forse e' HTML o XML): prova a leggerlo "
                        "come testo con terminale_esegui, oppure installa LibreOffice sul server.")
    parti = []
    for foglio in libro.sheets():
        righe = []
        for i in range(min(foglio.nrows, 20000)):
            valori = []
            for cella in foglio.row(i):
                v = cella.value
                if cella.ctype == xlrd.XL_CELL_DATE:
                    with contextlib.suppress(Exception):
                        v = xlrd.xldate_as_datetime(v, libro.datemode)
                valori.append(v)
            r = _riga_tabella(valori)
            if r:
                righe.append(r)
        parti.append(f"## Foglio: {foglio.name} ({len(righe)} righe)\n" + ("\n".join(righe) or "(vuoto)"))
    return "\n\n".join(parti)


def _pptx_testo(p: Path) -> str:
    try:
        from pptx import Presentation
    except ImportError:
        raise UserError("Libreria python-pptx non installata (pip install python-pptx).")
    pres = Presentation(str(p))
    parti = []

    def testi_forma(forma) -> list[str]:
        out = []
        if getattr(forma, "has_table", False) and forma.has_table:
            for riga in forma.table.rows:
                r = _riga_tabella([c.text for c in riga.cells])
                if r:
                    out.append(r)
        elif getattr(forma, "has_text_frame", False) and forma.has_text_frame:
            t = forma.text_frame.text.strip()
            if t:
                out.append(t)
        for sotto in getattr(forma, "shapes", []) or []:
            out.extend(testi_forma(sotto))
        return out

    for n, slide in enumerate(pres.slides, 1):
        righe = []
        for forma in slide.shapes:
            righe.extend(testi_forma(forma))
        if slide.has_notes_slide:
            note = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if note:
                righe.append(f"[Note: {note}]")
        parti.append(f"--- Slide {n} ---\n" + ("\n".join(righe) or "(nessun testo)"))
    return f"{len(parti)} slide\n\n" + "\n\n".join(parti)


_NS_ODF_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_NS_ODF_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_NS_ODF_DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"


def _odf_inline(el) -> str:
    parti = [el.text or ""]
    for figlio in el:
        tag = figlio.tag
        if tag == f"{{{_NS_ODF_TEXT}}}s":
            parti.append(" " * int(figlio.get(f"{{{_NS_ODF_TEXT}}}c", "1")))
        elif tag == f"{{{_NS_ODF_TEXT}}}tab":
            parti.append("\t")
        elif tag == f"{{{_NS_ODF_TEXT}}}line-break":
            parti.append("\n")
        elif tag in (f"{{{_NS_ODF_TEXT}}}note", "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}annotation"):
            pass
        else:
            parti.append(_odf_inline(figlio))
        parti.append(figlio.tail or "")
    return "".join(parti)


def _odf_righe(el, contatore: list[int]):
    tag = el.tag
    if tag in (f"{{{_NS_ODF_TEXT}}}p", f"{{{_NS_ODF_TEXT}}}h"):
        testo = _odf_inline(el).strip()
        if tag.endswith("}h") and testo:
            testo = "#" * int(el.get(f"{{{_NS_ODF_TEXT}}}outline-level", "1")) + " " + testo
        yield testo
        return
    if tag == f"{{{_NS_ODF_TABLE}}}table":
        nome = el.get(f"{{{_NS_ODF_TABLE}}}name", "")
        yield f"## Tabella: {nome}" if nome else ""
        for riga in el.iter(f"{{{_NS_ODF_TABLE}}}table-row"):
            valori = []
            for cella in riga:
                if cella.tag not in (f"{{{_NS_ODF_TABLE}}}table-cell", f"{{{_NS_ODF_TABLE}}}covered-table-cell"):
                    continue
                testo = "\n".join(_odf_inline(par).strip() for par in cella.iter(f"{{{_NS_ODF_TEXT}}}p"))
                ripetizioni = int(cella.get(f"{{{_NS_ODF_TABLE}}}number-columns-repeated", "1"))
                valori.extend([testo] * (min(ripetizioni, 50) if testo else 1))
            r = _riga_tabella(valori)
            if r:
                yield r
        yield ""
        return
    if tag == f"{{{_NS_ODF_DRAW}}}page":
        contatore[0] += 1
        yield f"--- Slide {contatore[0]} ---"
    for figlio in el:
        yield from _odf_righe(figlio, contatore)


def _odf_testo(p: Path) -> str:
    with zipfile.ZipFile(p) as zf:
        radice = ET.fromstring(_leggi_zip(zf, "content.xml"))
    corpo = radice.find("{urn:oasis:names:tc:opendocument:xmlns:office:1.0}body")
    if corpo is None:
        return ""
    testo = "\n".join(_odf_righe(corpo, [0]))
    return re.sub(r"\n{3,}", "\n\n", testo).strip()


def _rtf_testo(p: Path) -> str:
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError:
        raise UserError("Libreria striprtf non installata (pip install striprtf).")
    return rtf_to_text(p.read_text("latin-1"), errors="ignore").strip()


def _epub_testo(p: Path) -> str:
    with zipfile.ZipFile(p) as zf:
        ordine: list[str] = []
        with contextlib.suppress(Exception):
            container = ET.fromstring(_leggi_zip(zf, "META-INF/container.xml"))
            opf_path = next(e.get("full-path") for e in container.iter() if e.tag.endswith("rootfile"))
            opf = ET.fromstring(_leggi_zip(zf, opf_path))
            base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
            manifest = {e.get("id"): e.get("href") for e in opf.iter() if e.tag.endswith("}item")}
            ordine = [base + manifest[e.get("idref")] for e in opf.iter()
                      if e.tag.endswith("}itemref") and e.get("idref") in manifest]
        if not ordine:
            ordine = sorted(n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))
        capitoli = []
        for n, nome in enumerate(ordine, 1):
            with contextlib.suppress(KeyError):
                testo = _html_a_testo(_leggi_zip(zf, nome).decode("utf-8", "replace"))
                if testo:
                    capitoli.append(f"--- Sezione {n} ---\n{testo}")
    return "\n\n".join(capitoli)


def _eml_testo(p: Path) -> str:
    msg = email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
    righe = [f"{k}: {msg[k]}" for k in ("From", "To", "Cc", "Date", "Subject") if msg[k]]
    corpo = msg.get_body(preferencelist=("plain", "html"))
    if corpo is not None:
        contenuto = corpo.get_content()
        if corpo.get_content_type() == "text/html":
            contenuto = _html_a_testo(contenuto)
        righe += ["", contenuto.strip()]
    allegati = [a.get_filename() or "(senza nome)" for a in msg.iter_attachments()]
    if allegati:
        righe += ["", "Allegati: " + ", ".join(allegati)]
    return "\n".join(righe)


def _immagine_testo(p: Path) -> str:
    if not shutil.which("tesseract"):
        raise UserError("E' un'immagine: usa vedi_immagine per guardarla. Per estrarne il testo con l'OCR, "
                        f"installa sul server: {_comando_installa_ocr()}")
    testo = _ocr_immagine(p)
    return ("[testo riconosciuto con OCR; per vedere l'immagine usa vedi_immagine]\n\n" + testo) if testo else \
        "Nessun testo riconosciuto nell'immagine (per guardarla usa vedi_immagine)."


def _archivio_testo(p: Path) -> str:
    righe = []
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as zf:
            voci = zf.infolist()
            righe = [f"{i.filename}  ({i.file_size / 1024:.1f} KB)" for i in voci[:500]]
            totale = len(voci)
    else:
        try:
            with tarfile.open(p) as tf:
                voci = tf.getmembers()
                righe = [f"{m.name}  ({m.size / 1024:.1f} KB)" for m in voci[:500]]
                totale = len(voci)
        except tarfile.TarError:
            raise UserError("Archivio non riconosciuto (supportati zip e tar, anche compressi).")
    extra = f"\n[... altre {totale - 500} voci ...]" if totale > 500 else ""
    return (f"Archivio con {totale} file (per estrarlo usa terminale_esegui, es. unzip o tar -xf):\n"
            + "\n".join(righe) + extra)


_LOCK_LIBREOFFICE = threading.Lock()


def _office_vecchio_testo(p: Path) -> str:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    estensione = p.suffix.lower()
    destinazione = {".ppt": "pptx", ".pps": "pptx", ".xls": "xlsx"}.get(estensione, "docx")
    if not soffice:
        pacchetto = "libreoffice-impress-nogui" if destinazione == "pptx" else "libreoffice-writer-nogui"
        raise UserError(f"Per leggere i file {estensione} serve LibreOffice sul server: "
                        f"apt install -y --no-install-recommends {pacchetto}")
    profilo = DATA_DIR / ".libreoffice"
    profilo.mkdir(parents=True, exist_ok=True)
    # un solo LibreOffice alla volta: due istanze con lo stesso profilo si bloccano a vicenda
    with _LOCK_LIBREOFFICE, tempfile.TemporaryDirectory(prefix="conv-") as tmp:
        try:
            subprocess.run([soffice, "--headless", "--norestore", "--nologo",
                            f"-env:UserInstallation=file://{profilo}", "--convert-to", destinazione,
                            "--outdir", tmp, str(p)],
                           capture_output=True, timeout=180, env=env_sicuro())
        except subprocess.TimeoutExpired:
            raise UserError("La conversione con LibreOffice ha impiegato troppo tempo.")
        convertiti = list(Path(tmp).glob(f"*.{destinazione}"))
        if not convertiti:
            raise UserError(f"LibreOffice non e' riuscito a convertire {p.name}.")
        return {"docx": _docx_testo, "pptx": _pptx_testo, "xlsx": _xlsx_testo}[destinazione](convertiti[0])


_ESTRATTORI = {
    "pdf": _pdf_testo, "docx": _docx_testo, "xlsx": _xlsx_testo, "xls": _xls_testo, "pptx": _pptx_testo,
    "odf": _odf_testo, "rtf": _rtf_testo, "epub": _epub_testo, "eml": _eml_testo, "immagine": _immagine_testo,
    "archivio": _archivio_testo, "office_vecchio": _office_vecchio_testo,
}


def estrai_testo_documento(p: Path, tipo: str) -> str:
    stat = p.stat()
    chiave = (str(p), stat.st_mtime_ns, stat.st_size)
    if chiave in _cache_documenti:
        return _cache_documenti[chiave]
    if tipo in ("docx", "xlsx", "pptx", "odf", "epub") and not zipfile.is_zipfile(p):
        raise UserError(f"File {_NOMI_TIPI[tipo]} danneggiato o non valido: {p.name} non ha la struttura attesa.")
    try:
        testo = _ESTRATTORI[tipo](p)
    except UserError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, KeyError) as e:
        raise UserError(f"File {_NOMI_TIPI.get(tipo, tipo)} danneggiato o non valido: {type(e).__name__}")
    except Exception as e:
        logger.warning(f"Estrazione testo da {p.name} fallita: {e}")
        raise UserError(f"Impossibile leggere {p.name} come {_NOMI_TIPI.get(tipo, tipo)}: {type(e).__name__}: {e}")
    if len(testo) > _MAX_TESTO_DOCUMENTO:
        testo = testo[:_MAX_TESTO_DOCUMENTO] + "\n[... testo troncato ...]"
    if len(_cache_documenti) >= 10:
        _cache_documenti.pop(next(iter(_cache_documenti)))
    _cache_documenti[chiave] = testo
    return testo
