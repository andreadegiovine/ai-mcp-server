"""Utilità comuni: date e durate in italiano, formattazione, archivio JSON."""

import contextlib
import json
import os
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from nucleo.configurazione import TZ, logger
from nucleo.server import UserError

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
GIORNI_BREVI = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
MESI = [
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
]


def now() -> datetime:
    return datetime.now(TZ)


def tronca(testo: str, limite: int, coda: int = 0) -> str:
    """Tronca mantenendo inizio (e opzionalmente fine) del testo."""
    if len(testo) <= limite:
        return testo
    if coda:
        testa = limite - coda
        omessi = len(testo) - testa - coda
        return f"{testo[:testa]}\n\n[... {omessi} caratteri omessi ...]\n\n{testo[-coda:]}"
    return testo[:limite] + f"\n[... troncato, {len(testo) - limite} caratteri omessi ...]"


def senza_accenti(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def normalizza(s: str) -> str:
    """minuscolo, senza accenti, separatori -> spazio."""
    return re.sub(r"[\s_.\-/]+", " ", senza_accenti(s)).strip()


def fmt_durata(secondi: float) -> str:
    secondi = int(round(max(0, secondi)))
    g, r = divmod(secondi, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parti = []
    if g:
        parti.append(f"{g} g")
    if h:
        parti.append(f"{h} h")
    if m:
        parti.append(f"{m} min")
    if s or not parti:
        parti.append(f"{s} s")
    return " ".join(parti)


def fmt_dt(dt: datetime, con_giorno: bool = True) -> str:
    dt = dt.astimezone(TZ)
    base = dt.strftime("%d/%m/%Y %H:%M")
    return f"{GIORNI[dt.weekday()]} {base}" if con_giorno else base


def fa_quanto(dt: datetime) -> str:
    delta = (now() - dt).total_seconds()
    if delta < 0:
        return f"tra {fmt_durata(-delta)}"
    if delta < 60:
        return "pochi secondi fa"
    return f"{fmt_durata(delta - delta % 60)} fa"


class JsonStore:
    """Piccolo archivio JSON con scrittura atomica e thread-safe."""

    def __init__(self, path: Path, default: Any):
        self.path = path
        self.default = default
        self.lock = threading.RLock()

    def load(self) -> Any:
        with self.lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return json.loads(json.dumps(self.default))
            except Exception as e:
                logger.error(f"File {self.path} corrotto ({e}): ne salvo una copia e riparto vuoto")
                with contextlib.suppress(Exception):
                    self.path.rename(self.path.with_suffix(f".corrotto-{int(time.time())}"))
                return json.loads(json.dumps(self.default))

    def save(self, data: Any) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)


# --- Parsing di date/ore e durate in italiano --------------------------------
_RE_ORA = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?(?:[:.](\d{2}))?$")


def _parse_ora(s: str) -> tuple[int, int, int] | None:
    s = s.strip().lower().replace("alle ", "").replace("ore ", "").strip()
    m = _RE_ORA.match(s)
    if not m:
        return None
    h, mi, se = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
    if h > 23 or mi > 59 or se > 59:
        return None
    return h, mi, se


def parse_data_ora(testo: str, *, riferimento: datetime | None = None) -> tuple[datetime, bool]:
    """
    Interpreta date/ore in formati comuni. Ritorna (datetime aware, ha_orario).
    Esempi: "2026-11-18 15:30", "2026-11-18T15:30:00+01:00", "18/11/2026 15:30", "18/11",
            "oggi", "domani 9:00", "dopodomani alle 18", "venerdì 20:30", "15:00".
    """
    if not testo or not str(testo).strip():
        raise UserError("Data/ora mancante.")
    rif = (riferimento or now()).astimezone(TZ)
    s = str(testo).strip()

    # ISO 8601
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        ha_ora = bool(re.search(r"\d[T ]\d", s))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ), ha_ora
    except ValueError:
        pass

    low = senza_accenti(s).replace(",", " ").replace("'", " ")
    low = re.sub(r"\b(alle|all|ore|dalle|h)\b", " ", low)
    low = re.sub(r"\s+", " ", low).strip()

    giorno: date | None = None
    resto = low
    relativi = {"oggi": 0, "stasera": 0, "stanotte": 0, "domani": 1, "dopodomani": 2, "ieri": -1}
    for parola, off in sorted(relativi.items(), key=lambda x: -len(x[0])):
        if resto.startswith(parola):
            giorno = rif.date() + timedelta(days=off)
            resto = resto[len(parola) :].strip()
            break
    if giorno is None:
        nomi = [normalizza(g) for g in GIORNI]
        for i, nome in enumerate(nomi):
            m = re.match(rf"^(prossimo |prossima )?{nome[:3]}[a-z]*\b", resto)
            if m:
                diff = (i - rif.weekday()) % 7
                if diff == 0 and m.group(1):
                    diff = 7
                giorno = rif.date() + timedelta(days=diff)
                resto = resto[m.end() :].strip()
                break
    if giorno is None:
        m = re.match(r"^(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b", resto)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else rif.year
            if y < 100:
                y += 2000
            try:
                giorno = date(y, mo, d)
            except ValueError as e:
                raise UserError(f"Data non valida '{testo}': {e}")
            if not m.group(3) and giorno < rif.date() - timedelta(days=1):
                giorno = giorno.replace(year=y + 1)
            resto = resto[m.end() :].strip()

    ora = _parse_ora(resto) if resto else None
    if resto and ora is None:
        raise UserError(
            f"Non riesco a interpretare '{testo}'. Usa ad es. '2026-11-18 15:30', '18/11/2026 15:30', 'domani 9:00'."
        )
    if giorno is None and ora is None:
        raise UserError(f"Non riesco a interpretare '{testo}'.")
    if giorno is None:
        giorno = rif.date()
    h, mi, se = ora if ora else (0, 0, 0)
    return datetime(giorno.year, giorno.month, giorno.day, h, mi, se, tzinfo=TZ), ora is not None


_RE_DURATA = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(giorni|giorno|gg|g|d|ore|ora|hours|hour|hrs|hr|h|minuti|minuto|mins|min|m|"
    r"secondi|secondo|secs|sec|s)(?![a-z])",
    re.I,
)
_UNITA = {
    "giorni": 86400,
    "giorno": 86400,
    "gg": 86400,
    "g": 86400,
    "d": 86400,
    "ore": 3600,
    "ora": 3600,
    "hours": 3600,
    "hour": 3600,
    "hrs": 3600,
    "hr": 3600,
    "h": 3600,
    "minuti": 60,
    "minuto": 60,
    "mins": 60,
    "min": 60,
    "m": 60,
    "secondi": 1,
    "secondo": 1,
    "secs": 1,
    "sec": 1,
    "s": 1,
}


def parse_durata(testo: str) -> int:
    """'10m', '1h30m', '1 ora e 15 minuti', '90s', '00:05:00', 'mezz'ora', '25' (=minuti) -> secondi."""
    s = re.sub(r"\s+", " ", senza_accenti(testo).replace("'", " ")).strip()
    s = s.replace("mezz ora", "30 minuti").replace("mezzora", "30 minuti").replace("un quarto d ora", "15 minuti")
    s = re.sub(r"\b(un|una) (ora|minuto|secondo|giorno)\b", r"1 \2", s)
    s = re.sub(r"\bmezzo\b", "", s).strip()
    if re.fullmatch(r"\d+(?:[.,]\d+)?", s):
        return int(float(s.replace(",", ".")) * 60)
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", s)
    if m:
        if m.group(3) is not None:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        return int(m.group(1)) * 60 + int(m.group(2))
    totale = 0.0
    for num, unita in _RE_DURATA.findall(s):
        totale += float(num.replace(",", ".")) * _UNITA[unita.lower()]
    resto = _RE_DURATA.sub("", s)
    resto = re.sub(r"\b(e|and|per|di|tra|fra)\b|[,+]", "", resto).strip()
    if totale <= 0 or resto:
        raise UserError(f"Durata non valida: '{testo}'. Esempi: '10m', '1h30m', '45 secondi', '2 ore'.")
    return int(round(totale))
