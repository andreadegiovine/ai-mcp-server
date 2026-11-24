"""Strumento: domande all'utente con opzioni o risposta libera (anche con moduli MCP)."""
import asyncio
import re
from typing import Any, Optional, Union

from mcp.server.fastmcp import Context

from nucleo.configurazione import ENABLE_ELICITATION, ELICITATION_TIMEOUT, logger
from nucleo.server import UserError, tool, client_supporta_moduli
from nucleo.utilita import fmt_durata


def _lista_opzioni(opzioni: Union[list[str], str, None]) -> list[str]:
    if not opzioni:
        return []
    voci = opzioni if isinstance(opzioni, list) else re.split(r"\s*[|;\n]\s*", str(opzioni))
    pulite: list[str] = []
    for v in voci:
        v = str(v).strip()
        if v and v not in pulite:
            pulite.append(v[:100])
    return pulite[:10]


def _modulo_domanda(opzioni: list[str], libera: bool, multipla: bool):
    """Crea lo schema del modulo per l'elicitation MCP (solo tipi primitivi, come da specifica)."""
    from pydantic import Field, create_model
    campi: dict[str, Any] = {}
    if opzioni and multipla:
        campi["scelte"] = (list[str] if not libera else Optional[list[str]],
                           Field(default=... if not libera else None, title="Scelte",
                                 json_schema_extra={"items": {"type": "string", "enum": opzioni}}))
    elif opzioni:
        campi["scelta"] = (str if not libera else Optional[str],
                           Field(default=... if not libera else None, title="Scelta",
                                 json_schema_extra={"enum": opzioni}))
    if libera:
        campi["risposta"] = (str if not opzioni else Optional[str],
                             Field(default=... if not opzioni else None,
                                   title="Risposta" if not opzioni else "Altro (risposta libera)"))
    return create_model("Risposta", **campi)


def _frase_domanda(domanda: str, opzioni: list[str], libera: bool, multipla: bool) -> str:
    """Frase pronta da dire all'utente: naturale, senza elenchi ne' simboli (adatta alla voce)."""
    d = domanda.strip().rstrip(":;,. ").strip()
    if not opzioni:
        return d if d.endswith("?") else d + "?"
    d = d.rstrip("?").strip()
    if len(opzioni) == 1:
        elenco = opzioni[0]
    else:
        elenco = ", ".join(opzioni[:-1]) + (" e " if multipla else " oppure ") + opzioni[-1]
    frase = f"{d}: {elenco}?"
    if multipla:
        frase += " Puoi sceglierne anche più di una."
    if libera:
        frase += " Oppure dimmi tu cosa preferisci."
    return frase


def _testo_domanda(domanda: str, opzioni: list[str], libera: bool, multipla: bool) -> str:
    righe = [
        "Fai questa domanda all'utente e poi fermati ad aspettare la sua risposta.",
        "Frase da dire, in italiano (usala così com'è):",
        _frase_domanda(domanda, opzioni, libera, multipla),
    ]
    if opzioni:
        tipo = "una o più" if multipla else "una"
        vincolo = "oppure una risposta libera" if libera else "solo tra queste"
        righe.append(f"Risposte valide ({tipo}, {vincolo}): " + " | ".join(opzioni))
    righe.append(
        "Regole: scrivi solo in italiano, senza parole di altre lingue; niente elenchi né simboli; "
        "non chiamare altri strumenti e non rispondere al posto dell'utente. "
        "Quando risponde, accetta anche frasi come 'la seconda', 'l'ultima' o parafrasi."
    )
    return "\n".join(righe)


@tool(
    "chiedi_all_utente",
    title="Fai una domanda",
    description=(
        "Fa una domanda all'utente e ne attende la risposta, con opzioni prefissate e/o risposta libera. "
        "Usalo quando devi far scegliere l'utente tra alternative (es. quale brano, quale evento, quale "
        "dispositivo), quando ti manca un'informazione indispensabile o per una conferma prima di un'azione "
        "importante. 'opzioni': elenco di scelte (lista oppure testo separato da '|'), al massimo 10; "
        "risposta_libera=false obbliga a scegliere tra le opzioni; scelta_multipla=true permette piu' scelte. "
        "Non usarlo per domande banali che puoi risolvere da solo."
    ),
    read_only=True,
)
async def chiedi_all_utente(domanda: str, opzioni: Union[list[str], str, None] = None,
                            risposta_libera: bool = True, scelta_multipla: bool = False,
                            ctx: Optional[Context] = None) -> str:
    domanda = (domanda or "").strip()
    if not domanda:
        raise UserError("La domanda e' vuota.")
    voci = _lista_opzioni(opzioni)
    libera = risposta_libera or not voci

    # Modulo interattivo (elicitation MCP), solo se abilitato e supportato dal client
    if client_supporta_moduli(ctx):
        try:
            esito = await asyncio.wait_for(
                ctx.elicit(domanda, _modulo_domanda(voci, libera, scelta_multipla)), ELICITATION_TIMEOUT)
        except asyncio.TimeoutError:
            return f"L'utente non ha risposto entro {fmt_durata(ELICITATION_TIMEOUT)} alla domanda: {domanda}"
        except Exception as e:
            logger.warning(f"Elicitation non riuscita, uso la domanda in chat: {e}")
        else:
            if esito.action != "accept":
                return ("L'utente ha preferito non rispondere alla domanda." if esito.action == "decline"
                        else "L'utente ha annullato la domanda.") + f" (Domanda: {domanda})"
            dati = esito.data.model_dump()
            parti = []
            if dati.get("scelte"):
                parti.append("ha scelto: " + ", ".join(dati["scelte"]))
            if dati.get("scelta"):
                parti.append(f"ha scelto: {dati['scelta']}")
            if dati.get("risposta"):
                parti.append(f"ha risposto: {dati['risposta']}")
            fuori_elenco = [x for x in ([dati.get("scelta")] + list(dati.get("scelte") or []))
                            if x and voci and x not in voci]
            if fuori_elenco:
                parti.append("(valori non presenti tra le opzioni: " + ", ".join(fuori_elenco) + ")")
            return f"Risposta dell'utente alla domanda '{domanda}': " + ("; ".join(parti) or "nessun valore")

    # Modalita' conversazionale: funziona con qualsiasi client e con la voce
    logger.info("[chiedi_all_utente] " + ("client senza supporto ai moduli interattivi: domanda in chat"
                                          if ENABLE_ELICITATION else "moduli disattivati: domanda in chat"))
    return _testo_domanda(domanda, voci, libera, scelta_multipla)
