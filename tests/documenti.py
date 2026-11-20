"""Crea i documenti di prova per i test di file_leggi (Word, Excel, PowerPoint, OpenDocument, PDF, ...)."""
import io
import shutil
import subprocess
import tarfile
import zipfile
from datetime import date
from email.message import EmailMessage
from pathlib import Path

FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _immagine_con_testo(testo: str):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1400, 300), "white")
    disegno = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT), 64) if FONT.exists() else ImageFont.load_default()
    disegno.text((40, 100), testo, fill="black", font=font)
    return img


def _converti(soffice: str, sorgente: Path, formato: str, cartella: Path, profilo: Path) -> None:
    subprocess.run([soffice, "--headless", "--norestore", f"-env:UserInstallation=file://{profilo}",
                    "--convert-to", formato, "--outdir", str(cartella), str(sorgente)],
                   capture_output=True, timeout=180)


def crea(cartella: Path, pdf_testo: bytes) -> dict:
    """Ritorna un dizionario con le funzionalita' disponibili (libreoffice, ocr)."""
    cartella.mkdir(parents=True, exist_ok=True)

    import docx
    d = docx.Document()
    d.add_heading("Relazione condominio", level=1)
    d.add_paragraph("Il tetto va riparato entro marzo.")
    d.add_paragraph("Primo punto dell'elenco", style="List Bullet")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Voce", "Importo"
    t.cell(1, 0).text, t.cell(1, 1).text = "Tetto", "12500"
    d.save(cartella / "relazione.docx")

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Spese"
    ws.append(["Data", "Descrizione", "Importo"])
    ws.append([date(2026, 3, 15), "Pulizia scale", 120.5])
    ws.append([date(2026, 4, 1), "Ascensore", 300])
    wb.create_sheet("Note").append(["Pagato tutto"])
    wb.save(cartella / "spese.xlsx")

    from pptx import Presentation
    from pptx.util import Inches
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[1])
    slide.shapes.title.text = "Piano lavori 2027"
    slide.placeholders[1].text = "Rifacimento facciata"
    tab = pres.slides.add_slide(pres.slide_layouts[5])
    tab.shapes.title.text = "Costi"
    tabella = tab.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    tabella.cell(0, 0).text, tabella.cell(0, 1).text = "Lavoro", "Costo"
    tabella.cell(1, 0).text, tabella.cell(1, 1).text = "Facciata", "40000"
    tab.notes_slide.notes_text_frame.text = "Chiedere tre preventivi"
    pres.save(cartella / "lavori.pptx")

    (cartella / "testo.pdf").write_bytes(pdf_testo)
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for pagina in PdfReader(io.BytesIO(pdf_testo)).pages:
        w.add_page(pagina)
    w.encrypt("segreta")
    with open(cartella / "protetto.pdf", "wb") as fh:
        w.write(fh)

    img = _immagine_con_testo("FATTURA NUMERO 4521")
    img.save(cartella / "scansione.pdf")
    img.save(cartella / "scritta.png")

    (cartella / "nota.rtf").write_bytes(rb"{\rtf1\ansi\deff0 Ciao {\b mondo}, \'e8 una nota RTF.\par Seconda riga.}")

    with zipfile.ZipFile(cartella / "libro.epub", "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest><item id="c2" href="cap2.xhtml" media-type="application/xhtml+xml"/><item id="c1" href="cap1.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/cap1.xhtml", "<html><body><h1>Capitolo uno</h1><p>C'era una volta un condominio molto tranquillo e silenzioso.</p></body></html>")
        z.writestr("OEBPS/cap2.xhtml", "<html><body><h1>Capitolo due</h1><p>Poi arrivo' il nuovo amministratore con tante idee.</p></body></html>")

    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "admin@condominio.it", "andrea@example.com", "Convocazione assemblea"
    m.set_content("L'assemblea si terra' il 10 dicembre alle 18.")
    m.add_attachment(b"ordine del giorno", maintype="text", subtype="plain", filename="odg.txt")
    (cartella / "convocazione.eml").write_bytes(bytes(m))

    with zipfile.ZipFile(cartella / "pacchetto.zip", "w") as z:
        z.writestr("dentro/uno.txt", "uno")
        z.writestr("due.txt", "due")
    with tarfile.open(cartella / "pacchetto.tar.gz", "w:gz") as tf:
        tf.add(cartella / "nota.rtf", arcname="nota.rtf")

    (cartella / "rotto.docx").write_bytes(b"questo non e' un docx" * 10)
    (cartella / "pagina.html").write_text("<html><body><p>Sorgente HTML</p></body></html>")
    (cartella / "dati.bin").write_bytes(bytes(range(256)) * 4)

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        profilo = cartella.parent / "lo-profilo-test"
        # il .doc si genera dall'.odt: la conversione diretta docx (python-docx) -> doc perde le tabelle
        for sorgente, formato in (("relazione.docx", "odt"), ("relazione.odt", "doc"), ("spese.xlsx", "xls"),
                                  ("spese.xlsx", "ods"), ("lavori.pptx", "ppt"), ("lavori.pptx", "odp")):
            _converti(soffice, cartella / sorgente, formato, cartella, profilo)
        (cartella / "esportato.xls").write_text("<table><tr><td>Nome</td><td>Quota</td></tr>"
                                                "<tr><td>Rossi</td><td>350</td></tr></table>")
    return {"libreoffice": bool(soffice), "ocr": bool(shutil.which("tesseract")) and FONT.exists()}
