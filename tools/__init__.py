"""
Strumenti del server MCP: un modulo per gruppo, ognuno con le proprie costanti di configurazione.
L'ordine di import e' l'ordine in cui i tool vengono registrati.
"""
from tools import (
    data_ora,
    calcolatrice,
    ricerca_web,
    pagine_web,
    immagini,
    meteo,
    valute,
    domande_utente,
    memoria,
    rubrica,
    email_smtp,
    whatsapp,
    terminale,
    file_workspace,
    home_assistant,
    timer,
    musica,
    calendario_google,
    github,
    condivisione_file,
)

__all__ = ["data_ora", "calcolatrice", "ricerca_web", "pagine_web", "immagini", "meteo", "valute", "domande_utente",
           "memoria", "rubrica", "email_smtp", "whatsapp", "terminale", "file_workspace", "home_assistant", "timer",
           "musica", "calendario_google", "github", "condivisione_file"]
