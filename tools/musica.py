"""Strumenti: musica da YouTube stile Alexa, riprodotta sugli Echo tramite il bridge vocale."""

import asyncio
import contextlib
import re
import threading
import time
from dataclasses import dataclass
from typing import Literal

from nucleo import echo_bridge
from nucleo.configurazione import env_bool, env_int, logger
from nucleo.server import FUNZIONALITA, UserError, tool
from nucleo.utilita import fmt_durata, normalizza

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

try:
    from ytmusicapi import YTMusic
except ImportError:  # pragma: no cover
    YTMusic = None


MUSIC_START_TIMEOUT = env_int("MUSIC_START_TIMEOUT", 45)

# la musica suona solo sugli Echo: serve il bridge
FUNZIONALITA["music"] = env_bool("ENABLE_MUSIC", True) and yt_dlp is not None and echo_bridge.attivo()


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
        return (
            f"«{self.titolo}»"
            + (f" di {self.artista}" if self.artista else "")
            + (f" ({self.durata})" if self.durata else "")
        )


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
    def _ydl(extra: dict | None = None):
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": 15}
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
            return [
                Brano(
                    info["id"],
                    info.get("title", ""),
                    info.get("artist") or info.get("uploader", ""),
                    fmt_durata(info.get("duration") or 0),
                )
            ]
        ytm = self._ytmusic()
        if ytm is not None:
            for filtro in ("songs", "videos"):
                try:
                    res = [r for r in ytm.search(query, filter=filtro, limit=n) if r.get("videoId")]
                    if res:
                        return [
                            Brano(r["videoId"], r.get("title", ""), self._artisti(r), r.get("duration") or "")
                            for r in res[:n]
                        ]
                except Exception as e:
                    logger.warning(f"Ricerca YouTube Music fallita ({filtro}): {e}")
        with self._ydl({"extract_flat": True}) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
        return [
            Brano(
                e["id"],
                e.get("title", ""),
                e.get("channel") or e.get("uploader") or "",
                fmt_durata(e.get("duration") or 0) if e.get("duration") else "",
            )
            for e in (info.get("entries") or [])
            if e.get("id")
        ]

    def simili(self, video_id: str, n: int = 30) -> list[Brano]:
        ytm = self._ytmusic()
        if ytm is not None:
            try:
                res = ytm.get_watch_playlist(videoId=video_id, radio=True, limit=n)
                brani = [
                    Brano(t["videoId"], t.get("title", ""), self._artisti(t), t.get("length") or "")
                    for t in res.get("tracks", [])
                    if t.get("videoId")
                ]
                if brani:
                    return brani
            except Exception as e:
                logger.warning(f"Radio YouTube Music fallita: {e}")
        with self._ydl({"extract_flat": True, "noplaylist": False, "playlistend": n}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}", download=False)
        return [
            Brano(e["id"], e.get("title", ""), e.get("channel") or e.get("uploader") or "")
            for e in (info.get("entries") or [])
            if e.get("id")
        ]

    def stream(self, video_id: str) -> dict:
        with self._ydl({"format": "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best"}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return {
            "url": info["url"],
            "titolo": info.get("title", ""),
            "headers": info.get("http_headers") or {},
        }


class BackendEcho:
    """Riproduzione sull'Echo tramite il bridge (POST /api/music): il bridge decodifica l'URL con ffmpeg."""

    def __init__(self, echo: str, yt: YouTube) -> None:
        self.echo = echo  # vuoto = l'Echo dell'ultima richiesta vocale (fissato al primo play)
        self.yt = yt
        self._url_corrente = ""

    @property
    def nome(self) -> str:
        return self.echo or "Echo"

    async def _musica(self, azione: str, **dati) -> dict:
        try:
            return await echo_bridge.richiesta("/api/music", {"action": azione, "echo": self.echo, **dati})
        except echo_bridge.BridgeError as e:
            raise UserError(str(e)) from e

    async def riproduci(self, brano: Brano) -> None:
        info = await asyncio.to_thread(self.yt.stream, brano.video_id)
        self._url_corrente = info["url"]
        titolo = f"{brano.titolo} di {brano.artista}" if brano.artista else brano.titolo
        r = await self._musica("play", url=info["url"], headers=info.get("headers") or {}, title=titolo)
        if not self.echo and r.get("echo"):
            self.echo = r["echo"]  # i comandi successivi vanno allo stesso Echo

    async def _stato(self) -> dict:
        return await self._musica("status")

    async def stato(self) -> str:
        s = await self._stato()
        st = s.get("state", "")
        if st == "playing" and s.get("url") and self._url_corrente and s["url"] != self._url_corrente:
            return "altro"
        if st == "interrupted":  # in pausa per la voce o un annuncio: riprende da sola
            return "paused"
        return {"playing": "playing", "paused": "paused", "idle": "idle"}.get(st, "attesa")

    async def posizione(self) -> str:
        s = await self._stato()
        pos = s.get("position")
        testo = fmt_durata(pos) if pos else ""
        sl = s.get("sleep") or {}
        if sl.get("seconds_left"):
            testo += f"{' · ' if testo else ''}spegnimento tra {fmt_durata(sl['seconds_left'])}"
        return testo

    async def pausa(self) -> None:
        await self._musica("pause")

    async def riprendi(self) -> None:
        await self._musica("resume")

    async def stop(self) -> None:
        await self._musica("stop")

    async def volume(self) -> int:
        try:
            v = await echo_bridge.volume(self.echo)
        except echo_bridge.BridgeError:
            v = None
        return v if v is not None else 50

    async def imposta_volume(self, v: int) -> None:
        try:
            await echo_bridge.imposta_volume(v, self.echo)
        except echo_bridge.BridgeError as e:
            raise UserError(f"{e}. Usa i tasti del volume dell'Echo.") from e

    async def spegni_tra(self, minuti: float) -> None:
        await self._musica("sleep", minutes=minuti, fade=30)

    async def chiudi(self) -> None:
        pass


class LettoreMusicale:
    def __init__(self) -> None:
        self.yt = YouTube()
        self.coda: list[Brano] = []
        self.indice = -1
        self.backend: BackendEcho | None = None
        self.attivo = False
        self.radio = True
        self._lock = asyncio.Lock()
        self._monitor: asyncio.Task | None = None
        self._avvio = 0.0
        self._visto_play = False
        self._fallimenti = 0
        self._estendendo = False
        self._ids: set[str] = set()

    @property
    def corrente(self) -> Brano | None:
        return self.coda[self.indice] if 0 <= self.indice < len(self.coda) else None

    def avvia_monitor(self) -> None:
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.get_running_loop().create_task(self._ciclo_monitor())

    async def chiudi(self) -> None:
        if self._monitor:
            self._monitor.cancel()

    async def risolvi_echo(self, echo: str) -> BackendEcho:
        """Backend per l'Echo indicato ('' o 'echo' = quello che ha ricevuto l'ultima richiesta vocale)."""
        e = (echo or "").strip()
        n = normalizza(e)
        if not n or n in echo_bridge.PAROLE_ECHO:
            nome = echo_bridge.echo_predefinito()
        else:
            ricerca = n
            for parola in echo_bridge.PAROLE_ECHO:  # "alexa della cucina" -> "cucina"
                if ricerca.startswith(parola + " "):
                    ricerca = ricerca[len(parola) :].strip().removeprefix("della ").removeprefix("del ").strip()
                    break
            try:
                nome = await echo_bridge.trova_echo(n) or await echo_bridge.trova_echo(ricerca)
                if nome is None:
                    disponibili = ", ".join(x.get("name", "") for x in await echo_bridge.elenco_echo())
                    raise UserError(f"Nessun Echo corrisponde a '{e}'. Disponibili: {disponibili or 'nessuno'}")
            except echo_bridge.BridgeError as err:
                raise UserError(str(err)) from err
        if isinstance(self.backend, BackendEcho) and nome and self.backend.echo == nome:
            return self.backend
        return BackendEcho(nome, self.yt)

    async def spegni_tra(self, minuti: float) -> str:
        if self.backend is None:
            raise UserError("Nessuna riproduzione avviata.")
        await self.backend.spegni_tra(minuti)  # il bridge abbassa piano il volume prima di fermarsi
        if minuti <= 0:
            return "Spegnimento programmato annullato."
        return f"La musica si fermerà tra {fmt_durata(minuti * 60)}."

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

    async def riproduci(self, richiesta: str, echo: str, simili: bool) -> str:
        brani = await asyncio.to_thread(self.yt.cerca, richiesta, 5)
        if not brani:
            raise UserError(f"Nessun brano trovato su YouTube per '{richiesta}'.")
        nuovo_backend = await self.risolvi_echo(echo)
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
        return (
            f"In riproduzione: {brani[0].descr()} su {self.backend.nome}.\n"
            + (
                "Al termine continuerò con brani simili (radio)."
                if simili
                else "Riproduzione del solo brano richiesto."
            )
            + "\nAlternative trovate: "
            + "; ".join(b.descr() for b in brani[1:4])
        )

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
        return f"Ora in riproduzione: {self.corrente.descr()}"

    async def precedente(self) -> str:
        if self.indice <= 0:
            raise UserError("Non c'è un brano precedente.")
        self.indice -= 1
        await self._avvia_corrente()
        return f"Ora in riproduzione: {self.corrente.descr()}"

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
                    logger.info("[musica] l'Echo sta riproducendo altro: interrompo la coda")
                    self.attivo = False
                    continue
                if st != "idle":
                    continue
                trascorso = time.monotonic() - self._avvio
                if not self._visto_play and trascorso < MUSIC_START_TIMEOUT:
                    continue
                if not self._visto_play:
                    self._fallimenti += 1
                    logger.warning(f"[musica] il brano non è partito ({self._fallimenti}/3)")
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
        "e la suona sull'Echo; finito il brano, continua automaticamente con brani simili. 'echo': su quale Echo "
        "(nome, es. 'cucina'); vuoto = quello che ha ricevuto la richiesta. "
        "simili=false per riprodurre solo il brano richiesto."
    ),
    group="music",
)
async def musica_riproduci(richiesta: str, echo: str = "", simili: bool = True) -> str:
    if not richiesta.strip():
        raise UserError("Indica cosa riprodurre.")
    return await LETTORE.riproduci(richiesta.strip(), echo, simili)


@tool(
    "musica_controlla",
    title="Controlla musica",
    description=(
        "Controlla la riproduzione musicale in corso. azione: 'pausa', 'riprendi', 'stop', 'successivo' (salta), "
        "'precedente', 'volume' (valore 0-100), 'alza_volume' / 'abbassa_volume' (valore = passo, default 10), "
        "'radio_on' / 'radio_off' (continua o no con brani simili), 'spegni_tra' (valore = minuti, 0 annulla: "
        "ferma la musica dopo quel tempo, es. 'spegni la musica tra mezz'ora')."
    ),
    group="music",
)
async def musica_controlla(
    azione: Literal[
        "pausa",
        "riprendi",
        "stop",
        "successivo",
        "precedente",
        "volume",
        "alza_volume",
        "abbassa_volume",
        "radio_on",
        "radio_off",
        "spegni_tra",
    ],
    valore: int | None = None,
) -> str:
    L = LETTORE
    if L.backend is None:
        raise UserError("Nessuna riproduzione avviata.")
    if azione == "pausa":
        await L.backend.pausa()
        return "In pausa."
    if azione == "riprendi":
        if not L.attivo and L.corrente:
            async with L._lock:
                await L._avvia_corrente()
            return f"Riprendo: {L.corrente.descr()}"
        await L.backend.riprendi()
        L.attivo = True
        return "Riprendo la riproduzione."
    if azione == "stop":
        L.attivo = False
        await L.backend.stop()
        return "Riproduzione interrotta."
    if azione == "successivo":
        async with L._lock:
            return await L.successivo()
    if azione == "precedente":
        async with L._lock:
            return await L.precedente()
    if azione == "spegni_tra":
        if valore is None:
            raise UserError("Indica tra quanti minuti fermare la musica (valore).")
        return await L.spegni_tra(max(0, int(valore)))
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
    return f"Volume: {attuale}% → {nuovo}%"


@tool(
    "musica_aggiungi_in_coda",
    title="Aggiungi brano in coda",
    description=(
        "Aggiunge un brano da riprodurre subito dopo quello attuale (es. 'dopo metti Volare'). Se non c'è "
        "musica in corso, lo avvia."
    ),
    group="music",
)
async def musica_aggiungi_in_coda(richiesta: str) -> str:
    return await LETTORE.accoda(richiesta)


@tool(
    "musica_stato",
    title="Cosa sta suonando",
    description=(
        "Mostra il brano in riproduzione, Echo, stato, volume e i prossimi brani in coda. Usalo per "
        "'che canzone è questa?'."
    ),
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
    nomi = {
        "playing": "in riproduzione",
        "paused": "in pausa",
        "idle": "fermo",
        "attesa": "in caricamento",
        "altro": "l'Echo riproduce altro",
    }
    righe = [
        f"{L.corrente.descr()}",
        f"Stato: {nomi.get(st, st)}{(' · ' + pos) if pos else ''} · volume {vol}%",
        f"Echo: {L.backend.nome} · radio brani simili: {'sì' if L.radio else 'no'}",
    ]
    prossimi = L.coda[L.indice + 1 : L.indice + 11]
    if prossimi:
        righe.append("Prossimi:")
        righe.extend(f"  {i}. {b.descr()}" for i, b in enumerate(prossimi, 1))
    return "\n".join(righe)
