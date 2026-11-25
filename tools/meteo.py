"""Strumento: meteo attuale e previsioni (Open-Meteo)."""

import asyncio
from datetime import date

from nucleo.configurazione import TZ_NAME, env_str
from nucleo.rete import HTTP
from nucleo.server import UserError, tool
from nucleo.utilita import GIORNI_BREVI, normalizza, now

METEO_DEFAULT_LOCALITA = env_str("METEO_DEFAULT_LOCALITA")
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"


CODICI_METEO = {
    0: "sereno",
    1: "prevalentemente sereno",
    2: "parzialmente nuvoloso",
    3: "coperto",
    45: "nebbia",
    48: "nebbia con brina",
    51: "pioviggine debole",
    53: "pioviggine moderata",
    55: "pioviggine intensa",
    56: "pioviggine gelata debole",
    57: "pioviggine gelata intensa",
    61: "pioggia debole",
    63: "pioggia moderata",
    65: "pioggia forte",
    66: "pioggia gelata debole",
    67: "pioggia gelata forte",
    71: "neve debole",
    73: "neve moderata",
    75: "neve forte",
    77: "granelli di neve",
    80: "rovesci deboli",
    81: "rovesci moderati",
    82: "rovesci violenti",
    85: "rovesci di neve deboli",
    86: "rovesci di neve forti",
    95: "temporale",
    96: "temporale con grandine debole",
    99: "temporale con grandine forte",
}


def _direzione_vento(gradi: float | None) -> str:
    if gradi is None:
        return ""
    nomi = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    return nomi[int((gradi % 360) / 45 + 0.5) % 8]


def _geocodifica(localita: str) -> dict:
    parti = [p.strip() for p in localita.split(",") if p.strip()]
    nome = parti[0] if parti else localita
    r = HTTP.get(OPEN_METEO_GEO_URL, params={"name": nome, "count": 10, "language": "it", "format": "json"}, timeout=10)
    r.raise_for_status()
    risultati = r.json().get("results") or []
    if not risultati:
        raise UserError(f"Località '{localita}' non trovata. Prova con il solo nome del comune.")
    if len(parti) > 1:
        extra = normalizza(" ".join(parti[1:]))
        for res in risultati:
            campi = normalizza(
                " ".join(str(res.get(k, "")) for k in ("admin1", "admin2", "admin3", "country", "country_code"))
            )
            if any(tok in campi for tok in extra.split()):
                return res
    italiani = [x for x in risultati if x.get("country_code") == "IT"]
    return (italiani or risultati)[0]


def _meteo(lat: float, lon: float, giorni: int) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": TZ_NAME,
        "forecast_days": giorni,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
        "cloud_cover,wind_speed_10m,wind_direction_10m,wind_gusts_10m,is_day,pressure_msl",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
        "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset,uv_index_max",
    }
    r = HTTP.get(OPEN_METEO_URL, params=params, timeout=12)
    r.raise_for_status()
    return r.json()


@tool(
    "meteo",
    title="Meteo e previsioni",
    description=(
        "Meteo attuale, prossime ore e previsioni giornaliere (fino a 16 giorni) per una località. "
        "Passa 'localita' come nome (es. 'Priolo Gargallo' o 'Roma, Lazio') OPPURE latitudine e longitudine. "
        "Utile per domande su temperatura, pioggia, vento, alba/tramonto, UV."
    ),
    read_only=True,
)
async def meteo(
    localita: str | None = None, latitudine: float | None = None, longitudine: float | None = None, giorni: int = 3
) -> str:
    giorni = max(1, min(int(giorni or 3), 16))
    nome = ""
    if latitudine is None or longitudine is None:
        localita = (localita or METEO_DEFAULT_LOCALITA).strip()
        if not localita:
            raise UserError("Indica una località oppure latitudine e longitudine.")
        geo = await asyncio.to_thread(_geocodifica, localita)
        latitudine, longitudine = geo["latitude"], geo["longitude"]
        nome = ", ".join(x for x in (geo.get("name"), geo.get("admin2") or geo.get("admin1"), geo.get("country")) if x)
    dati = await asyncio.to_thread(_meteo, latitudine, longitudine, giorni)
    c = dati.get("current", {})
    righe = [f"Meteo {nome or ''} ({latitudine:.3f}, {longitudine:.3f})".replace("  ", " ")]
    righe.append(
        f"ADESSO ({c.get('time', '').replace('T', ' ')}): {CODICI_METEO.get(c.get('weather_code'), 'n/d')}, "
        f"{c.get('temperature_2m')}°C (percepita {c.get('apparent_temperature')}°C), "
        f"umidità {c.get('relative_humidity_2m')}%, nuvolosità {c.get('cloud_cover')}%, "
        f"precipitazioni {c.get('precipitation')} mm, vento {c.get('wind_speed_10m')} km/h "
        f"{_direzione_vento(c.get('wind_direction_10m'))} (raffiche {c.get('wind_gusts_10m')} km/h), "
        f"pressione {c.get('pressure_msl')} hPa"
    )
    h = dati.get("hourly", {})
    if h.get("time"):
        adesso = now().strftime("%Y-%m-%dT%H:00")
        try:
            i0 = h["time"].index(adesso)
        except ValueError:
            i0 = 0
        prossime = []
        for i in range(i0 + 1, min(i0 + 13, len(h["time"])), 2):
            prossime.append(
                f"{h['time'][i][11:16]} {h['temperature_2m'][i]}°C {CODICI_METEO.get(h['weather_code'][i], '')}"
                f" pioggia {h['precipitation_probability'][i]}%"
            )
        righe.append("PROSSIME ORE: " + " | ".join(prossime))
    d = dati.get("daily", {})
    righe.append("PREVISIONI:")
    for i, giorno in enumerate(d.get("time", [])):
        gd = date.fromisoformat(giorno)
        righe.append(
            f"- {GIORNI_BREVI[gd.weekday()]} {gd:%d/%m}: {CODICI_METEO.get(d['weather_code'][i], 'n/d')}, "
            f"{d['temperature_2m_min'][i]}–{d['temperature_2m_max'][i]}°C, pioggia {d['precipitation_sum'][i]} mm "
            f"(prob. {d['precipitation_probability_max'][i]}%), vento max {d['wind_speed_10m_max'][i]} km/h, "
            f"UV {d['uv_index_max'][i]}, alba {d['sunrise'][i][11:16]}, tramonto {d['sunset'][i][11:16]}"
        )
    return "\n".join(righe)
