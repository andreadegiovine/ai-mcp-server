"""Strumenti: Google Calendar (eventi: elenco, creazione, modifica, eliminazione)."""
import asyncio
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from nucleo.configurazione import _env, _env_bool, _env_list, DATA_DIR, TZ_NAME, TZ
from nucleo.server import FUNZIONALITA, UserError, tool
from nucleo.utilita import GIORNI_BREVI, now, tronca, fmt_dt, parse_data_ora


GOOGLE_SA_FILE = _env("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_TOKEN_FILE = _env("GOOGLE_OAUTH_TOKEN_FILE", str(DATA_DIR / "google_token.json"))
GOOGLE_CALENDAR_IDS = _env_list("GOOGLE_CALENDAR_IDS", "primary")
GOOGLE_DELEGATED_USER = _env("GOOGLE_DELEGATED_USER")
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]

FUNZIONALITA["calendar"] = _env_bool("ENABLE_CALENDAR", True) and (
    bool(GOOGLE_SA_FILE and Path(GOOGLE_SA_FILE).is_file()) or Path(GOOGLE_TOKEN_FILE).is_file())


class GoogleCalendar:
    def __init__(self) -> None:
        self._svc = None
        self._lock = threading.Lock()  # le chiamate httplib2 non sono thread-safe

    def _crea_servizio(self):
        try:
            from googleapiclient.discovery import build
            from google.auth.transport.requests import Request
        except ImportError:
            raise UserError("Installa google-api-python-client e google-auth per usare il calendario.")
        if GOOGLE_SA_FILE and Path(GOOGLE_SA_FILE).is_file():
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(GOOGLE_SA_FILE, scopes=GOOGLE_SCOPES)
            if GOOGLE_DELEGATED_USER:
                creds = creds.with_subject(GOOGLE_DELEGATED_USER)
        elif Path(GOOGLE_TOKEN_FILE).is_file():
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, GOOGLE_SCOPES)
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
                Path(GOOGLE_TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")
        else:
            raise UserError("Google Calendar non configurato (GOOGLE_SERVICE_ACCOUNT_FILE o GOOGLE_OAUTH_TOKEN_FILE).")
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def esegui(self, fn):
        """Esegue fn(service).execute() in modo thread-safe, traducendo gli errori."""
        with self._lock:
            if self._svc is None:
                self._svc = self._crea_servizio()
            try:
                return fn(self._svc).execute()
            except UserError:
                raise
            except Exception as e:
                stato = getattr(getattr(e, "resp", None), "status", None)
                if stato == 404:
                    raise UserError("Calendario o evento non trovato (controlla l'ID e la condivisione col service account).")
                if stato in (401, 403):
                    raise UserError(f"Accesso negato a Google Calendar: {e}")
                raise


CALENDARIO = GoogleCalendar()


def _cal_id(calendario: str) -> str:
    c = (calendario or "").strip()
    return c or GOOGLE_CALENDAR_IDS[0]


def _evento_dt(campo: dict) -> tuple[datetime, bool]:
    if "dateTime" in campo:
        return datetime.fromisoformat(campo["dateTime"].replace("Z", "+00:00")).astimezone(TZ), False
    d = date.fromisoformat(campo["date"])
    return datetime(d.year, d.month, d.day, tzinfo=TZ), True


def _formatta_evento(ev: dict, cal: str) -> str:
    inizio, intero = _evento_dt(ev["start"])
    fine, _ = _evento_dt(ev["end"])
    if intero:
        giorni = (fine - inizio).days
        quando = f"{GIORNI_BREVI[inizio.weekday()]} {inizio:%d/%m/%Y} (tutto il giorno" + \
                 (f", {giorni} giorni)" if giorni > 1 else ")")
    else:
        quando = f"{GIORNI_BREVI[inizio.weekday()]} {inizio:%d/%m/%Y %H:%M}–" + \
                 (f"{fine:%H:%M}" if fine.date() == inizio.date() else f"{fine:%d/%m %H:%M}")
    riga = f"• {quando} · {ev.get('summary', '(senza titolo)')}"
    if ev.get("location"):
        riga += f" · 📍 {ev['location']}"
    if ev.get("description"):
        riga += f"\n    {tronca(re.sub(r'<[^>]+>', ' ', ev['description']).strip(), 200)}"
    riga += f"\n    [id: {ev['id']} · calendario: {cal}]"
    return riga


@tool(
    "calendario_eventi",
    title="Eventi del calendario",
    description=(
        "Elenca gli eventi del Google Calendar in un intervallo. Senza date mostra i prossimi 'giorni' (default 7). "
        "'da'/'a' accettano 'oggi', 'domani', '18/11/2026', '2026-11-18 15:00'. 'cerca' filtra per testo."
    ),
    group="calendar",
    read_only=True,
)
async def calendario_eventi(da: str = "", a: str = "", giorni: int = 7, cerca: str = "",
                            calendario: str = "") -> str:
    inizio = parse_data_ora(da)[0] if da.strip() else now()
    if a.strip():
        fine, ha_ora = parse_data_ora(a)
        if not ha_ora:
            fine += timedelta(days=1)
    else:
        if da.strip() and not parse_data_ora(da)[1]:
            inizio = inizio.replace(hour=0, minute=0)
        fine = inizio + timedelta(days=max(1, min(int(giorni or 7), 366)))
    cals = [calendario.strip()] if calendario.strip() else GOOGLE_CALENDAR_IDS
    tutti = []
    errori = []
    for cal in cals:
        try:
            params = dict(calendarId=cal, timeMin=inizio.isoformat(), timeMax=fine.isoformat(), singleEvents=True,
                          orderBy="startTime", maxResults=100)
            if cerca.strip():
                params["q"] = cerca.strip()
            res = await asyncio.to_thread(CALENDARIO.esegui, lambda s, p=params: s.events().list(**p))
            for ev in res.get("items", []):
                if ev.get("status") != "cancelled":
                    tutti.append((_evento_dt(ev["start"])[0], ev, cal))
        except UserError as e:
            errori.append(f"{cal}: {e}")
    tutti.sort(key=lambda x: x[0])
    intest = f"Eventi dal {fmt_dt(inizio)} al {fmt_dt(fine)}" + (f" con '{cerca}'" if cerca else "")
    if not tutti:
        return intest + ": nessun evento." + (("\nErrori: " + "; ".join(errori)) if errori else "")
    return intest + f" ({len(tutti)}):\n" + "\n".join(_formatta_evento(ev, cal) for _, ev, cal in tutti) + \
        (("\nErrori: " + "; ".join(errori)) if errori else "")


def _corpo_tempi(inizio: str, fine: str, durata_minuti: int, tutto_il_giorno: bool) -> tuple[dict, dict]:
    dt_i, ha_ora = parse_data_ora(inizio)
    if tutto_il_giorno or not ha_ora:
        d_f = parse_data_ora(fine)[0].date() + timedelta(days=1) if fine.strip() else dt_i.date() + timedelta(days=1)
        if d_f <= dt_i.date():
            raise UserError("La data di fine deve essere successiva all'inizio.")
        return {"date": dt_i.date().isoformat()}, {"date": d_f.isoformat()}
    if fine.strip():
        dt_f, ha_ora_f = parse_data_ora(fine, riferimento=dt_i)
        if not ha_ora_f:
            raise UserError("Indica anche l'ora di fine (o usa durata_minuti).")
    else:
        dt_f = dt_i + timedelta(minutes=max(1, int(durata_minuti or 60)))
    if dt_f <= dt_i:
        raise UserError("La fine deve essere successiva all'inizio.")
    return ({"dateTime": dt_i.isoformat(), "timeZone": TZ_NAME},
            {"dateTime": dt_f.isoformat(), "timeZone": TZ_NAME})


@tool(
    "calendario_crea_evento",
    title="Crea evento nel calendario",
    description=(
        "Crea un evento nel Google Calendar. 'inizio' es. '2026-11-20 15:00' o 'domani 9:30'; 'fine' opzionale "
        "(altrimenti usa durata_minuti, default 60). Se l'inizio non ha ora o tutto_il_giorno=true crea un evento "
        "di un'intera giornata. promemoria_minuti aggiunge una notifica (es. 30)."
    ),
    group="calendar",
)
async def calendario_crea_evento(titolo: str, inizio: str, fine: str = "", durata_minuti: int = 60,
                                 tutto_il_giorno: bool = False, descrizione: str = "", luogo: str = "",
                                 promemoria_minuti: Optional[int] = None, calendario: str = "") -> str:
    if not titolo.strip():
        raise UserError("Il titolo e' obbligatorio.")
    start, end = _corpo_tempi(inizio, fine, durata_minuti, tutto_il_giorno)
    corpo: dict[str, Any] = {"summary": titolo.strip(), "start": start, "end": end}
    if descrizione:
        corpo["description"] = descrizione
    if luogo:
        corpo["location"] = luogo
    if promemoria_minuti is not None:
        corpo["reminders"] = {"useDefault": False,
                              "overrides": [{"method": "popup", "minutes": max(0, int(promemoria_minuti))}]}
    cal = _cal_id(calendario)
    ev = await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.events().insert(calendarId=cal, body=corpo))
    return "✅ Evento creato:\n" + _formatta_evento(ev, cal) + (f"\n    link: {ev.get('htmlLink')}" if ev.get("htmlLink") else "")


@tool(
    "calendario_modifica_evento",
    title="Modifica evento del calendario",
    description=(
        "Modifica un evento esistente (ottieni l'id con calendario_eventi). Specifica solo i campi da cambiare. "
        "Se cambi solo 'inizio' la durata originale viene mantenuta."
    ),
    group="calendar",
)
async def calendario_modifica_evento(event_id: str, titolo: str = "", inizio: str = "", fine: str = "",
                                     descrizione: Optional[str] = None, luogo: Optional[str] = None,
                                     calendario: str = "") -> str:
    cal = _cal_id(calendario)
    patch: dict[str, Any] = {}
    if titolo.strip():
        patch["summary"] = titolo.strip()
    if descrizione is not None:
        patch["description"] = descrizione
    if luogo is not None:
        patch["location"] = luogo
    if inizio.strip() or fine.strip():
        ev = await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.events().get(calendarId=cal, eventId=event_id))
        vecchio_i, intero = _evento_dt(ev["start"])
        vecchio_f, _ = _evento_dt(ev["end"])
        durata = int((vecchio_f - vecchio_i).total_seconds() // 60)
        if intero:
            i_txt = inizio.strip() or vecchio_i.date().isoformat()
            f_txt = fine.strip() or (parse_data_ora(i_txt)[0].date() + timedelta(days=max(1, durata // 1440) - 1)).isoformat()
            patch["start"], patch["end"] = _corpo_tempi(i_txt, f_txt, 0, True)
        else:
            i_txt = inizio.strip() or vecchio_i.isoformat()
            patch["start"], patch["end"] = _corpo_tempi(i_txt, fine, durata, False)
    if not patch:
        raise UserError("Nessuna modifica indicata.")
    ev = await asyncio.to_thread(CALENDARIO.esegui,
                                 lambda s: s.events().patch(calendarId=cal, eventId=event_id, body=patch))
    return "✅ Evento aggiornato:\n" + _formatta_evento(ev, cal)


@tool(
    "calendario_elimina_evento",
    title="Elimina evento dal calendario",
    description="Elimina un evento dal Google Calendar. Chiedi conferma all'utente e poi richiama con conferma=true.",
    group="calendar",
    destructive=True,
)
async def calendario_elimina_evento(event_id: str, conferma: bool = False, calendario: str = "") -> str:
    cal = _cal_id(calendario)
    ev = await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.events().get(calendarId=cal, eventId=event_id))
    if not conferma:
        return ("⚠️ Stai per eliminare:\n" + _formatta_evento(ev, cal) +
                "\nChiedi conferma all'utente e poi richiama con conferma=true.")
    await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.events().delete(calendarId=cal, eventId=event_id))
    return f"🗑️ Evento «{ev.get('summary', '')}» eliminato."


@tool(
    "calendario_lista",
    title="Elenco dei calendari",
    description="Elenca i calendari Google accessibili con il loro ID (da usare nel parametro 'calendario').",
    group="calendar",
    read_only=True,
)
async def calendario_lista() -> str:
    res = await asyncio.to_thread(CALENDARIO.esegui, lambda s: s.calendarList().list())
    righe = [f"• {c.get('summary')} — id: {c['id']}{' (principale)' if c.get('primary') else ''} [{c.get('accessRole')}]"
             for c in res.get("items", [])]
    configurati = ", ".join(GOOGLE_CALENDAR_IDS)
    return ("Calendari accessibili:\n" + "\n".join(righe) if righe else
            "Nessun calendario nella lista (con un service account i calendari condivisi possono non comparire qui, "
            "ma sono comunque utilizzabili tramite ID).") + f"\nCalendari configurati (GOOGLE_CALENDAR_IDS): {configurati}"
