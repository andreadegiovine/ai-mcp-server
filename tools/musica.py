"""Strumenti: musica da YouTube stile Alexa (locale con mpv o su un media_player di Home Assistant)."""
import asyncio
import contextlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from nucleo.configurazione import _env, _env_bool, _env_int, DATA_DIR, logger
from nucleo.server import FUNZIONALITA, UserError, tool
from nucleo.utilita import normalizza, fmt_durata
from tools.home_assistant import (
    HASS,
    HASS_BLOCKED_ENTITIES,
    _RE_ENTITY,
    HassError,
    _hass,
    _match,
    cerca_entita,
)

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

try:
    from ytmusicapi import YTMusic
except ImportError:  # pragma: no cover
    YTMusic = None


MUSIC_DEFAULT_DEVICE = _env("MUSIC_DEFAULT_DEVICE", "locale")
MUSIC_MPV_AUDIO_DEVICE = _env("MUSIC_MPV_AUDIO_DEVICE")
MUSIC_MPV_AO = _env("MUSIC_MPV_AO")
MUSIC_DEFAULT_VOLUME = _env_int("MUSIC_DEFAULT_VOLUME", 60)
MUSIC_START_TIMEOUT = _env_int("MUSIC_START_TIMEOUT", 45)

FUNZIONALITA["music"] = _env_bool("ENABLE_MUSIC", True) and yt_dlp is not None


@dataclass
class Brano:
    video_id: str
    titolo: str
    artista: str = ""
    durata: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    def descr(self) -> str:
        return f"«{self.titolo}»" + (f" di {self.artista}" if self.artista else "") + \
            (f" ({self.durata})" if self.durata else "")


_RE_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


class YouTube:
    def __init__(self) -> None:
        self._ytm = None
        self._lock = threading.Lock()

    def _ytmusic(self):
        if YTMusic is None:
            return None
        with self._lock:
            if self._ytm is None:
                try:
                    self._ytm = YTMusic(language="it", location="IT")
                except Exception as e:
                    logger.warning(f"YTMusic non inizializzabile: {e}")
                    return None
            return self._ytm

    @staticmethod
    def _ydl(extra: Optional[dict] = None):
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                "socket_timeout": 15}
        opts.update(extra or {})
        return yt_dlp.YoutubeDL(opts)

    @staticmethod
    def _artisti(item: dict) -> str:
        arts = item.get("artists") or []
        return ", ".join(a.get("name", "") for a in arts if isinstance(a, dict) and a.get("name"))

    def cerca(self, query: str, n: int = 5) -> list[Brano]:
        m = _RE_YT_ID.search(query)
        if m:
            with self._ydl() as ydl:
                info = ydl.extract_info(m.group(1), download=False)
            return [Brano(info["id"], info.get("title", ""), info.get("artist") or info.get("uploader", ""),
                          fmt_durata(info.get("duration") or 0))]
        ytm = self._ytmusic()
        if ytm is not None:
            for filtro in ("songs", "videos"):
                try:
                    res = [r for r in ytm.search(query, filter=filtro, limit=n) if r.get("videoId")]
                    if res:
                        return [Brano(r["videoId"], r.get("title", ""), self._artisti(r), r.get("duration") or "")
                                for r in res[:n]]
                except Exception as e:
                    logger.warning(f"Ricerca YouTube Music fallita ({filtro}): {e}")
        with self._ydl({"extract_flat": True}) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
        return [Brano(e["id"], e.get("title", ""), e.get("channel") or e.get("uploader") or "",
                      fmt_durata(e.get("duration") or 0) if e.get("duration") else "")
                for e in (info.get("entries") or []) if e.get("id")]

    def simili(self, video_id: str, n: int = 30) -> list[Brano]:
        ytm = self._ytmusic()
        if ytm is not None:
            try:
                res = ytm.get_watch_playlist(videoId=video_id, radio=True, limit=n)
                brani = [Brano(t["videoId"], t.get("title", ""), self._artisti(t), t.get("length") or "")
                         for t in res.get("tracks", []) if t.get("videoId")]
                if brani:
                    return brani
            except Exception as e:
                logger.warning(f"Radio YouTube Music fallita: {e}")
        with self._ydl({"extract_flat": True, "noplaylist": False, "playlistend": n}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}", download=False)
        return [Brano(e["id"], e.get("title", ""), e.get("channel") or e.get("uploader") or "")
                for e in (info.get("entries") or []) if e.get("id")]

    def stream(self, video_id: str) -> dict:
        with self._ydl({"format": "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best"}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return {"url": info["url"], "titolo": info.get("title", ""), "thumb": info.get("thumbnail", ""),
                "mime": "audio/mp4" if info.get("ext") == "m4a" else "audio/webm"}


class BackendLocale:
    """Riproduzione locale con mpv (controllato via socket IPC JSON)."""

    nome = "locale (mpv)"

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.sock = DATA_DIR / "mpv.sock"
        self._rid = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def disponibile() -> bool:
        return shutil.which("mpv") is not None

    async def _assicura(self) -> None:
        if self.proc is not None and self.proc.returncode is None and self.sock.exists():
            return
        if not self.disponibile():
            raise UserError("mpv non installato: impossibile riprodurre in locale (apt install mpv) "
                            "oppure indica un media_player di Home Assistant come dispositivo.")
        self.sock.unlink(missing_ok=True)
        ytdl = shutil.which("yt-dlp") or str(Path(sys.executable).parent / "yt-dlp")
        args = ["mpv", "--idle=yes", "--no-video", "--no-terminal", "--really-quiet", "--force-window=no",
                f"--input-ipc-server={self.sock}", f"--volume={MUSIC_DEFAULT_VOLUME}",
                "--ytdl-format=bestaudio/best", f"--script-opts=ytdl_hook-ytdl_path={ytdl}"]
        if MUSIC_MPV_AUDIO_DEVICE:
            args.append(f"--audio-device={MUSIC_MPV_AUDIO_DEVICE}")
        if MUSIC_MPV_AO:
            args.append(f"--ao={MUSIC_MPV_AO}")
        self.proc = await asyncio.create_subprocess_exec(*args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                         stdin=subprocess.DEVNULL)
        for _ in range(50):
            if self.sock.exists():
                return
            await asyncio.sleep(0.1)
        raise RuntimeError("mpv non ha creato il socket IPC")

    async def _cmd(self, *comando) -> Any:
        async with self._lock:
            await self._assicura()
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(self.sock)), 3)
            try:
                self._rid += 1
                rid = self._rid
                writer.write((json.dumps({"command": list(comando), "request_id": rid}) + "\n").encode())
                await writer.drain()
                while True:
                    riga = await asyncio.wait_for(reader.readline(), 5)
                    if not riga:
                        raise RuntimeError("connessione mpv chiusa")
                    msg = json.loads(riga)
                    if msg.get("request_id") == rid:
                        if msg.get("error") not in (None, "success"):
                            raise RuntimeError(msg["error"])
                        return msg.get("data")
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    async def _prop(self, nome: str) -> Any:
        try:
            return await self._cmd("get_property", nome)
        except RuntimeError:
            return None

    async def riproduci(self, brano: Brano) -> None:
        await self._cmd("loadfile", brano.url, "replace")
        await self._cmd("set_property", "pause", False)

    async def stato(self) -> str:
        if self.proc is None or self.proc.returncode is not None:
            return "idle"
        if await self._prop("idle-active"):
            return "idle"
        return "paused" if await self._prop("pause") else "playing"

    async def posizione(self) -> str:
        pos, dur = await self._prop("time-pos"), await self._prop("duration")
        if pos is None:
            return ""
        return f"{fmt_durata(pos)}" + (f" / {fmt_durata(dur)}" if dur else "")

    async def pausa(self) -> None:
        await self._cmd("set_property", "pause", True)

    async def riprendi(self) -> None:
        await self._cmd("set_property", "pause", False)

    async def stop(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            await self._cmd("stop")

    async def volume(self) -> int:
        v = await self._prop("volume")
        return int(v) if v is not None else MUSIC_DEFAULT_VOLUME

    async def imposta_volume(self, v: int) -> None:
        await self._cmd("set_property", "volume", max(0, min(100, v)))

    async def chiudi(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._cmd("quit"), 2)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.proc.wait(), 3)
            if self.proc.returncode is None:
                self.proc.kill()
        self.sock.unlink(missing_ok=True)


class BackendHass:
    """Riproduzione su un media_player di Home Assistant (Chromecast, Sonos, Music Assistant...)."""

    def __init__(self, entity_id: str, yt: YouTube) -> None:
        self.entity_id = entity_id
        self.yt = yt
        self.nome = entity_id
        self._url_corrente = ""

    async def _servizio(self, servizio: str, **dati) -> None:
        await asyncio.to_thread(_hass().chiama_servizio, "media_player", servizio,
                                {"entity_id": self.entity_id, **dati})

    async def _stato_ha(self) -> dict:
        return await asyncio.to_thread(_hass().stato, self.entity_id)

    async def riproduci(self, brano: Brano) -> None:
        info = await asyncio.to_thread(self.yt.stream, brano.video_id)
        self._url_corrente = info["url"]
        extra = {"title": brano.titolo, "thumb": info.get("thumb", ""),
                 "metadata": {"metadataType": 3, "title": brano.titolo, "artist": brano.artista,
                              "images": [{"url": info.get("thumb", "")}] if info.get("thumb") else []}}
        try:
            await self._servizio("play_media", media_content_id=info["url"], media_content_type="music", extra=extra)
        except HassError:
            await self._servizio("play_media", media_content_id=info["url"], media_content_type="music")

    async def stato(self) -> str:
        s = await self._stato_ha()
        st = s.get("state", "")
        cid = (s.get("attributes") or {}).get("media_content_id")
        if st == "playing" and cid and self._url_corrente and cid != self._url_corrente:
            return "altro"  # qualcos'altro ha preso il controllo del dispositivo
        if st in ("idle", "off", "standby", "stopped"):
            return "idle"
        return {"playing": "playing", "paused": "paused"}.get(st, "attesa")

    async def posizione(self) -> str:
        a = (await self._stato_ha()).get("attributes") or {}
        pos, dur = a.get("media_position"), a.get("media_duration")
        return f"{fmt_durata(pos)} / {fmt_durata(dur)}" if pos is not None and dur else ""

    async def pausa(self) -> None:
        await self._servizio("media_pause")

    async def riprendi(self) -> None:
        await self._servizio("media_play")

    async def stop(self) -> None:
        await self._servizio("media_stop")

    async def volume(self) -> int:
        v = ((await self._stato_ha()).get("attributes") or {}).get("volume_level")
        return int(round(v * 100)) if v is not None else 50

    async def imposta_volume(self, v: int) -> None:
        await self._servizio("volume_set", volume_level=max(0, min(100, v)) / 100)

    async def chiudi(self) -> None:
        pass


class LettoreMusicale:
    def __init__(self) -> None:
        self.yt = YouTube()
        self.coda: list[Brano] = []
        self.indice = -1
        self.backend: Any = None
        self._locale: Optional[BackendLocale] = None
        self.attivo = False
        self.radio = True
        self._lock = asyncio.Lock()
        self._monitor: Optional[asyncio.Task] = None
        self._avvio = 0.0
        self._visto_play = False
        self._fallimenti = 0
        self._estendendo = False
        self._ids: set[str] = set()

    @property
    def corrente(self) -> Optional[Brano]:
        return self.coda[self.indice] if 0 <= self.indice < len(self.coda) else None

    def avvia_monitor(self) -> None:
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.get_running_loop().create_task(self._ciclo_monitor())

    async def chiudi(self) -> None:
        if self._monitor:
            self._monitor.cancel()
        if self._locale:
            await self._locale.chiudi()

    async def risolvi_dispositivo(self, dispositivo: str) -> Any:
        d = (dispositivo or MUSIC_DEFAULT_DEVICE or "locale").strip()
        if normalizza(d) in ("locale", "local", "server", "mpv", "questo dispositivo"):
            if self._locale is None:
                self._locale = BackendLocale()
            return self._locale
        if HASS is None:
            raise UserError("Per riprodurre su un altoparlante serve Home Assistant configurato.")
        if _RE_ENTITY.match(d):
            if not d.startswith("media_player."):
                raise UserError("Il dispositivo deve essere un media_player.")
            await asyncio.to_thread(HASS.stato, d)
        else:
            stati, aree = await asyncio.gather(asyncio.to_thread(HASS.stati), asyncio.to_thread(HASS.aree))
            trovati = cerca_entita(stati, aree, d, "media_player")
            if not trovati:
                disponibili = [e["entity_id"] for e in stati if e["entity_id"].startswith("media_player.")]
                raise UserError(f"Nessun altoparlante corrisponde a '{d}'. Disponibili: {', '.join(disponibili) or 'nessuno'}")
            d = trovati[0][1]["entity_id"]
        if _match(d, HASS_BLOCKED_ENTITIES):
            raise UserError(f"Il dispositivo {d} e' bloccato dalla configurazione.")
        if isinstance(self.backend, BackendHass) and self.backend.entity_id == d:
            return self.backend
        return BackendHass(d, self.yt)

    async def _avvia_corrente(self) -> None:
        brano = self.corrente
        self.attivo = True
        self._avvio = time.monotonic()
        self._visto_play = False
        logger.info(f"[musica] riproduco {brano.descr()} su {self.backend.nome}")
        await self.backend.riproduci(brano)

    async def _estendi_coda(self) -> None:
        if self._estendendo or not self.coda:
            return
        self._estendendo = True
        try:
            seme = self.coda[-1]
            nuovi = await asyncio.to_thread(self.yt.simili, seme.video_id, 30)
            aggiunti = 0
            titoli = {normalizza(b.titolo) for b in self.coda}
            for b in nuovi:
                if b.video_id in self._ids or normalizza(b.titolo) in titoli:
                    continue
                self.coda.append(b)
                self._ids.add(b.video_id)
                titoli.add(normalizza(b.titolo))
                aggiunti += 1
                if aggiunti >= 20:
                    break
            logger.info(f"[musica] aggiunti {aggiunti} brani simili a {seme.descr()}")
        except Exception as e:
            logger.warning(f"[musica] impossibile trovare brani simili: {e}")
        finally:
            self._estendendo = False

    async def riproduci(self, richiesta: str, dispositivo: str, simili: bool) -> str:
        brani = await asyncio.to_thread(self.yt.cerca, richiesta, 5)
        if not brani:
            raise UserError(f"Nessun brano trovato su YouTube per '{richiesta}'.")
        nuovo_backend = await self.risolvi_dispositivo(dispositivo)
        async with self._lock:
            if self.backend is not None and self.backend is not nuovo_backend and self.attivo:
                with contextlib.suppress(Exception):
                    await self.backend.stop()
            self.backend = nuovo_backend
            self.coda, self.indice, self.radio = [brani[0]], 0, simili
            self._ids = {brani[0].video_id}
            self._fallimenti = 0
            await self._avvia_corrente()
        self.avvia_monitor()
        if simili:
            asyncio.get_running_loop().create_task(self._estendi_coda())
        return (f"▶️ In riproduzione: {brani[0].descr()} su {self.backend.nome}.\n"
                + ("Al termine continuerò con brani simili (radio)." if simili else "Riproduzione del solo brano richiesto.")
                + "\nAlternative trovate: " + "; ".join(b.descr() for b in brani[1:4]))

    async def accoda(self, richiesta: str) -> str:
        brani = await asyncio.to_thread(self.yt.cerca, richiesta, 3)
        if not brani:
            raise UserError(f"Nessun brano trovato per '{richiesta}'.")
        b = brani[0]
        async with self._lock:
            if not self.attivo or self.corrente is None:
                pass
            else:
                self.coda.insert(self.indice + 1, b)
                self._ids.add(b.video_id)
                return f"Aggiunto come prossimo brano: {b.descr()}"
        return await self.riproduci(richiesta, "", self.radio)

    async def successivo(self) -> str:
        if not self.coda:
            raise UserError("Nessuna riproduzione in corso.")
        if self.indice + 1 >= len(self.coda) and self.radio:
            await self._estendi_coda()
        if self.indice + 1 >= len(self.coda):
            self.attivo = False
            with contextlib.suppress(Exception):
                await self.backend.stop()
            return "Coda terminata: non ci sono altri brani."
        self.indice += 1
        await self._avvia_corrente()
        if self.radio and len(self.coda) - self.indice <= 3:
            asyncio.get_running_loop().create_task(self._estendi_coda())
        return f"⏭️ Ora in riproduzione: {self.corrente.descr()}"

    async def precedente(self) -> str:
        if self.indice <= 0:
            raise UserError("Non c'e' un brano precedente.")
        self.indice -= 1
        await self._avvia_corrente()
        return f"⏮️ Ora in riproduzione: {self.corrente.descr()}"

    async def _ciclo_monitor(self) -> None:
        while True:
            try:
                await asyncio.sleep(2)
                if not self.attivo or self.backend is None:
                    continue
                st = await self.backend.stato()
                if st == "playing":
                    self._visto_play = True
                    self._fallimenti = 0
                    continue
                if st == "altro":
                    logger.info("[musica] il dispositivo sta riproducendo altro: interrompo la coda")
                    self.attivo = False
                    continue
                if st != "idle":
                    continue
                trascorso = time.monotonic() - self._avvio
                if not self._visto_play and trascorso < MUSIC_START_TIMEOUT:
                    continue
                if not self._visto_play:
                    self._fallimenti += 1
                    logger.warning(f"[musica] il brano non e' partito ({self._fallimenti}/3)")
                    if self._fallimenti >= 3:
                        self.attivo = False
                        continue
                async with self._lock:
                    if self.attivo:
                        msg = await self.successivo()
                        logger.info(f"[musica] {msg}")
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"[musica] errore nel monitor: {e}")
                await asyncio.sleep(5)


LETTORE = LettoreMusicale()


@tool(
    "musica_riproduci",
    title="Riproduci musica",
    description=(
        "Riproduce musica da YouTube come un assistente vocale: cerca il brano/artista/genere richiesto "
        "(es. 'Bohemian Rhapsody Queen', 'musica jazz rilassante', 'ultimo singolo di Elodie' o un link YouTube) "
        "e, finito il brano, continua automaticamente con brani simili. 'dispositivo': 'locale' oppure il nome/"
        "entity_id di un altoparlante di Home Assistant (es. 'cucina' o 'media_player.soggiorno'); vuoto = predefinito. "
        "simili=false per riprodurre solo il brano richiesto."
    ),
    group="music",
)
async def musica_riproduci(richiesta: str, dispositivo: str = "", simili: bool = True) -> str:
    if not richiesta.strip():
        raise UserError("Indica cosa riprodurre.")
    return await LETTORE.riproduci(richiesta.strip(), dispositivo, simili)


@tool(
    "musica_controlla",
    title="Controlla musica",
    description=(
        "Controlla la riproduzione musicale in corso. azione: 'pausa', 'riprendi', 'stop', 'successivo' (salta), "
        "'precedente', 'volume' (valore 0-100), 'alza_volume' / 'abbassa_volume' (valore = passo, default 10), "
        "'radio_on' / 'radio_off' (continua o no con brani simili)."
    ),
    group="music",
)
async def musica_controlla(
    azione: Literal["pausa", "riprendi", "stop", "successivo", "precedente", "volume",
                    "alza_volume", "abbassa_volume", "radio_on", "radio_off"],
    valore: Optional[int] = None,
) -> str:
    L = LETTORE
    if L.backend is None:
        raise UserError("Nessuna riproduzione avviata.")
    if azione == "pausa":
        await L.backend.pausa()
        return "⏸️ In pausa."
    if azione == "riprendi":
        if not L.attivo and L.corrente:
            async with L._lock:
                await L._avvia_corrente()
            return f"▶️ Riprendo: {L.corrente.descr()}"
        await L.backend.riprendi()
        L.attivo = True
        return "▶️ Riprendo la riproduzione."
    if azione == "stop":
        L.attivo = False
        await L.backend.stop()
        return "⏹️ Riproduzione interrotta."
    if azione == "successivo":
        async with L._lock:
            return await L.successivo()
    if azione == "precedente":
        async with L._lock:
            return await L.precedente()
    if azione in ("radio_on", "radio_off"):
        L.radio = azione == "radio_on"
        if L.radio:
            asyncio.get_running_loop().create_task(L._estendi_coda())
        return "Riproduzione continua di brani simili " + ("attivata." if L.radio else "disattivata.")
    attuale = await L.backend.volume()
    if azione == "volume":
        if valore is None:
            return f"Volume attuale: {attuale}%"
        nuovo = int(valore)
    else:
        passo = abs(int(valore)) if valore else 10
        nuovo = attuale + passo if azione == "alza_volume" else attuale - passo
    nuovo = max(0, min(100, nuovo))
    await L.backend.imposta_volume(nuovo)
    return f"🔊 Volume: {attuale}% → {nuovo}%"


@tool(
    "musica_aggiungi_in_coda",
    title="Aggiungi brano in coda",
    description="Aggiunge un brano da riprodurre subito dopo quello attuale (es. 'dopo metti Volare'). Se non c'e' musica in corso, lo avvia.",
    group="music",
)
async def musica_aggiungi_in_coda(richiesta: str) -> str:
    return await LETTORE.accoda(richiesta)


@tool(
    "musica_stato",
    title="Cosa sta suonando",
    description="Mostra il brano in riproduzione, dispositivo, stato, volume e i prossimi brani in coda. Usalo per 'che canzone e' questa?'.",
    group="music",
    read_only=True,
)
async def musica_stato() -> str:
    L = LETTORE
    if L.backend is None or L.corrente is None:
        return "Nessuna musica in riproduzione."
    try:
        st, pos, vol = await asyncio.gather(L.backend.stato(), L.backend.posizione(), L.backend.volume())
    except Exception as e:
        st, pos, vol = f"sconosciuto ({e})", "", "?"
    nomi = {"playing": "in riproduzione", "paused": "in pausa", "idle": "fermo", "attesa": "in caricamento",
            "altro": "il dispositivo riproduce altro"}
    righe = [f"🎵 {L.corrente.descr()}", f"Stato: {nomi.get(st, st)}{(' · ' + pos) if pos else ''} · volume {vol}%",
             f"Dispositivo: {L.backend.nome} · radio brani simili: {'sì' if L.radio else 'no'}"]
    prossimi = L.coda[L.indice + 1: L.indice + 11]
    if prossimi:
        righe.append("Prossimi:")
        righe.extend(f"  {i}. {b.descr()}" for i, b in enumerate(prossimi, 1))
    return "\n".join(righe)
