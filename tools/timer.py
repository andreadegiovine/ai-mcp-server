"""Strumenti: timer e sveglie multipli, persistenti, con notifiche tramite Home Assistant."""
import asyncio
import contextlib
import math
import secrets
import shutil
import struct
import subprocess
import time
import wave
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from nucleo.configurazione import _env, _env_bool, DATA_DIR, TZ, logger
from nucleo.server import UserError, tool
from nucleo.utilita import (
    GIORNI,
    now,
    normalizza,
    fmt_durata,
    fmt_dt,
    JsonStore,
    _parse_ora,
    parse_data_ora,
    parse_durata,
)
from tools.home_assistant import HASS


TIMER_NOTIFY_SERVICE = _env("TIMER_NOTIFY_SERVICE")
TIMER_TTS_ENTITY = _env("TIMER_TTS_ENTITY")
TIMER_TTS_PLAYER = _env("TIMER_TTS_PLAYER")
TIMER_HASS_SCRIPT = _env("TIMER_HASS_SCRIPT")
TIMER_PERSISTENT_NOTIFICATION = _env_bool("TIMER_PERSISTENT_NOTIFICATION", True)
TIMER_LOCAL_SOUND = _env_bool("TIMER_LOCAL_SOUND", False)


class GestoreTimer:
    def __init__(self) -> None:
        self.store = JsonStore(DATA_DIR / "timers.json", {"attivi": [], "scaduti": []})
        self.timer: dict[str, dict] = {}
        self.scaduti: deque = deque(maxlen=20)
        self._task: dict[str, asyncio.Task] = {}
        self._beep = DATA_DIR / "timer_beep.wav"

    def _salva(self) -> None:
        self.store.save({"attivi": list(self.timer.values()), "scaduti": list(self.scaduti)})

    def avvia(self) -> None:
        dati = self.store.load()
        self.scaduti.extend(dati.get("scaduti", []))
        adesso = time.time()
        for t in dati.get("attivi", []):
            if t["fine"] > adesso - 300:  # recupera anche quelli scaduti da poco (server spento)
                self.timer[t["id"]] = t
                self._programma(t)
            else:
                t["nota"] = "scaduto mentre il server era spento"
                self.scaduti.append(t)
        self._salva()
        if self.timer:
            logger.info(f"Ripristinati {len(self.timer)} timer")

    def ferma(self) -> None:
        for task in self._task.values():
            task.cancel()
        self._task.clear()

    def _programma(self, t: dict) -> None:
        self._task[t["id"]] = asyncio.get_running_loop().create_task(self._attendi(t["id"]))

    async def _attendi(self, tid: str) -> None:
        try:
            while True:
                t = self.timer.get(tid)
                if t is None:
                    return
                rimanente = t["fine"] - time.time()
                if rimanente <= 0:
                    break
                await asyncio.sleep(min(rimanente, 30))
            await self._scatta(tid)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Errore timer {tid}: {e}")

    async def _scatta(self, tid: str) -> None:
        t = self.timer.pop(tid, None)
        self._task.pop(tid, None)
        if t is None:
            return
        t["scattato"] = now().isoformat(timespec="seconds")
        self.scaduti.append(t)
        self._salva()
        tipo = "La sveglia" if t["tipo"] == "sveglia" else "Il timer"
        msg = f"{tipo} {('«' + t['etichetta'] + '» ') if t['etichetta'] else ''}è scaduto!"
        if t["tipo"] == "timer":
            msg += f" (durata {fmt_durata(t['durata'])})"
        logger.info(f"⏰ {msg}")
        await self._notifica(t, msg)

    async def _notifica(self, t: dict, msg: str) -> None:
        compiti = []
        if HASS is not None:
            if TIMER_PERSISTENT_NOTIFICATION:
                compiti.append(("persistent_notification", "create",
                                {"title": "⏰ Timer", "message": msg, "notification_id": f"mcp_timer_{t['id']}"}))
            if TIMER_NOTIFY_SERVICE and "." in TIMER_NOTIFY_SERVICE:
                d, s = TIMER_NOTIFY_SERVICE.split(".", 1)
                compiti.append((d, s, {"title": "⏰ Timer", "message": msg}))
            if TIMER_TTS_ENTITY and TIMER_TTS_PLAYER:
                compiti.append(("tts", "speak", {"entity_id": TIMER_TTS_ENTITY,
                                                 "media_player_entity_id": TIMER_TTS_PLAYER, "message": msg}))
            if TIMER_HASS_SCRIPT:
                compiti.append(("script", "turn_on", {"entity_id": TIMER_HASS_SCRIPT,
                                                      "variables": {"messaggio": msg, "etichetta": t["etichetta"]}}))
        for d, s, dati in compiti:
            try:
                await asyncio.to_thread(HASS.chiama_servizio, d, s, dati)
            except Exception as e:
                logger.warning(f"Notifica timer via {d}.{s} fallita: {e}")
        if TIMER_LOCAL_SOUND:
            await self._suono_locale()

    async def _suono_locale(self) -> None:
        if not self._beep.exists():
            with wave.open(str(self._beep), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                frames = bytearray()
                for rip in range(3):
                    for i in range(int(22050 * 0.35)):
                        frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 880 * i / 22050)))
                    frames += b"\x00\x00" * int(22050 * 0.2)
                w.writeframes(bytes(frames))
        for cmd in (["mpv", "--no-video", "--really-quiet"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
                    ["paplay"], ["aplay", "-q"]):
            if shutil.which(cmd[0]):
                with contextlib.suppress(Exception):
                    p = await asyncio.create_subprocess_exec(*cmd, str(self._beep), stdout=subprocess.DEVNULL,
                                                             stderr=subprocess.DEVNULL)
                    await asyncio.wait_for(p.wait(), 15)
                return

    def crea(self, secondi: int, etichetta: str, tipo: str, fine: Optional[float] = None) -> dict:
        if len(self.timer) >= 50:
            raise UserError("Troppi timer attivi (max 50).")
        t = {
            "id": secrets.token_hex(2),
            "etichetta": etichetta.strip()[:80],
            "tipo": tipo,
            "durata": secondi,
            "creato": time.time(),
            "fine": fine if fine is not None else time.time() + secondi,
        }
        while t["id"] in self.timer:
            t["id"] = secrets.token_hex(2)
        self.timer[t["id"]] = t
        self._salva()
        self._programma(t)
        return t

    def annulla(self, chiave: str) -> list[dict]:
        chiave = chiave.strip()
        if chiave.lower() in ("tutti", "all", "*"):
            eliminati = list(self.timer.values())
        else:
            eliminati = [t for t in self.timer.values()
                         if t["id"] == chiave or (t["etichetta"] and normalizza(t["etichetta"]) == normalizza(chiave))]
        for t in eliminati:
            self.timer.pop(t["id"], None)
            task = self._task.pop(t["id"], None)
            if task:
                task.cancel()
        self._salva()
        return eliminati


TIMER = GestoreTimer()


def _descr_timer(t: dict) -> str:
    rim = t["fine"] - time.time()
    fine = datetime.fromtimestamp(t["fine"], TZ)
    nome = f"«{t['etichetta']}» " if t["etichetta"] else ""
    if t["tipo"] == "sveglia":
        return f"[{t['id']}] ⏰ Sveglia {nome}alle {fine:%H:%M} di {GIORNI[fine.weekday()]} {fine:%d/%m} (tra {fmt_durata(rim)})"
    return f"[{t['id']}] ⏲️ Timer {nome}di {fmt_durata(t['durata'])}: mancano {fmt_durata(rim)} (scade alle {fine:%H:%M:%S})"


@tool(
    "timer_imposta",
    title="Imposta timer o sveglia",
    description=(
        "Imposta un timer (parametro 'durata', es. '10m', '1h30m', '45 secondi', '2 ore e 15 minuti'; un numero "
        "senza unita' = minuti) OPPURE una sveglia/promemoria a un orario (parametro 'orario', es. '07:30', "
        "'domani 9:00', '2026-12-24 18:00'). Supporta piu' timer contemporanei: usa 'etichetta' per distinguerli "
        "(es. 'pasta'). Alla scadenza viene inviata una notifica tramite Home Assistant."
    ),
)
async def timer_imposta(durata: str = "", etichetta: str = "", orario: str = "") -> str:
    if orario.strip():
        dt, ha_ora = parse_data_ora(orario)
        if not ha_ora:
            raise UserError("Per la sveglia indica anche l'ora (es. 'domani 7:30').")
        if dt <= now():
            if _parse_ora(orario) is not None:  # solo ora: la prossima occorrenza
                dt += timedelta(days=1)
            else:
                raise UserError(f"L'orario {fmt_dt(dt)} e' gia' passato.")
        sec = int((dt - now()).total_seconds())
        t = TIMER.crea(sec, etichetta, "sveglia", fine=dt.timestamp())
    elif durata.strip():
        sec = parse_durata(durata)
        if sec < 1 or sec > 7 * 86400:
            raise UserError("La durata deve essere tra 1 secondo e 7 giorni.")
        t = TIMER.crea(sec, etichetta, "timer")
    else:
        raise UserError("Indica 'durata' (es. '10m') oppure 'orario' (es. '07:30').")
    altri = len(TIMER.timer) - 1
    return "Impostato: " + _descr_timer(t) + (f"\n(altri timer attivi: {altri})" if altri else "")


@tool(
    "timer_lista",
    title="Elenco timer",
    description="Mostra i timer e le sveglie attivi con il tempo rimanente, e quelli scaduti di recente. Usalo per 'quanto manca al timer?'.",
    read_only=True,
)
async def timer_lista() -> str:
    righe = []
    attivi = sorted(TIMER.timer.values(), key=lambda t: t["fine"])
    righe.append(f"Timer/sveglie attivi: {len(attivi)}")
    righe.extend(_descr_timer(t) for t in attivi)
    recenti = [t for t in TIMER.scaduti if t.get("fine", 0) > time.time() - 6 * 3600]
    if recenti:
        righe.append("\nScaduti nelle ultime 6 ore:")
        for t in recenti[-10:]:
            nome = f"«{t['etichetta']}» " if t["etichetta"] else ""
            righe.append(f"- {t['tipo']} {nome}scaduto alle {datetime.fromtimestamp(t['fine'], TZ):%H:%M}"
                         + (f" ({t['nota']})" if t.get("nota") else ""))
    return "\n".join(righe)


@tool(
    "timer_annulla",
    title="Annulla timer",
    description="Annulla un timer o una sveglia tramite id o etichetta (vedi timer_lista), oppure tutti con 'tutti'.",
    destructive=True,
)
async def timer_annulla(id_o_etichetta: str) -> str:
    eliminati = TIMER.annulla(id_o_etichetta)
    if not eliminati:
        raise UserError(f"Nessun timer attivo corrisponde a '{id_o_etichetta}'. Usa timer_lista.")
    return "Annullati:\n" + "\n".join(_descr_timer(t) for t in eliminati)
