"""Strumento: data e ora correnti."""

from datetime import datetime
from zoneinfo import ZoneInfo

from nucleo.configurazione import TZ
from nucleo.server import UserError, tool
from nucleo.utilita import GIORNI, MESI


@tool(
    "data_ora_corrente",
    title="Data e ora",
    description=(
        "Restituisce data, ora, giorno della settimana e numero di settimana correnti. "
        "Usalo quando l'utente chiede che ore sono, che giorno è oggi o per qualsiasi calcolo che "
        "dipende dalla data attuale. Parametro opzionale fuso_orario IANA (es. 'America/New_York')."
    ),
    read_only=True,
)
async def data_ora_corrente(fuso_orario: str | None = None) -> str:
    tz = TZ
    if fuso_orario:
        try:
            tz = ZoneInfo(fuso_orario.strip())
        except Exception:
            raise UserError(f"Fuso orario sconosciuto: '{fuso_orario}'. Usa un nome IANA, es. 'Europe/London'.")
    d = datetime.now(tz)
    offset = d.strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}"
    iso_y, iso_w, _ = d.isocalendar()
    return (
        f"{GIORNI[d.weekday()]} {d.day} {MESI[d.month - 1]} {d.year}, ore {d.strftime('%H:%M:%S')}\n"
        f"Fuso orario: {getattr(tz, 'key', str(tz))} ({offset})\n"
        f"ISO: {d.isoformat(timespec='seconds')} · settimana {iso_w} · giorno dell'anno {d.timetuple().tm_yday}"
    )
