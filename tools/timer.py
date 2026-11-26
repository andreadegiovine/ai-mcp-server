"""Strumenti: timer e sveglie multipli e persistenti, che suonano sull'Echo (bridge vocale)."""

import asyncio
import contextlib
import secrets
import time
from collections import deque
from datetime import datetime, timedelta

from nucleo import echo_bridge
from nucleo.configurazione import DATA_DIR, TZ, env_bool, logger
from nucleo.server import FUNZIONALITA, UserError, tool
from nucleo.utilita import (
    GIORNI,
    JsonStore,
    _parse_ora,
    fmt_dt,
    fmt_durata,
    normalizza,
    now,
    parse_data_ora,
    parse_durata,
)

# timer e sveglie suonano sull'Echo: senza bridge non avrebbero modo di avvisare
FUNZIONALITA["timer"] = env_bool("ENABLE_TIMER", True) and echo_bridge.attivo()

GIORNI_RIPETIZIONE = {
    "feriali": "feriali",
    "lavorativi": "feriali",
    "weekend": "weekend",
    "fine settimana": "weekend",
    "tutti": "tutti",
    "ogni giorno": "tutti",
    "tutti i giorni": "tutti",
}


class GestoreTimer:
    def __init__(self) -> None:
        self.store = JsonStore(DATA_DIR / "timers.json", {"attivi": [], "scaduti": []})
        self.timer: dict[str, dict] = {}
        self.scaduti: deque = deque(maxlen=20)
        self._task: dict[str, asyncio.Task] = {}

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
        logger.info(f"{msg}")
        await self._notifica(t, msg)

    async def _notifica(self, t: dict, msg: str) -> None:
        """Se il timer suona già sull'Echo non serve altro; altrimenti lo annuncia a voce."""
        if echo_bridge.attivo() and not t.get("echo_id"):
            await echo_bridge.annuncia(msg.replace("«", "").replace("»", ""), t.get("echo", ""))

    def crea(self, secondi: int, etichetta: str, tipo: str, fine: float | None = None) -> dict:
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
            eliminati = [
                t
                for t in self.timer.values()
                if t["id"] == chiave or (t["etichetta"] and normalizza(t["etichetta"]) == normalizza(chiave))
            ]
        for t in eliminati:
            self.timer.pop(t["id"], None)
            task = self._task.pop(t["id"], None)
            if task:
                task.cancel()
        self._salva()
        return eliminati

    async def registra_echo(self, t: dict, echo: str = "") -> str:
        """Registra il timer/sveglia anche sull'Echo; ritorna una nota per la risposta ('' se tutto ok)."""
        if not echo_bridge.attivo():
            return ""
        nome = t["etichetta"] or ("Sveglia" if t["tipo"] == "sveglia" else "")
        echo = echo_bridge.echo_predefinito(echo)
        try:
            if t["tipo"] == "sveglia":
                fine = datetime.fromtimestamp(t["fine"], TZ)
                r = await echo_bridge.richiesta(
                    "/api/alarms",
                    {"action": "set", "time": f"{fine:%H:%M}", "date": f"{fine:%Y-%m-%d}", "name": nome, "echo": echo},
                )
                voci = r.get("alarms") or []
            else:
                secondi = max(1, int(round(t["fine"] - time.time())))
                r = await echo_bridge.richiesta(
                    "/api/timers", {"action": "start", "seconds": secondi, "name": nome, "echo": echo}
                )
                voci = r.get("timers") or []
        except echo_bridge.BridgeError as e:
            logger.warning(f"[timer] registrazione sull'Echo non riuscita: {e}")
            return f"(non registrato sull'Echo: {e}; alla scadenza provo ad annunciarlo)"
        t["echo"] = r.get("echo", echo)
        t["echo_id"] = voci[0].get("id") if voci else None
        if self.timer.get(t["id"]) is t:
            self._salva()
        return f"Sull'Echo {t['echo']}." if t["echo"] else ""

    async def annulla_echo(self, eliminati: list[dict]) -> None:
        for t in eliminati:
            if not t.get("echo_id") or not echo_bridge.attivo():
                continue
            percorso = "/api/alarms" if t["tipo"] == "sveglia" else "/api/timers"
            with contextlib.suppress(echo_bridge.BridgeError):
                await echo_bridge.richiesta(
                    percorso, {"action": "cancel", "name": t["echo_id"], "echo": t.get("echo", "")}
                )


TIMER = GestoreTimer()


async def _sveglie_ricorrenti_echo() -> list[dict]:
    """Sveglie ricorrenti sull'Echo (vivono solo sul bridge)."""
    if not echo_bridge.attivo():
        return []
    r = await echo_bridge.richiesta("/api/alarms", {"action": "list"}, metodo="GET")
    return [a for a in (r.get("alarms") or []) if a.get("days")]


def _giorni_bridge(giorni: str) -> str:
    g = normalizza(giorni)
    return GIORNI_RIPETIZIONE.get(g, giorni.strip())


def _descr_timer(t: dict) -> str:
    rim = t["fine"] - time.time()
    fine = datetime.fromtimestamp(t["fine"], TZ)
    nome = f"«{t['etichetta']}» " if t["etichetta"] else ""
    if t["tipo"] == "sveglia":
        giorno = f"{GIORNI[fine.weekday()]} {fine:%d/%m}"
        return f"[{t['id']}] Sveglia {nome}alle {fine:%H:%M} di {giorno} (tra {fmt_durata(rim)})"
    return (
        f"[{t['id']}] Timer {nome}di {fmt_durata(t['durata'])}: mancano {fmt_durata(rim)} (scade alle {fine:%H:%M:%S})"
    )


@tool(
    "timer_imposta",
    title="Imposta timer o sveglia",
    description=(
        "Imposta un timer (parametro 'durata', es. '10m', '1h30m', '45 secondi', '2 ore e 15 minuti'; un numero "
        "senza unità = minuti) OPPURE una sveglia/promemoria a un orario (parametro 'orario', es. '07:30', "
        "'domani 9:00', '2026-12-24 18:00'). Supporta più timer contemporanei: usa 'etichetta' per distinguerli "
        "(es. 'pasta'). Alla scadenza l'Echo suona con la sua suoneria. "
        "'giorni' rende la sveglia ricorrente sull'Echo ('feriali', 'weekend', "
        "'tutti' o 'lun,mer,ven'). 'echo': su quale Echo (vuoto = quello che ha ricevuto la richiesta)."
    ),
    group="timer",
)
async def timer_imposta(
    durata: str = "", etichetta: str = "", orario: str = "", giorni: str = "", echo: str = ""
) -> str:
    if giorni.strip():
        ora = _parse_ora(orario) if orario.strip() else None
        if ora is None:
            raise UserError("Per una sveglia ricorrente indica solo l'ora (es. orario='07:00', giorni='feriali').")
        try:
            r = await echo_bridge.richiesta(
                "/api/alarms",
                {
                    "action": "set",
                    "time": f"{ora[0]:02d}:{ora[1]:02d}",
                    "days": _giorni_bridge(giorni),
                    "name": etichetta.strip() or "Sveglia",
                    "echo": echo_bridge.echo_predefinito(echo),
                },
            )
        except echo_bridge.BridgeError as e:
            raise UserError(str(e)) from e
        a = (r.get("alarms") or [{}])[0]
        return (
            f"Sveglia ricorrente «{a.get('name', etichetta)}» alle {a.get('time')} ({_descr_giorni(a.get('days'))}) "
            f"sull'Echo {r.get('echo', '')}. Prossima: {a.get('next', '?')}."
        )
    if orario.strip():
        dt, ha_ora = parse_data_ora(orario)
        if not ha_ora:
            raise UserError("Per la sveglia indica anche l'ora (es. 'domani 7:30').")
        if dt <= now():
            if _parse_ora(orario) is not None:  # solo ora: la prossima occorrenza
                dt += timedelta(days=1)
            else:
                raise UserError(f"L'orario {fmt_dt(dt)} è già passato.")
        sec = int((dt - now()).total_seconds())
        t = TIMER.crea(sec, etichetta, "sveglia", fine=dt.timestamp())
    elif durata.strip():
        sec = parse_durata(durata)
        if sec < 1 or sec > 7 * 86400:
            raise UserError("La durata deve essere tra 1 secondo e 7 giorni.")
        t = TIMER.crea(sec, etichetta, "timer")
    else:
        raise UserError("Indica 'durata' (es. '10m') oppure 'orario' (es. '07:30').")
    nota = await TIMER.registra_echo(t, echo)
    altri = len(TIMER.timer) - 1
    return (
        "Impostato: "
        + _descr_timer(t)
        + (f" {nota}" if nota else "")
        + (f"\n(altri timer attivi: {altri})" if altri else "")
    )


def _descr_giorni(giorni) -> str:
    if isinstance(giorni, list | tuple):
        return ", ".join(str(g) for g in giorni)
    return str(giorni or "")


@tool(
    "timer_lista",
    title="Elenco timer",
    group="timer",
    description=(
        "Mostra i timer e le sveglie attivi con il tempo rimanente, e quelli scaduti di recente. Usalo per "
        "'quanto manca al timer?'."
    ),
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
            righe.append(
                f"- {t['tipo']} {nome}scaduto alle {datetime.fromtimestamp(t['fine'], TZ):%H:%M}"
                + (f" ({t['nota']})" if t.get("nota") else "")
            )
    try:
        ricorrenti = await _sveglie_ricorrenti_echo()
    except echo_bridge.BridgeError as e:
        ricorrenti = []
        righe.append(f"\n(Sveglie dell'Echo non leggibili: {e})")
    if ricorrenti:
        righe.append("\nSveglie ricorrenti sull'Echo:")
        righe.extend(
            f"- [{a.get('id')}] «{a.get('name')}» alle {a.get('time')} ({_descr_giorni(a.get('days'))}), "
            f"prossima {a.get('next')}"
            for a in ricorrenti
        )
    return "\n".join(righe)


@tool(
    "timer_annulla",
    title="Annulla timer",
    group="timer",
    description=(
        "Annulla un timer o una sveglia tramite id o etichetta (vedi timer_lista), oppure tutti con 'tutti'. "
        "Annulla anche le sveglie ricorrenti dell'Echo (per id o nome)."
    ),
    destructive=True,
)
async def timer_annulla(id_o_etichetta: str) -> str:
    eliminati = TIMER.annulla(id_o_etichetta)
    await TIMER.annulla_echo(eliminati)
    if eliminati:
        return "Annullati:\n" + "\n".join(_descr_timer(t) for t in eliminati)
    chiave = normalizza(id_o_etichetta)
    try:
        ricorrenti = await _sveglie_ricorrenti_echo()
    except echo_bridge.BridgeError:
        ricorrenti = []
    tolte = []
    for a in ricorrenti:
        if chiave in (normalizza(str(a.get("id", ""))), normalizza(a.get("name", ""))):
            with contextlib.suppress(echo_bridge.BridgeError):
                await echo_bridge.richiesta("/api/alarms", {"action": "cancel", "name": a["id"]})
                tolte.append(a)
    if tolte:
        return "Sveglie ricorrenti dell'Echo annullate: " + ", ".join(
            f"«{a.get('name')}» alle {a.get('time')}" for a in tolte
        )
    raise UserError(f"Nessun timer attivo corrisponde a '{id_o_etichetta}'. Usa timer_lista.")
