"""
Test end-to-end per pytest: un test per ogni area (web, GitHub, Home Assistant, sandbox, ...).

La suite vera e propria è in run_tests.py: avvia una volta sola Home Assistant, sito web, API e gateway finti e il
server MCP, poi chiama gli strumenti con un vero client MCP. Qui ogni area diventa un test separato, che fallisce
mostrando i controlli non superati. Anche "python tests/run_tests.py" continua a funzionare.
"""

import asyncio
import re
from pathlib import Path

import pytest

_SORGENTE = (Path(__file__).parent / "run_tests.py").read_text()
SEZIONI = list(dict.fromkeys(re.findall(r'sezione\("([a-z_]+)"\)', _SORGENTE)))
_risultati: list | None = None


def _esegui_suite() -> list:
    global _risultati
    if _risultati is None:
        import run_tests  # la configurazione dell'ambiente di prova avviene all'import

        _risultati = asyncio.run(run_tests.esegui())
    return _risultati


@pytest.mark.parametrize("sezione", SEZIONI)
def test_area(sezione):
    controlli = [r for r in _esegui_suite() if r[0] == sezione]
    falliti = [f"{nome}: {dettagli}" for _, nome, ok, dettagli in controlli if not ok]
    assert controlli, f"nessun controllo eseguito per l'area {sezione} (saltata?)"
    assert not falliti, "controlli non superati:\n" + "\n".join(falliti)
