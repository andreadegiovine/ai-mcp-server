"""Strumenti: memoria permanente delle informazioni sull'utente."""
import secrets

from nucleo.configurazione import DATA_DIR
from nucleo.server import UserError, tool
from nucleo.utilita import now, normalizza, JsonStore


MEMORIA = JsonStore(DATA_DIR / "memoria.json", [])


@tool(
    "memoria_salva",
    title="Salva in memoria",
    description=(
        "Salva in modo permanente un'informazione utile sull'utente o sul contesto (preferenze, nomi, "
        "abitudini, dati ricorrenti) per ricordarla nelle conversazioni future. Usa testi brevi e autonomi."
    ),
)
async def memoria_salva(testo: str, categoria: str = "generale") -> str:
    testo = testo.strip()
    if not testo:
        raise UserError("Testo vuoto.")
    if len(testo) > 2000:
        raise UserError("Testo troppo lungo (max 2000 caratteri).")
    dati = MEMORIA.load()
    for m in dati:
        if normalizza(m["testo"]) == normalizza(testo):
            return f"Gia' presente in memoria (id {m['id']})."
    voce = {"id": secrets.token_hex(3), "testo": testo, "categoria": categoria.strip().lower() or "generale",
            "creato": now().isoformat(timespec="seconds")}
    dati.append(voce)
    MEMORIA.save(dati)
    return f"Salvato in memoria (id {voce['id']}, categoria {voce['categoria']})."


@tool(
    "memoria_leggi",
    title="Leggi memoria",
    description=(
        "Legge le informazioni salvate in memoria. Senza parametri restituisce tutto; con 'cerca' filtra "
        "per parole chiave o categoria. Consultala all'inizio delle conversazioni personali."
    ),
    read_only=True,
)
async def memoria_leggi(cerca: str = "") -> str:
    dati = MEMORIA.load()
    if cerca.strip():
        toks = normalizza(cerca).split()
        dati = [m for m in dati if all(t in normalizza(m["testo"] + " " + m["categoria"]) for t in toks)]
    if not dati:
        return "Nessuna informazione in memoria" + (f" per '{cerca}'." if cerca else ".")
    righe = [f"[{m['id']}] ({m['categoria']}) {m['testo']}" for m in dati]
    return f"{len(dati)} voci in memoria:\n" + "\n".join(righe)


@tool(
    "memoria_elimina",
    title="Elimina dalla memoria",
    description="Elimina una voce dalla memoria tramite il suo id (vedi memoria_leggi).",
    destructive=True,
)
async def memoria_elimina(id: str) -> str:
    dati = MEMORIA.load()
    nuovi = [m for m in dati if m["id"] != id.strip()]
    if len(nuovi) == len(dati):
        raise UserError(f"Nessuna voce con id '{id}'.")
    MEMORIA.save(nuovi)
    return f"Voce {id} eliminata."
