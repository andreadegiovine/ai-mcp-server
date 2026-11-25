"""Strumento: cambio valuta con i tassi BCE (Frankfurter)."""

import asyncio
import re

from nucleo.rete import HTTP
from nucleo.server import UserError, tool

FRANKFURTER_URLS = ["https://api.frankfurter.dev/v1", "https://api.frankfurter.app"]


def _cambio(importo: float, da: str, a: str) -> str:
    ultimo_errore = None
    for base in FRANKFURTER_URLS:
        try:
            r = HTTP.get(f"{base}/latest", params={"base": da, "symbols": a}, timeout=10)
            if r.status_code == 404 or r.status_code == 422:
                raise UserError(f"Valuta non supportata ({da} o {a}). Usa codici ISO come EUR, USD, GBP, CHF.")
            r.raise_for_status()
            j = r.json()
            tasso = j["rates"][a]
            return (
                f"{importo:,.2f} {da} = {importo * tasso:,.2f} {a}\n"
                f"Tasso: 1 {da} = {tasso} {a} (BCE, aggiornato al {j.get('date')})"
            )
        except UserError:
            raise
        except Exception as e:
            ultimo_errore = e
    raise RuntimeError(f"Servizio cambi non raggiungibile: {ultimo_errore}")


@tool(
    "converti_valuta",
    title="Converti valuta",
    description=(
        "Converte un importo tra valute con i tassi ufficiali BCE aggiornati (codici ISO: EUR, USD, GBP, CHF, JPY...)."
    ),
    read_only=True,
)
async def converti_valuta(importo: float, da: str, a: str) -> str:
    da, a = da.strip().upper(), a.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", da) or not re.fullmatch(r"[A-Z]{3}", a):
        raise UserError("Usa codici valuta ISO di 3 lettere (es. EUR, USD).")
    if da == a:
        return f"{importo} {da} = {importo} {a}"
    return await asyncio.to_thread(_cambio, float(importo), da, a)
