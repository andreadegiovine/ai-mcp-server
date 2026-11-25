"""Strumenti: Home Assistant (ricerca entità, stato, storico, servizi con regole di sicurezza)."""

import asyncio
import contextlib
import fnmatch
import json
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from nucleo.configurazione import DATA_DIR, TZ, env_bool, env_int, env_lista, env_str, logger
from nucleo.manutenzione import scrivi_registro
from nucleo.server import FUNZIONALITA, UserError, tool
from nucleo.utilita import fa_quanto, fmt_dt, normalizza, now, tronca

HASS_URL = env_str("HASS_URL", "http://192.168.178.48:8123").rstrip("/")
HASS_TOKEN = env_str("HASS_TOKEN")
HASS_READ_ONLY = env_bool("HASS_READ_ONLY", False)
HASS_VERIFY_SSL = env_bool("HASS_VERIFY_SSL", True)
HASS_CACHE_TTL = env_int("HASS_CACHE_TTL", 10)
_DEFAULT_HASS_DOMAINS = (
    "light,switch,fan,cover,climate,media_player,scene,script,input_boolean,input_number,"
    "input_select,input_text,input_button,input_datetime,button,vacuum,humidifier,water_heater,"
    "number,select,automation,notify,tts,persistent_notification,timer,counter,siren,valve,"
    "lawn_mower,todo,weather,lock,alarm_control_panel,remote,homeassistant,calendar"
)
HASS_ALLOWED_DOMAINS = set(env_lista("HASS_ALLOWED_DOMAINS", _DEFAULT_HASS_DOMAINS))
HASS_CONFIRM_DOMAINS = set(env_lista("HASS_CONFIRM_DOMAINS", "lock,alarm_control_panel"))
HASS_CONFIRM_ENTITIES = env_lista("HASS_CONFIRM_ENTITIES")
HASS_BLOCKED_ENTITIES = env_lista("HASS_BLOCKED_ENTITIES")
HASS_ALLOWED_ENTITIES = env_lista("HASS_ALLOWED_ENTITIES")


FUNZIONALITA["hass"] = env_bool("ENABLE_HASS", True) and bool(HASS_TOKEN)


RE_ENTITY = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_RE_SLUG = re.compile(r"^[a-z0-9_]+$")
_ATTR_RUMOROSI = {
    "friendly_name",
    "unit_of_measurement",
    "icon",
    "entity_picture",
    "supported_features",
    "supported_color_modes",
    "attribution",
    "assumed_state",
    "editable",
    "id",
    "restored",
    "options",
    "effect_list",
    "source_list",
    "sound_mode_list",
    "hvac_modes",
    "fan_modes",
    "preset_modes",
    "swing_modes",
    "access_token",
    "entity_id",
    "device_trackers",
    "state_class",
    "min_color_temp_kelvin",
    "max_color_temp_kelvin",
    "min_mireds",
    "max_mireds",
}
_ATTR_SENSIBILI = re.compile(r"(token|password|passwd|secret|api_?key|code_format|access|credential)", re.I)
_SINONIMI = {
    "luce": "light",
    "luci": "light",
    "lampada": "light",
    "lampadina": "light",
    "faretti": "light",
    "interruttore": "switch",
    "presa": "switch",
    "prese": "switch",
    "spina": "switch",
    "tapparella": "cover",
    "tapparelle": "cover",
    "serranda": "cover",
    "tenda": "cover",
    "tende": "cover",
    "persiana": "cover",
    "garage": "cover garage",
    "cancello": "cover gate",
    "termostato": "climate",
    "clima": "climate",
    "condizionatore": "climate",
    "climatizzatore": "climate",
    "riscaldamento": "climate heat",
    "condizionatori": "climate",
    "ventilatore": "fan",
    "aspirapolvere": "vacuum",
    "robot": "vacuum",
    "serratura": "lock",
    "allarme": "alarm_control_panel",
    "scena": "scene",
    "scene": "scene",
    "automazione": "automation",
    "tv": "media_player tv",
    "televisore": "media_player tv",
    "cassa": "media_player",
    "speaker": "media_player",
    "altoparlante": "media_player",
    "temperatura": "temperature",
    "umidita": "humidity",
    "consumo": "power",
    "potenza": "power",
    "energia": "energy",
    "batteria": "battery",
    "autonomia": "range battery",
    "porta": "door",
    "finestra": "window",
    "movimento": "motion",
    "presenza": "occupancy presence",
    "sensore": "sensor",
    "pulsante": "button",
    "meteo": "weather",
}


class HassError(UserError):
    pass


class HassClient:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.session.verify = HASS_VERIFY_SSL
        retry = Retry(
            total=3,
            backoff_factor=0.4,
            status_forcelist=(502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        self.session.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=10))
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=10))
        self._lock = threading.Lock()
        self._stati: list[dict] | None = None
        self._stati_ts = 0.0
        self._aree: dict[str, str] = {}
        self._aree_ts = 0.0
        self._servizi: dict | None = None
        self._servizi_ts = 0.0
        self.audit = DATA_DIR / "hass_audit.log"

    def _req(self, metodo: str, path: str, **kw) -> Any:
        kw.setdefault("timeout", 15)
        try:
            r = self.session.request(metodo, f"{self.url}{path}", **kw)
        except requests.exceptions.SSLError as e:
            raise HassError(f"Errore SSL verso Home Assistant ({e}). Controlla HASS_URL/HASS_VERIFY_SSL.")
        except requests.exceptions.ConnectionError:
            raise HassError(f"Home Assistant non raggiungibile su {self.url}.")
        except requests.exceptions.Timeout:
            raise HassError("Home Assistant non ha risposto in tempo.")
        if r.status_code == 401:
            raise HassError("Token Home Assistant non valido o scaduto (HASS_TOKEN).")
        if r.status_code == 403:
            raise HassError("Operazione non autorizzata per questo utente di Home Assistant.")
        if r.status_code == 404:
            raise HassError("Risorsa non trovata in Home Assistant.")
        if r.status_code == 400:
            raise HassError(f"Richiesta rifiutata da Home Assistant: {r.text[:300]}")
        if r.status_code >= 500:
            raise HassError(f"Errore interno di Home Assistant (HTTP {r.status_code}): {r.text[:200]}")
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        return r.json() if "json" in ct else r.text

    # --- letture ---
    def stati(self, forza: bool = False) -> list[dict]:
        with self._lock:
            if not forza and self._stati is not None and time.monotonic() - self._stati_ts < HASS_CACHE_TTL:
                return self._stati
        stati = self._req("GET", "/api/states")
        with self._lock:
            self._stati, self._stati_ts = stati, time.monotonic()
        return stati

    def stato(self, entity_id: str) -> dict:
        valida_entity(entity_id)
        try:
            return self._req("GET", f"/api/states/{entity_id}")
        except HassError as e:
            if "non trovata" in str(e):
                raise HassError(f"Entità '{entity_id}' inesistente. Usa hass_cerca_entita per trovare l'ID corretto.")
            raise

    def aree(self) -> dict[str, str]:
        with self._lock:
            if self._aree and time.monotonic() - self._aree_ts < 300:
                return self._aree
        tpl = (
            "{{ {'e': states | map(attribute='entity_id') | list, "
            "'a': states | map(attribute='entity_id') | map('area_name') | list} | tojson }}"
        )
        try:
            testo = self._req("POST", "/api/template", json={"template": tpl})
            dati = json.loads(testo) if isinstance(testo, str) else testo
            mappa = {e: a for e, a in zip(dati["e"], dati["a"], strict=False) if a}
        except Exception as e:
            logger.warning(f"Aree HA non disponibili (serve un token admin per /api/template): {e}")
            mappa = {}
        with self._lock:
            self._aree, self._aree_ts = mappa, time.monotonic()
        return mappa

    def servizi(self) -> dict[str, dict]:
        with self._lock:
            if self._servizi is not None and time.monotonic() - self._servizi_ts < 600:
                return self._servizi
        dati = self._req("GET", "/api/services")
        mappa = {d["domain"]: d.get("services", {}) for d in dati}
        with self._lock:
            self._servizi, self._servizi_ts = mappa, time.monotonic()
        return mappa

    def storico(self, entity_id: str, ore: float) -> list[dict]:
        valida_entity(entity_id)
        inizio = (datetime.now(UTC) - timedelta(hours=ore)).isoformat()
        dati = self._req(
            "GET",
            f"/api/history/period/{quote(inizio)}",
            params={
                "filter_entity_id": entity_id,
                "minimal_response": "",
                "no_attributes": "",
                "significant_changes_only": "",
            },
            timeout=30,
        )
        return dati[0] if dati else []

    # --- scritture ---
    def chiama_servizio(self, dominio: str, servizio: str, dati: dict, risposta: bool = False) -> Any:
        path = f"/api/services/{dominio}/{servizio}" + ("?return_response" if risposta else "")
        res = self._req("POST", path, json=dati, timeout=30)
        with self._lock:
            self._stati_ts = 0.0  # invalida cache
        return res

    def registra_audit(self, testo: str) -> None:
        scrivi_registro(self.audit, f"{now().isoformat(timespec='seconds')} {testo}")


HASS = HassClient(HASS_URL, HASS_TOKEN) if HASS_TOKEN else None


def valida_entity(entity_id: str) -> str:
    if not entity_id or not RE_ENTITY.match(entity_id):
        raise HassError(f"entity_id non valido: '{entity_id}' (formato atteso: dominio.nome, es. light.cucina)")
    return entity_id


def client_hass() -> HassClient:
    if HASS is None:
        raise UserError("Home Assistant non configurato (imposta HASS_URL e HASS_TOKEN).")
    return HASS


def _valore_attr(k: str, v: Any) -> str:
    if _ATTR_SENSIBILI.search(k):
        return "[nascosto]"
    if k == "brightness" and isinstance(v, (int, float)):
        return f"{round(v / 255 * 100)}%"
    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    return s if len(s) <= 120 else s[:117] + "..."


def formatta_entita(e: dict, aree: dict[str, str], dettaglio: bool = False) -> str:
    eid = e.get("entity_id", "?")
    attrs = e.get("attributes", {}) or {}
    nome = attrs.get("friendly_name", "")
    unita = attrs.get("unit_of_measurement", "")
    riga = f"- {eid}"
    if nome:
        riga += f" «{nome}»"
    riga += f" = {e.get('state')}{(' ' + unita) if unita else ''}"
    if eid in aree:
        riga += f" [area: {aree[eid]}]"
    if dettaglio:
        with contextlib.suppress(Exception):
            lc = datetime.fromisoformat(e["last_changed"].replace("Z", "+00:00"))
            riga += f"\n    ultimo cambio: {fmt_dt(lc)} ({fa_quanto(lc)})"
        extra = [(k, v) for k, v in attrs.items() if k not in _ATTR_RUMOROSI and v is not None]
        for k, v in extra[:25]:
            riga += f"\n    {k}: {_valore_attr(k, v)}"
        for k in ("hvac_modes", "preset_modes", "fan_modes", "source_list", "options", "effect_list"):
            if attrs.get(k):
                riga += f"\n    {k}: {_valore_attr(k, attrs[k])}"
    else:
        utili = []
        for k in (
            "device_class",
            "current_temperature",
            "temperature",
            "hvac_action",
            "current_position",
            "brightness",
            "media_title",
            "media_artist",
            "volume_level",
            "battery_level",
        ):
            if attrs.get(k) is not None:
                utili.append(f"{k}={_valore_attr(k, attrs[k])}")
        if utili:
            riga += f" ({', '.join(utili[:4])})"
    return riga


def _testo_ricerca(e: dict, aree: dict[str, str]) -> str:
    attrs = e.get("attributes", {}) or {}
    return normalizza(
        " ".join(
            str(x)
            for x in (
                e["entity_id"],
                attrs.get("friendly_name", ""),
                aree.get(e["entity_id"], ""),
                attrs.get("device_class", ""),
                e["entity_id"].split(".")[0],
            )
        )
    )


def cerca_entita(
    stati: list[dict], aree: dict[str, str], testo: str = "", dominio: str = "", area: str = ""
) -> list[tuple[float, dict]]:
    dominio = (dominio or "").strip().lower()
    area_n = normalizza(area) if area else ""
    toks = []
    for t in normalizza(testo).split():
        if t in _SINONIMI:
            toks.append((t, _SINONIMI[t].split()))
        elif len(t) > 1 and t not in ("il", "la", "lo", "le", "di", "del", "della", "in", "e", "dei", "delle"):
            toks.append((t, []))
    risultati = []
    for e in stati:
        eid = e["entity_id"]
        if dominio and not eid.startswith(dominio + "."):
            continue
        if (
            area_n
            and area_n not in normalizza(aree.get(eid, ""))
            and area_n not in normalizza(e.get("attributes", {}).get("friendly_name", ""))
        ):
            continue
        hay = _testo_ricerca(e, aree)
        parole = hay.split()
        punteggio = 0.0
        trovati = 0
        for t, sin in toks:
            if t in hay or any(s in hay for s in sin):
                trovati += 1
                punteggio += 3 if re.search(rf"\b{re.escape(t)}\b", hay) else 2
            else:
                migliore = max((SequenceMatcher(None, t, p).ratio() for p in parole), default=0)
                if migliore >= 0.8:
                    trovati += 1
                    punteggio += migliore
        if toks and trovati == 0:
            continue
        punteggio += trovati * 5 - (len(toks) - trovati) * 4
        if e.get("state") in ("unavailable", "unknown"):
            punteggio -= 1
        risultati.append((punteggio, e))
    risultati.sort(key=lambda x: (-x[0], x[1]["entity_id"]))
    return risultati


@tool(
    "hass_cerca_entita",
    title="Cerca dispositivi della casa",
    description=(
        "Cerca entità di Home Assistant per nome, stanza/area, tipo o dominio e ne mostra lo stato attuale. "
        "Capisce termini italiani (luce, tapparella, termostato, temperatura, batteria, consumo...). "
        "Esempi: testo='temperatura soggiorno'; testo='mokka autonomia'; dominio='light' area='cucina'. "
        "Usalo SEMPRE per trovare l'entity_id corretto prima di leggere o controllare un dispositivo."
    ),
    group="hass",
    read_only=True,
)
async def hass_cerca_entita(testo: str = "", dominio: str = "", area: str = "", limite: int = 25) -> str:
    h = client_hass()
    stati, aree = await asyncio.gather(asyncio.to_thread(h.stati), asyncio.to_thread(h.aree))
    risultati = cerca_entita(stati, aree, testo, dominio, area)
    limite = max(1, min(int(limite or 25), 100))
    if not risultati:
        return (
            f"Nessuna entità trovata per testo='{testo}' dominio='{dominio}' area='{area}'. "
            f"Prova termini diversi o hass_panoramica per vedere domini e aree."
        )
    righe = [formatta_entita(e, aree) for _, e in risultati[:limite]]
    extra = f"\n(+{len(risultati) - limite} altre: affina la ricerca)" if len(risultati) > limite else ""
    return f"{min(len(risultati), limite)} entità trovate:\n" + "\n".join(righe) + extra


@tool(
    "hass_stato",
    title="Stato dei dispositivi",
    description=(
        "Legge lo stato attuale (in tempo reale) e tutti gli attributi di una o più entità di Home Assistant. "
        "entity_id: uno o più ID separati da virgola (es. 'sensor.temperatura_soggiorno,light.cucina')."
    ),
    group="hass",
    read_only=True,
)
async def hass_stato(entity_id: str) -> str:
    h = client_hass()
    ids = [x.strip() for x in entity_id.split(",") if x.strip()][:20]
    if not ids:
        raise UserError("Specifica almeno un entity_id.")
    for i in ids:
        valida_entity(i)
    aree = await asyncio.to_thread(h.aree)
    risultati = await asyncio.gather(*[asyncio.to_thread(h.stato, i) for i in ids], return_exceptions=True)
    righe = []
    for i, r in zip(ids, risultati, strict=False):
        righe.append(f"- {i}: {r}" if isinstance(r, Exception) else formatta_entita(r, aree, dettaglio=True))
    return "\n".join(righe)


@tool(
    "hass_panoramica",
    title="Panoramica della casa",
    description=(
        "Riepilogo della casa: numero di entità per dominio, elenco aree, e cosa è attivo ora "
        "(luci accese, dispositivi in funzione, porte/finestre aperte, clima, media in riproduzione, avvisi)."
    ),
    group="hass",
    read_only=True,
)
async def hass_panoramica() -> str:
    h = client_hass()
    stati, aree = await asyncio.gather(asyncio.to_thread(h.stati, True), asyncio.to_thread(h.aree))
    domini: dict[str, int] = {}
    for e in stati:
        d = e["entity_id"].split(".")[0]
        domini[d] = domini.get(d, 0) + 1
    righe = [
        f"Home Assistant: {len(stati)} entità",
        "Domini: " + ", ".join(f"{d} ({n})" for d, n in sorted(domini.items(), key=lambda x: -x[1])),
    ]
    nomi_aree = sorted(set(aree.values()))
    if nomi_aree:
        righe.append("Aree: " + ", ".join(nomi_aree))

    def sel(pred, titolo, max_n=30):
        trovati = [e for e in stati if pred(e)]
        if trovati:
            righe.append(f"\n{titolo} ({len(trovati)}):")
            righe.extend(formatta_entita(e, aree) for e in trovati[:max_n])

    dc = lambda e: e.get("attributes", {}).get("device_class")  # noqa: E731
    sel(lambda e: e["entity_id"].startswith("light.") and e["state"] == "on", "Luci accese")
    sel(
        lambda e: e["entity_id"].startswith(("switch.", "fan.", "vacuum.")) and e["state"] in ("on", "cleaning"),
        "Interruttori/dispositivi accesi",
    )
    sel(
        lambda e: (
            e["entity_id"].startswith("binary_sensor.")
            and dc(e) in ("door", "window", "garage_door", "opening")
            and e["state"] == "on"
        ),
        "Porte/finestre aperte",
    )
    sel(lambda e: e["entity_id"].startswith("cover.") and e["state"] in ("open", "opening"), "Coperture aperte")
    sel(lambda e: e["entity_id"].startswith("climate.") and e["state"] not in ("off", "unavailable"), "Clima attivo")
    sel(lambda e: e["entity_id"].startswith("media_player.") and e["state"] in ("playing", "paused"), "Media")
    sel(lambda e: e["entity_id"].startswith(("lock.", "alarm_control_panel.")), "Sicurezza")
    sel(
        lambda e: (
            e["entity_id"].startswith("persistent_notification.")
            or (e["entity_id"].startswith("update.") and e["state"] == "on")
        ),
        "Notifiche/aggiornamenti",
        10,
    )
    non_disp = sum(1 for e in stati if e["state"] == "unavailable")
    if non_disp:
        righe.append(f"\nEntità non disponibili: {non_disp}")
    return "\n".join(righe)


@tool(
    "hass_storico",
    title="Storico dei dispositivi",
    description=(
        "Storico di un'entita' nelle ultime N ore (max 168): per sensori numerici min/max/media e andamento, "
        "per gli altri l'elenco dei cambi di stato. Es: 'quanto ha consumato oggi', 'quando si è aperta la porta'."
    ),
    group="hass",
    read_only=True,
)
async def hass_storico(entity_id: str, ore: float = 24) -> str:
    h = client_hass()
    ore = max(0.1, min(float(ore or 24), 168))
    dati = await asyncio.to_thread(h.storico, entity_id.strip(), ore)
    if not dati:
        return f"Nessun dato storico per {entity_id} nelle ultime {ore:g} ore."
    punti = []
    for d in dati:
        with contextlib.suppress(Exception):
            ts = datetime.fromisoformat((d.get("last_changed") or d.get("last_updated")).replace("Z", "+00:00"))
            punti.append((ts, d.get("state")))
    unita = (dati[0].get("attributes") or {}).get("unit_of_measurement", "")
    numerici = []
    for ts, s in punti:
        with contextlib.suppress(TypeError, ValueError):
            numerici.append((ts, float(s)))
    righe = [f"Storico {entity_id} – ultime {ore:g} ore ({len(punti)} cambi di stato)"]
    if numerici and len(numerici) >= len(punti) * 0.8:
        valori = [v for _, v in numerici]
        imin = min(range(len(valori)), key=valori.__getitem__)
        imax = max(range(len(valori)), key=valori.__getitem__)
        righe.append(
            f"Attuale: {valori[-1]:g} {unita} · Min: {valori[imin]:g} {unita} ({fmt_dt(numerici[imin][0])}) · "
            f"Max: {valori[imax]:g} {unita} ({fmt_dt(numerici[imax][0])}) · "
            f"Media: {sum(valori) / len(valori):.2f} {unita}"
        )
        passo = max(1, len(numerici) // 24)
        campioni = numerici[::passo][-24:]
        righe.append("Andamento: " + " | ".join(f"{ts.astimezone(TZ):%d/%m %H:%M} {v:g}" for ts, v in campioni))
    else:
        for ts, s in punti[-40:]:
            righe.append(f"- {fmt_dt(ts)}: {s}")
        if len(punti) > 40:
            righe.insert(1, f"(mostrati gli ultimi 40 di {len(punti)})")
    return "\n".join(righe)


@tool(
    "hass_servizi",
    title="Azioni disponibili della casa",
    description=(
        "Elenca i servizi (azioni) disponibili per un dominio di Home Assistant con i relativi parametri, "
        "es. dominio='climate' o 'media_player'."
    ),
    group="hass",
    read_only=True,
)
async def hass_servizi(dominio: str) -> str:
    h = client_hass()
    dominio = dominio.strip().lower()
    servizi = await asyncio.to_thread(h.servizi)
    if dominio not in servizi:
        raise UserError(f"Dominio '{dominio}' senza servizi. Domini disponibili: {', '.join(sorted(servizi))}")
    righe = [
        f"Servizi del dominio {dominio}"
        + ("" if dominio in HASS_ALLOWED_DOMAINS else " (ATTENZIONE: dominio non abilitato al controllo)")
    ]
    for nome, info in sorted(servizi[dominio].items()):
        if not _servizio_permesso(dominio, nome):
            continue
        campi = info.get("fields", {}) or {}
        desc_campi = ", ".join(
            f"{k}{'*' if v.get('required') else ''}"
            + (f" (es. {v['example']})" if v.get("example") is not None else "")
            for k, v in campi.items()
            if isinstance(v, dict) and k != "advanced_fields"
        )
        righe.append(
            f"- {dominio}.{nome}: {info.get('description', '')[:150]}"
            + (f"\n    parametri: {desc_campi}" if desc_campi else "")
        )
    return "\n".join(righe)


_SERVIZI_HOMEASSISTANT = {"turn_on", "turn_off", "toggle", "update_entity"}
_DOMINI_SENZA_ENTITA = {"notify", "persistent_notification", "script", "scene", "tts"}


def _servizio_permesso(dominio: str, servizio: str) -> bool:
    if dominio == "homeassistant":
        return servizio in _SERVIZI_HOMEASSISTANT
    if servizio.startswith("reload") or servizio.endswith("_reload") or "config" in servizio:
        return False
    if dominio == "automation" and servizio not in ("trigger", "turn_on", "turn_off", "toggle"):
        return False
    return True


def corrisponde_pattern(entity_id: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(entity_id, p) for p in patterns)


def _richiede_conferma(entita: dict) -> bool:
    eid = entita["entity_id"]
    dom = eid.split(".")[0]
    if dom in HASS_CONFIRM_DOMAINS or corrisponde_pattern(eid, HASS_CONFIRM_ENTITIES):
        return True
    dc = (entita.get("attributes") or {}).get("device_class")
    return dom == "cover" and dc in ("garage", "gate", "door")


@tool(
    "hass_esegui_servizio",
    title="Controllo dei dispositivi",
    description=(
        "Esegue un servizio (azione) di Home Assistant su una o più entità. Esempi:\n"
        "- accendere luce al 50%: dominio='light' servizio='turn_on' entity_id='light.cucina' "
        "dati={\"brightness_pct\":50}\n"
        "- spegnere: servizio='turn_off'; invertire: 'toggle'\n"
        "- tapparella: dominio='cover' servizio='open_cover'/'close_cover'/'set_cover_position' "
        "dati={\"position\":30}\n"
        "- termostato: dominio='climate' servizio='set_temperature' dati={\"temperature\":21}\n"
        "- volume: dominio='media_player' servizio='volume_set' dati={\"volume_level\":0.3}\n"
        "- scena/script: dominio='scene' servizio='turn_on' entity_id='scene.cinema'\n"
        "- previsioni: dominio='weather' servizio='get_forecasts' dati={\"type\":\"daily\"}\n"
        "entity_id accetta più ID separati da virgola. Trova prima gli ID con hass_cerca_entita. "
        "Serrature, allarmi, garage e cancelli richiedono conferma=true: chiedi PRIMA il consenso esplicito all'utente."
    ),
    group="hass",
    destructive=True,
)
async def hass_esegui_servizio(
    dominio: str, servizio: str, entity_id: str = "", dati: dict[str, Any] | None = None, conferma: bool = False
) -> str:
    h = client_hass()
    if HASS_READ_ONLY:
        raise UserError("Home Assistant è configurato in sola lettura (HASS_READ_ONLY=true).")
    dominio, servizio = _controlla_servizio(dominio, servizio)
    ids, dati = _estrai_entita(entity_id, dati)

    servizi = await asyncio.to_thread(h.servizi)
    if dominio not in servizi or servizio not in servizi[dominio]:
        disponibili = ", ".join(sorted(servizi.get(dominio, {}))) or "nessuno"
        raise UserError(f"Servizio {dominio}.{servizio} inesistente. Servizi di {dominio}: {disponibili}")
    vuole_risposta = "response" in servizi[dominio][servizio]
    if not ids and dominio not in _DOMINI_SENZA_ENTITA:
        raise UserError("Specifica entity_id (usa hass_cerca_entita per trovarlo).")

    sensibili = await _controlla_entita(h, dominio, ids)
    if sensibili and not conferma:
        return (
            f"AZIONE SENSIBILE: {dominio}.{servizio} su {', '.join(sensibili)}. "
            f"Chiedi all'utente una conferma esplicita; solo se conferma ripeti la chiamata con conferma=true."
        )

    if ids:
        dati["entity_id"] = ids if len(ids) > 1 else ids[0]
    h.registra_audit(f"CALL {dominio}.{servizio} {json.dumps(dati, ensure_ascii=False)} conferma={conferma}")
    logger.info(f"[hass] {dominio}.{servizio} {dati}")
    risposta = await asyncio.to_thread(h.chiama_servizio, dominio, servizio, dati, vuole_risposta)
    return await _esito_servizio(h, dominio, servizio, ids, risposta)


def _controlla_servizio(dominio: str, servizio: str) -> tuple[str, str]:
    """Normalizza e valida dominio e servizio secondo le regole di sicurezza."""
    dominio, servizio = dominio.strip().lower(), servizio.strip().lower()
    if "." in servizio and not dominio:
        dominio, servizio = servizio.split(".", 1)
    if not _RE_SLUG.match(dominio) or not _RE_SLUG.match(servizio):
        raise UserError("Dominio o servizio non validi.")
    if dominio not in HASS_ALLOWED_DOMAINS:
        raise UserError(
            f"Il dominio '{dominio}' non è abilitato al controllo. Domini consentiti: "
            f"{', '.join(sorted(HASS_ALLOWED_DOMAINS))}"
        )
    if not _servizio_permesso(dominio, servizio):
        raise UserError(f"Il servizio {dominio}.{servizio} non è consentito per motivi di sicurezza.")
    return dominio, servizio


def _estrai_entita(entity_id: str, dati: dict[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    """Riunisce gli entity_id dal parametro e dai dati; rifiuta i target per area, dispositivo, piano o etichetta."""
    dati = dict(dati or {})
    if isinstance(dati.get("target"), dict):
        dati.update(dati.pop("target"))
    ids_param = [x.strip() for x in entity_id.split(",") if x.strip()]
    ids_dati = dati.pop("entity_id", [])
    ids_dati = [ids_dati] if isinstance(ids_dati, str) else list(ids_dati or [])
    for chiave in ("area_id", "device_id", "floor_id", "label_id"):
        if chiave in dati:
            raise UserError(f"Il parametro '{chiave}' non è consentito: indica esplicitamente gli entity_id.")
    return list(dict.fromkeys(ids_param + [str(x).strip() for x in ids_dati])), dati


async def _controlla_entita(h: HassClient, dominio: str, ids: list[str]) -> list[str]:
    """Verifica esistenza, dominio e liste di sicurezza; ritorna le entità che richiedono conferma."""
    stati = {e["entity_id"]: e for e in await asyncio.to_thread(h.stati)}
    sensibili = []
    for eid in ids:
        if eid == "all":
            raise UserError("entity_id 'all' non consentito: elenca le entità esplicitamente.")
        valida_entity(eid)
        if eid not in stati:
            stati.update({e["entity_id"]: e for e in await asyncio.to_thread(h.stati, True)})
            if eid not in stati:
                raise UserError(f"Entità '{eid}' inesistente. Usa hass_cerca_entita.")
        dom_e = eid.split(".")[0]
        if dominio != "homeassistant" and dom_e != dominio and dominio not in ("tts", "notify", "media_player"):
            raise UserError(
                f"L'entità {eid} non appartiene al dominio '{dominio}' (usa dominio='{dom_e}' oppure 'homeassistant')."
            )
        if dom_e not in HASS_ALLOWED_DOMAINS:
            raise UserError(f"Le entità del dominio '{dom_e}' non sono controllabili.")
        if corrisponde_pattern(eid, HASS_BLOCKED_ENTITIES):
            raise UserError(f"L'entità {eid} è bloccata dalla configurazione di sicurezza.")
        if HASS_ALLOWED_ENTITIES and not corrisponde_pattern(eid, HASS_ALLOWED_ENTITIES):
            raise UserError(f"L'entità {eid} non è nell'elenco delle entità controllabili.")
        if _richiede_conferma(stati[eid]):
            sensibili.append(eid)
    return sensibili


async def _esito_servizio(h: HassClient, dominio: str, servizio: str, ids: list[str], risposta: Any) -> str:
    """Testo di esito: risposta del servizio (se c'è) e nuovo stato delle entità."""
    righe = [f"Eseguito {dominio}.{servizio}" + (f" su {', '.join(ids)}" if ids else "")]
    if isinstance(risposta, dict) and risposta.get("service_response"):
        righe.append(
            "Risposta: " + tronca(json.dumps(risposta["service_response"], ensure_ascii=False, indent=1), 6000)
        )
    if ids:
        await asyncio.sleep(1.0)
        aree = await asyncio.to_thread(h.aree)
        nuovi = await asyncio.gather(*[asyncio.to_thread(h.stato, i) for i in ids[:10]], return_exceptions=True)
        righe.append("Stato attuale:")
        for i, n in zip(ids, nuovi, strict=False):
            righe.append(f"- {i}: {n}" if isinstance(n, Exception) else formatta_entita(n, aree))
    return "\n".join(righe)
