"""Strumenti: rubrica dei contatti (email e numeri WhatsApp; 'io' indica l'utente)."""

import re
from typing import Literal

from nucleo.configurazione import DATA_DIR, env_str
from nucleo.server import UserError, tool
from nucleo.utilita import JsonStore, normalizza, now

# prefisso internazionale aggiunto ai numeri scritti senza '+'
WHATSAPP_PREFISSO = env_str("WHATSAPP_PREFISSO", "39").lstrip("+")


RUBRICA = JsonStore(DATA_DIR / "rubrica.json", [])
_ALIAS_IO = {
    "io",
    "me",
    "a me",
    "mio",
    "mia",
    "mie",
    "miei",
    "me stesso",
    "me stessa",
    "utente",
    "l utente",
    "il mio",
    "la mia",
    "mia email",
    "mio numero",
    "my",
    "myself",
}
_RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
RE_TELEFONO = re.compile(r"^[+\d][\d\s\-.()/]{5,}$")


def chiave_contatto(nome: str) -> str:
    n = normalizza(nome)
    return "io" if n in _ALIAS_IO else n


def nome_leggibile(chiave: str) -> str:
    return "te" if chiave == "io" else chiave


def normalizza_telefono(numero: str) -> str:
    """'340 343 4334' -> '+393403434334' (aggiunge WHATSAPP_PREFISSO se manca il prefisso)."""
    s = re.sub(r"[\s\-.()/]", "", numero or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        if s.startswith(WHATSAPP_PREFISSO) and len(s) > 10 + len(WHATSAPP_PREFISSO) - 1:
            s = "+" + s
        else:
            s = "+" + WHATSAPP_PREFISSO + s
    if not re.fullmatch(r"\+\d{8,15}", s):
        raise UserError(f"Numero di telefono non valido: '{numero}'.")
    return s


def telefono_leggibile(numero: str) -> str:
    pref = "+" + WHATSAPP_PREFISSO
    return numero[len(pref) :] if numero.startswith(pref) else numero


@tool(
    "rubrica_salva",
    title="Salva contatto",
    description=(
        "Salva o aggiorna un contatto nella rubrica (email e/o numero di telefono WhatsApp). Per i recapiti "
        "dell'utente stesso usa nome='io'. Usalo SUBITO quando l'utente ti comunica un indirizzo email o un "
        "numero, così la prossima volta non dovrai chiederlo. I campi non indicati restano invariati."
    ),
)
async def rubrica_salva(nome: str, email: str = "", telefono: str = "", note: str = "") -> str:
    chiave = chiave_contatto(nome)
    if not chiave:
        raise UserError("Indica il nome del contatto ('io' per l'utente).")
    email = email.strip()
    if email and not _RE_EMAIL.match(email):
        raise UserError(f"Indirizzo email non valido: '{email}'.")
    tel = normalizza_telefono(telefono) if telefono.strip() else ""
    if not email and not tel and not note.strip():
        raise UserError("Indica almeno un'email o un numero di telefono.")
    contatti = RUBRICA.load()
    c = next((x for x in contatti if x["chiave"] == chiave), None)
    if c is None:
        c = {
            "chiave": chiave,
            "nome": "io" if chiave == "io" else nome.strip(),
            "email": "",
            "telefono": "",
            "note": "",
            "creato": now().isoformat(timespec="seconds"),
        }
        contatti.append(c)
    if email:
        c["email"] = email
    if tel:
        c["telefono"] = tel
    if note.strip():
        c["note"] = note.strip()[:300]
    RUBRICA.save(contatti)
    return f"Contatto salvato: {_descr_contatto(c)}"


def _descr_contatto(c: dict) -> str:
    parti = [("io (l'utente)" if c["chiave"] == "io" else c["nome"])]
    if c.get("email"):
        parti.append(f"email {c['email']}")
    if c.get("telefono"):
        parti.append(f"telefono {telefono_leggibile(c['telefono'])}")
    if c.get("note"):
        parti.append(f"note: {c['note']}")
    return " · ".join(parti)


@tool(
    "rubrica_cerca",
    title="Cerca contatti",
    description=(
        "Cerca nella rubrica (nome, email, telefono, note). Senza testo elenca tutti i contatti. 'io' indica "
        "l'utente stesso."
    ),
    read_only=True,
)
async def rubrica_cerca(testo: str = "") -> str:
    contatti = RUBRICA.load()
    if testo.strip():
        toks = normalizza(testo).split()
        contatti = [
            c
            for c in contatti
            if all(
                t in normalizza(" ".join(str(c.get(k, "")) for k in ("chiave", "nome", "email", "telefono", "note")))
                for t in toks
            )
            or (chiave_contatto(testo) == "io" and c["chiave"] == "io")
        ]
    if not contatti:
        return "Nessun contatto" + (f" per '{testo}'." if testo.strip() else " in rubrica.")
    return f"{len(contatti)} contatti:\n" + "\n".join(f"- {_descr_contatto(c)}" for c in contatti)


@tool(
    "rubrica_elimina",
    title="Elimina contatto",
    description=(
        "Elimina un contatto dalla rubrica, oppure solo la sua email o il suo telefono (campo='email' o 'telefono')."
    ),
    destructive=True,
)
async def rubrica_elimina(nome: str, campo: Literal["tutto", "email", "telefono"] = "tutto") -> str:
    chiave = chiave_contatto(nome)
    contatti = RUBRICA.load()
    c = next((x for x in contatti if x["chiave"] == chiave), None)
    if c is None:
        raise UserError(f"Nessun contatto '{nome}' in rubrica.")
    if campo == "tutto":
        contatti.remove(c)
        esito = f"Contatto '{nome_leggibile(chiave)}' eliminato."
    else:
        c[campo] = ""
        esito = f"Rimosso il campo {campo} di '{nome_leggibile(chiave)}'."
    RUBRICA.save(contatti)
    return esito


def risolvi_destinatario(destinatario: str, campo: Literal["email", "telefono"]) -> tuple[str | None, str, str]:
    """
    Ritorna (recapito, chiave_contatto, messaggio). Se il recapito manca, il messaggio spiega al modello
    cosa chiedere all'utente e come salvare la risposta in rubrica.
    """
    d = (destinatario or "").strip() or "io"
    if campo == "email" and _RE_EMAIL.match(d):
        return d, d, ""
    if campo == "telefono" and RE_TELEFONO.match(d):
        return normalizza_telefono(d), d, ""
    chiave = chiave_contatto(d)
    contatti = RUBRICA.load()
    trovati = [c for c in contatti if c["chiave"] == chiave]
    if not trovati and chiave != "io":
        toks = chiave.split()
        trovati = [c for c in contatti if c["chiave"] != "io" and all(t in c["chiave"] for t in toks)]
    if len(trovati) > 1:
        nomi = ", ".join(c["nome"] for c in trovati)
        return (
            None,
            chiave,
            (
                f"Ci sono più contatti che corrispondono a '{d}': {nomi}. Chiedi all'utente quale "
                f"intende e poi richiama lo strumento con il nome esatto."
            ),
        )
    nome_tool = "invia_email" if campo == "email" else "invia_whatsapp"
    if trovati and trovati[0].get(campo):
        return trovati[0][campo], trovati[0]["chiave"], ""
    if trovati:
        chiave = trovati[0]["chiave"]
    cosa = "indirizzo email" if campo == "email" else "numero WhatsApp"
    if chiave == "io":
        frase = "A quale indirizzo email te la mando?" if campo == "email" else "A quale numero WhatsApp te lo mando?"
    else:
        frase = f"Qual è l'{cosa} di {trovati[0]['nome'] if trovati else d}?"
    nome_salva = "io" if chiave == "io" else (trovati[0]["nome"] if trovati else d)
    return (
        None,
        chiave,
        (
            f"Non ho un {cosa} per {nome_leggibile(chiave)}: l'invio non è stato preparato.\n"
            f"Frase da dire, in italiano (usala così com'è):\n{frase}\n"
            f"Quando l'utente risponde: 1) salva il recapito con rubrica_salva(nome='{nome_salva}', "
            f"{campo}='<recapito>'); 2) richiama {nome_tool} con destinatario='{nome_salva}' e gli stessi contenuti."
        ),
    )
