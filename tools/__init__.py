"""
Strumenti del server MCP: un modulo per gruppo, ognuno con le proprie costanti di configurazione.
L'ordine di import è l'ordine in cui i tool vengono registrati.
"""

from tools import (
    ambienti,
    calcolatrice,
    calendario_google,
    condivisione_file,
    data_ora,
    domande_utente,
    email_smtp,
    file_workspace,
    github,
    home_assistant,
    immagini,
    memoria,
    meteo,
    musica,
    pagine_web,
    ricerca_web,
    rubrica,
    terminale,
    timer,
    valute,
    whatsapp,
)

__all__ = [
    "data_ora",
    "calcolatrice",
    "ricerca_web",
    "pagine_web",
    "immagini",
    "meteo",
    "valute",
    "domande_utente",
    "memoria",
    "rubrica",
    "email_smtp",
    "whatsapp",
    "terminale",
    "ambienti",
    "file_workspace",
    "home_assistant",
    "timer",
    "musica",
    "calendario_google",
    "github",
    "condivisione_file",
]
