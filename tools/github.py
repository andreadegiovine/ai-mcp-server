"""
Strumenti: GitHub (clone, stato, commit, push e pull request) sui soli repository autorizzati.

Sicurezza:
- si lavora solo sui repository elencati in GITHUB_REPO_AUTORIZZATI (sono ammessi pattern come "utente/*");
- il token non viene mai scritto su disco né passato ai comandi dell'agente: lo usa solo il server, nella riga di
  comando di clone e push e nelle chiamate alle API;
- commit e comandi locali girano nella sandbox del terminale, senza token;
- prima del push il server controlla la configurazione git del repository (che l'agente può modificare): chiavi
  che eseguono programmi o reindirizzano le connessioni (hook, proxy, filtri, include...) bloccano il push;
- push e pull request richiedono SEMPRE la conferma dell'utente; il codice di conferma è legato al commit
  preparato: se nel frattempo il repository cambia, il push viene rifiutato. Il push forzato non è previsto.
"""

import asyncio
import base64
import fnmatch
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import requests
from mcp.server.fastmcp import Context

from nucleo.configurazione import env_bool, env_lista, env_str, logger
from nucleo.invii import chiedi_conferma, prendi_attesa, registra_invio
from nucleo.sandbox import esegui_comando
from nucleo.server import FUNZIONALITA, UserError, tool
from nucleo.utilita import tronca
from nucleo.workspace import IdConversazione, cartella_lavoro, env_sicuro, percorso_workspace, relativo

GITHUB_TOKEN = env_str("GITHUB_TOKEN")
GITHUB_REPO_AUTORIZZATI = env_lista("GITHUB_REPO_AUTORIZZATI")
GITHUB_AUTORE_NOME = env_str("GITHUB_AUTORE_NOME", "Assistente MCP")
GITHUB_AUTORE_EMAIL = env_str("GITHUB_AUTORE_EMAIL", "assistente-mcp@users.noreply.github.com")
GITHUB_URL = env_str("GITHUB_URL", "https://github.com").rstrip("/")
GITHUB_API = env_str("GITHUB_API", "https://api.github.com").rstrip("/")
GIT_TIMEOUT = 300

FUNZIONALITA["github"] = (
    env_bool("ENABLE_GITHUB", True)
    and bool(GITHUB_TOKEN and GITHUB_REPO_AUTORIZZATI)
    and shutil.which("git") is not None
)

_RE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_RE_RAMO = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//)(?!.*@\{)[A-Za-z0-9._/-]{1,200}(?<![./])$")

# Chiavi ammesse nella configurazione locale del repository al momento del push: tutte le altre (hook, proxy,
# filtri, programmi esterni, include, url.*.insteadOf, credential.*, http.*, ...) bloccano il push.
_CONFIG_AMMESSE = [
    "core.repositoryformatversion",
    "core.filemode",
    "core.bare",
    "core.logallrefupdates",
    "core.ignorecase",
    "core.precomposeunicode",
    "core.symlinks",
    "core.autocrlf",
    "core.eol",
    "core.quotepath",
    "remote.origin.url",
    "remote.origin.fetch",
    "remote.origin.tagopt",
    "branch.*.remote",
    "branch.*.merge",
    "branch.*.rebase",
    "user.name",
    "user.email",
    "pull.rebase",
    "pull.ff",
    "init.defaultbranch",
    "gc.auto",
    "advice.*",
]


def repo_autorizzato(repo: str) -> bool:
    return bool(_RE_REPO.match(repo)) and any(fnmatch.fnmatch(repo.lower(), p.lower()) for p in GITHUB_REPO_AUTORIZZATI)


def _controlla_repo(repo: str) -> str:
    repo = repo.strip().removesuffix(".git")
    m = re.match(r"^(?:https?://github\.com/)?([^/\s]+/[^/\s]+)$", repo)
    repo = m.group(1) if m else repo
    if not _RE_REPO.match(repo):
        raise UserError(f"Nome di repository non valido: '{repo}' (formato atteso: proprietario/nome).")
    if not repo_autorizzato(repo):
        raise UserError(
            f"Il repository {repo} non è tra quelli autorizzati (GITHUB_REPO_AUTORIZZATI: "
            f"{', '.join(GITHUB_REPO_AUTORIZZATI) or 'nessuno'})."
        )
    return repo


def _url_repo(repo: str) -> str:
    return f"{GITHUB_URL}/{repo}.git"


def _protocolli_git() -> list[str]:
    """Solo il protocollo di GITHUB_URL è ammesso (https in produzione)."""
    schema = GITHUB_URL.split("://", 1)[0]
    return ["-c", "protocol.allow=never", "-c", f"protocol.{schema}.allow=always"]


def _git_rete(argomenti: list[str], cwd: Path) -> tuple[int, str]:
    """git con il token (clone, push): fuori dalla sandbox, senza config globale né hook né credential helper."""
    credenziali = base64.b64encode(f"x-access-token:{GITHUB_TOKEN}".encode()).decode()
    env = env_sicuro()
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "true",
            "SSH_ASKPASS": "true",
        }
    )
    comando = [
        "git",
        "-c",
        "core.hooksPath=" + os.devnull,
        "-c",
        "credential.helper=",
        "-c",
        "core.fsmonitor=false",
        *_protocolli_git(),
        "-c",
        f"http.extraHeader=Authorization: Basic {credenziali}",
        *argomenti,
    ]
    try:
        r = subprocess.run(comando, cwd=str(cwd), capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return -9, "Operazione git troppo lunga, interrotta."
    uscita = (
        (r.stdout + r.stderr).replace(credenziali, "***").replace(GITHUB_TOKEN, "***")
        if GITHUB_TOKEN
        else r.stdout + r.stderr
    )
    return r.returncode, uscita.strip()


async def _git_locale(argomenti: list[str], repo_dir: Path, base: Path, timeout: int = 120) -> tuple[int, str]:
    """git senza token, nella sandbox del terminale (stato, log, add, commit)."""
    comando = shlex.join(
        [
            "git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            f"user.name={GITHUB_AUTORE_NOME}",
            "-c",
            f"user.email={GITHUB_AUTORE_EMAIL}",
            *argomenti,
        ]
    )
    codice, uscita, _ = await esegui_comando(comando, repo_dir, timeout, base)
    return codice, uscita.strip()


def _cartella_repo(cartella: str, id_conversazione: str | None) -> tuple[Path, Path]:
    base = cartella_lavoro(id_conversazione)
    repo_dir = percorso_workspace(cartella or ".", base)
    git_dir = repo_dir / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        raise UserError(f"'{cartella}' non è un repository git clonato nel workspace (usa github_clona).")
    if (git_dir / "commondir").exists():
        raise UserError("I worktree git collegati non sono supportati.")
    return repo_dir, base


def _config_repo(repo_dir: Path) -> list[tuple[str, str]]:
    r = subprocess.run(
        ["git", "config", "--file", str(repo_dir / ".git" / "config"), "--list", "--null"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**env_sicuro(), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    if r.returncode != 0:
        raise UserError("Configurazione git del repository non leggibile.")
    voci = []
    for voce in filter(None, r.stdout.split("\0")):
        chiave, _, valore = voce.partition("\n")
        voci.append((chiave.lower(), valore))
    return voci


def controlla_config_per_push(repo_dir: Path) -> str:
    """Verifica la configurazione locale prima di usare il token; ritorna il repository 'proprietario/nome'."""
    voci = _config_repo(repo_dir)
    vietate = [k for k, _ in voci if not any(fnmatch.fnmatch(k, a) for a in _CONFIG_AMMESSE)]
    if vietate:
        raise UserError(
            "Push bloccato per sicurezza: la configurazione git del repository contiene impostazioni "
            f"non ammesse ({', '.join(sorted(set(vietate)))}). Rimuovile con 'git config --unset'."
        )
    url = next((v for k, v in voci if k == "remote.origin.url"), "")
    prefisso = GITHUB_URL + "/"
    if not url.startswith(prefisso):
        raise UserError(f"Il remote 'origin' non punta a {GITHUB_URL}: push bloccato.")
    return _controlla_repo(url[len(prefisso) :])


async def _ramo_corrente(repo_dir: Path, base: Path) -> str:
    codice, uscita = await _git_locale(["rev-parse", "--abbrev-ref", "HEAD"], repo_dir, base)
    if codice != 0 or uscita == "HEAD":
        raise UserError("Il repository non è su un ramo (HEAD staccato): crea o seleziona un ramo.")
    return uscita.splitlines()[-1]


@tool(
    "github_clona",
    title="Clona un repository GitHub",
    description=(
        "Clona nel workspace un repository GitHub autorizzato (es. 'utente/progetto'), opzionalmente su un ramo. "
        "Poi puoi modificarlo con i tool sui file o con il terminale, e salvare le modifiche con github_commit e "
        "github_push. Facoltativo: id_conversazione per lavorare nella cartella della conversazione."
    ),
    group="github",
)
async def github_clona(repo: str, ramo: str = "", cartella: str = "", id_conversazione: IdConversazione = None) -> str:
    repo = _controlla_repo(repo)
    base = cartella_lavoro(id_conversazione)
    dest = percorso_workspace(cartella.strip() or repo.split("/")[1], base)
    if dest.exists() and any(dest.iterdir()):
        raise UserError(f"La cartella {relativo(dest, base)} esiste già e non è vuota.")
    argomenti = ["clone", "--no-tags"]
    if ramo.strip():
        if not _RE_RAMO.match(ramo.strip()):
            raise UserError(f"Nome di ramo non valido: '{ramo}'.")
        argomenti += ["--branch", ramo.strip()]
    codice, uscita = await asyncio.to_thread(_git_rete, [*argomenti, _url_repo(repo), str(dest)], base)
    if codice != 0:
        raise UserError(f"Clone di {repo} non riuscito: {tronca(uscita, 600)}")
    logger.info(f"[github] clonato {repo} in {dest}")
    attuale = await _ramo_corrente(dest, base)
    return f"Clonato {repo} in {relativo(dest, base)} (ramo {attuale})."


@tool(
    "github_stato",
    title="Stato del repository",
    description=(
        "Mostra lo stato di un repository clonato: ramo, file modificati, ultimi commit e commit non ancora "
        "inviati a GitHub. 'cartella' è la cartella del repository nel workspace."
    ),
    group="github",
    read_only=True,
)
async def github_stato(cartella: str, id_conversazione: IdConversazione = None) -> str:
    repo_dir, base = _cartella_repo(cartella, id_conversazione)
    ramo = await _ramo_corrente(repo_dir, base)
    _, stato = await _git_locale(["status", "--short"], repo_dir, base)
    _, storia = await _git_locale(["log", "--oneline", "-8"], repo_dir, base)
    codice, da_inviare = await _git_locale(["log", "--oneline", f"origin/{ramo}..HEAD"], repo_dir, base)
    righe = [
        f"Repository {relativo(repo_dir, base)} · ramo {ramo}",
        "File modificati:\n" + (stato or "(nessuno)"),
        "Ultimi commit:\n" + (storia or "(nessuno)"),
    ]
    if codice == 0:
        righe.append("Da inviare con github_push:\n" + (da_inviare or "(nessuno)"))
    else:
        righe.append(f"Il ramo {ramo} non esiste ancora su GitHub: github_push lo creerà.")
    return "\n\n".join(righe)


@tool(
    "github_commit",
    title="Salva un commit",
    description=(
        "Registra le modifiche di un repository clonato in un commit locale. Senza 'file' include tutte le "
        "modifiche; altrimenti solo i file indicati (percorsi relativi al repository, separati da virgola). "
        "Il commit resta locale finché non usi github_push."
    ),
    group="github",
)
async def github_commit(cartella: str, messaggio: str, file: str = "", id_conversazione: IdConversazione = None) -> str:
    repo_dir, base = _cartella_repo(cartella, id_conversazione)
    if not messaggio.strip():
        raise UserError("Il messaggio del commit è obbligatorio.")
    elenco = [f.strip() for f in file.split(",") if f.strip()]
    for f in elenco:
        percorso_workspace(f, repo_dir)  # niente percorsi fuori dal repository
    codice, uscita = await _git_locale(["add", "--", *elenco] if elenco else ["add", "-A"], repo_dir, base)
    if codice != 0:
        raise UserError(f"git add non riuscito: {tronca(uscita, 600)}")
    codice, uscita = await _git_locale(["commit", "-m", messaggio.strip()], repo_dir, base)
    if codice != 0:
        if "nothing to commit" in uscita or "nothing added" in uscita:
            return "Nessuna modifica da salvare."
        raise UserError(f"Commit non riuscito: {tronca(uscita, 600)}")
    _, ultimo = await _git_locale(["log", "--oneline", "-1", "--stat"], repo_dir, base)
    return f"Commit creato (locale, non ancora su GitHub):\n{tronca(ultimo, 3000)}"


@tool(
    "github_push",
    title="Invia i commit a GitHub",
    description=(
        "Invia a GitHub i commit locali di un repository clonato, sul ramo corrente o su 'ramo' (creato se non "
        "esiste). Richiede SEMPRE la conferma dell'utente: la prima chiamata prepara il push e restituisce la domanda "
        "di conferma; solo dopo il sì dell'utente richiama con il codice_conferma ricevuto. Il push forzato non è "
        "consentito."
    ),
    group="github",
    destructive=True,
)
async def github_push(
    cartella: str = "",
    ramo: str = "",
    codice_conferma: str = "",
    id_conversazione: IdConversazione = None,
    ctx: Context | None = None,
) -> str:
    if codice_conferma.strip():
        dati = prendi_attesa(codice_conferma, "github_push")
        repo_dir, base = Path(dati["repo_dir"]), Path(dati["base"])
        repo = await asyncio.to_thread(controlla_config_per_push, repo_dir)
        _, testa = await _git_locale(["rev-parse", "HEAD"], repo_dir, base)
        if repo != dati["repo"] or testa.splitlines()[-1] != dati["sha"]:
            raise UserError("Il repository è cambiato dopo la conferma: push annullato. Prepara di nuovo il push.")
    else:
        repo_dir, base = _cartella_repo(cartella, id_conversazione)
        repo = await asyncio.to_thread(controlla_config_per_push, repo_dir)
        destinazione = ramo.strip() or await _ramo_corrente(repo_dir, base)
        if not _RE_RAMO.match(destinazione):
            raise UserError(f"Nome di ramo non valido: '{destinazione}'.")
        _, testa = await _git_locale(["rev-parse", "HEAD"], repo_dir, base)
        codice, commit = await _git_locale(["log", "--oneline", f"origin/{destinazione}..HEAD"], repo_dir, base)
        if codice != 0:
            _, commit = await _git_locale(["log", "--oneline", "-10"], repo_dir, base)
            nuovo = True
        else:
            nuovo = False
            if not commit:
                return f"Niente da inviare: il ramo {destinazione} su GitHub è già aggiornato."
        n = len(commit.splitlines())
        dati = {
            "repo_dir": str(repo_dir),
            "base": str(base),
            "repo": repo,
            "ramo": destinazione,
            "sha": testa.splitlines()[-1],
        }
        cosa = "del nuovo ramo" if nuovo else (f"di {n} commit sul ramo" if n != 1 else "di un commit sul ramo")
        frase = f"Faccio il push {cosa} {destinazione} del repository {repo}?"
        anteprima = (
            f"Repository: {repo}\nRamo: {destinazione}{' (nuovo)' if nuovo else ''}\nCommit:\n{tronca(commit, 1500)}"
        )
        attesa = await chiedi_conferma(ctx, "github_push", dati, frase, anteprima, "github_push")
        if attesa is not None:
            return attesa
    codice, uscita = await asyncio.to_thread(
        _git_rete, ["push", "--no-verify", _url_repo(dati["repo"]), f"HEAD:refs/heads/{dati['ramo']}"], repo_dir
    )
    if codice != 0:
        raise UserError(f"Push non riuscito: {tronca(uscita, 800)}")
    # il push è riuscito: aggiorna localmente origin/<ramo>, senza rete (la sandbox non ha il token)
    await _git_locale(["update-ref", f"refs/remotes/origin/{dati['ramo']}", dati["sha"]], repo_dir, base)
    registra_invio(f"GITHUB push {dati['repo']} ramo={dati['ramo']} sha={dati['sha'][:12]}")
    return f"Push completato su {dati['repo']}, ramo {dati['ramo']} ({dati['sha'][:7]})."


def _api(metodo: str, percorso: str, **kw) -> dict:
    try:
        r = requests.request(
            metodo,
            f"{GITHUB_API}{percorso}",
            timeout=30,
            **kw,
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    except requests.exceptions.RequestException as e:
        raise UserError(f"API di GitHub non raggiungibili: {type(e).__name__}")
    if r.status_code in (401, 403):
        raise UserError("Token GitHub non valido o senza i permessi necessari (GITHUB_TOKEN).")
    if r.status_code == 404:
        raise UserError("Repository o ramo non trovato su GitHub (o il token non ha accesso).")
    if r.status_code == 422:
        dettagli = "; ".join(e.get("message", "") for e in r.json().get("errors", [])) or r.json().get("message", "")
        raise UserError(f"GitHub ha rifiutato la richiesta: {dettagli}")
    r.raise_for_status()
    return r.json()


@tool(
    "github_crea_pull_request",
    title="Apri una pull request",
    description=(
        "Apre una pull request su un repository autorizzato, dal ramo 'ramo' (già inviato con github_push) verso "
        "'base' (default: il ramo principale del repository). Richiede SEMPRE la conferma dell'utente: la prima "
        "chiamata restituisce la domanda di conferma; dopo il sì richiama con il codice_conferma ricevuto."
    ),
    group="github",
)
async def github_crea_pull_request(
    repo: str = "",
    ramo: str = "",
    titolo: str = "",
    descrizione: str = "",
    base: str = "",
    codice_conferma: str = "",
    ctx: Context | None = None,
) -> str:
    if codice_conferma.strip():
        dati = prendi_attesa(codice_conferma, "github_pr")
    else:
        repo = _controlla_repo(repo)
        if not titolo.strip() or not _RE_RAMO.match(ramo.strip()):
            raise UserError("Servono il titolo e il ramo della pull request.")
        destinazione = base.strip() or (await asyncio.to_thread(_api, "GET", f"/repos/{repo}"))["default_branch"]
        dati = {
            "repo": repo,
            "ramo": ramo.strip(),
            "base": destinazione,
            "titolo": titolo.strip()[:250],
            "descrizione": descrizione.strip(),
        }
        frase = f"Apro la pull request «{dati['titolo']}» dal ramo {dati['ramo']} verso {destinazione} di {repo}?"
        anteprima = (
            f"Repository: {repo}\nDa: {dati['ramo']} → {destinazione}\nTitolo: {dati['titolo']}\n"
            f"Descrizione:\n{tronca(dati['descrizione'], 1500) or '(vuota)'}"
        )
        attesa = await chiedi_conferma(ctx, "github_pr", dati, frase, anteprima, "github_crea_pull_request")
        if attesa is not None:
            return attesa
    pr = await asyncio.to_thread(
        _api,
        "POST",
        f"/repos/{dati['repo']}/pulls",
        json={"title": dati["titolo"], "head": dati["ramo"], "base": dati["base"], "body": dati["descrizione"]},
    )
    registra_invio(f"GITHUB pull request {dati['repo']} #{pr.get('number')} {dati['ramo']}->{dati['base']}")
    return f"Pull request #{pr.get('number')} aperta: {pr.get('html_url')}"
