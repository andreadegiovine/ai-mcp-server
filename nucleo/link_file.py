"""
Link HTTP firmati ai file del workspace, per mostrarli nel client (immagini, PDF, documenti).

Il client (es. OpenWebUI nel browser) non puo' aprire un percorso locale del server: il server pubblica
quindi il file su /file/<token>/<nome>, dove il token contiene il percorso relativo e la scadenza ed e'
firmato con HMAC. Il link funziona senza chiave API (un tag <img> non puo' inviarla), ma non si puo'
falsificare ne' modificare per leggere altri file.
"""
import base64
import hashlib
import hmac
import mimetypes
import os
import secrets
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from nucleo.configurazione import DATA_DIR, WORKSPACE_DIR, _env, _env_int, logger
from nucleo.server import UserError, mcp

# Indirizzo del server nei link. Facoltativo: di norma si usa quello con cui il client ha raggiunto il server
# (preso dalla richiesta). Serve solo se il client usa un indirizzo che il browser non raggiunge (es. un nome
# interno di Docker): in quel caso indica l'indirizzo per il browser, es. http://192.168.1.11:8000
MCP_URL_PUBBLICO = _env("MCP_URL_PUBBLICO").rstrip("/")
LINK_FILE_GIORNI = _env_int("LINK_FILE_GIORNI", 30)
LINK_FILE_SEGRETO = _env("LINK_FILE_SEGRETO")
PREFISSO_ROTTA = "/file/"

_ESTENSIONI_IMMAGINE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "application/pdf", "text/plain"}
_chiave_cache: Optional[bytes] = None


def _chiave() -> bytes:
    """Chiave di firma: LINK_FILE_SEGRETO oppure una chiave casuale salvata in data/ (resta valida ai riavvii)."""
    global _chiave_cache
    if _chiave_cache is None:
        if LINK_FILE_SEGRETO:
            _chiave_cache = LINK_FILE_SEGRETO.encode()
        else:
            file_chiave = DATA_DIR / ".chiave_link_file"
            if not file_chiave.exists():
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                file_chiave.write_text(secrets.token_hex(32))
                os.chmod(file_chiave, 0o600)
            _chiave_cache = file_chiave.read_text().strip().encode()
    return _chiave_cache


def _b64(dati: bytes) -> str:
    return base64.urlsafe_b64encode(dati).decode().rstrip("=")


def _da_b64(testo: str) -> bytes:
    return base64.urlsafe_b64decode(testo + "=" * (-len(testo) % 4))


def _firma(contenuto: bytes) -> str:
    return _b64(hmac.new(_chiave(), contenuto, hashlib.sha256).digest()[:20])


def url_base(ctx=None) -> str:
    """Indirizzo del server per i link: MCP_URL_PUBBLICO se impostato, altrimenti quello della richiesta MCP."""
    if MCP_URL_PUBBLICO:
        return MCP_URL_PUBBLICO
    try:
        richiesta = ctx.request_context.request
        return str(richiesta.base_url).rstrip("/") if richiesta is not None else ""
    except Exception:
        return ""


def crea_link(percorso: Path, giorni: Optional[int] = None, ctx=None) -> Optional[str]:
    """URL firmato per un file del workspace; None se l'indirizzo del server non e' noto."""
    base = url_base(ctx)
    if not base:
        return None
    reale = Path(os.path.realpath(percorso))
    if WORKSPACE_DIR not in reale.parents or not reale.is_file():
        raise UserError(f"Si possono condividere solo file del workspace: {percorso}")
    scadenza = int(time.time()) + int((LINK_FILE_GIORNI if giorni is None else giorni) * 86400)
    contenuto = f"{reale.relative_to(WORKSPACE_DIR).as_posix()}\n{scadenza}".encode()
    token = f"{_b64(contenuto)}.{_firma(contenuto)}"
    return f"{base}{PREFISSO_ROTTA}{token}/{quote(reale.name)}"


def markdown_file(percorso: Path, descrizione: str = "", ctx=None) -> Optional[str]:
    """Markdown per mostrare il file nella chat: immagine per le immagini, link per gli altri file."""
    url = crea_link(percorso, ctx=ctx)
    if not url:
        return None
    testo = (descrizione or percorso.name).replace("[", "(").replace("]", ")")
    return f"![{testo}]({url})" if percorso.suffix.lower() in _ESTENSIONI_IMMAGINE else f"[{testo}]({url})"


def nota_link(percorso: Path, descrizione: str = "", ctx=None) -> str:
    """Frase da aggiungere alla risposta dei tool che producono file."""
    md = markdown_file(percorso, descrizione, ctx)
    if md:
        return f"Per mostrarlo all'utente scrivi nella risposta: {md}"
    return ("(Link al file non disponibile: non usare il percorso locale come link, il client non puo' aprirlo.)")


def _verifica_token(token: str) -> Path:
    try:
        parte, firma = token.split(".", 1)
        contenuto = _da_b64(parte)
    except Exception:
        raise PermissionError("token non valido")
    if not hmac.compare_digest(firma, _firma(contenuto)):
        raise PermissionError("firma non valida")
    relativo, scadenza = contenuto.decode().rsplit("\n", 1)
    if int(scadenza) < time.time():
        raise TimeoutError("link scaduto")
    reale = Path(os.path.realpath(WORKSPACE_DIR / relativo))
    if WORKSPACE_DIR not in reale.parents:
        raise PermissionError("percorso fuori dal workspace")
    return reale


@mcp.custom_route(PREFISSO_ROTTA + "{token}/{nome}", methods=["GET", "HEAD"])
async def servi_file(request):
    from starlette.responses import FileResponse, PlainTextResponse
    try:
        percorso = _verifica_token(request.path_params["token"])
    except TimeoutError:
        return PlainTextResponse("Link scaduto", status_code=410)
    except Exception:
        return PlainTextResponse("Link non valido", status_code=403)
    if not percorso.is_file():
        return PlainTextResponse("File non piu' disponibile", status_code=404)
    tipo = mimetypes.guess_type(percorso.name)[0] or "application/octet-stream"
    disposizione = "inline" if tipo in _INLINE else "attachment"
    logger.info(f"File servito tramite link: {percorso.relative_to(WORKSPACE_DIR)}")
    return FileResponse(
        percorso, media_type=tipo, filename=percorso.name, content_disposition_type=disposizione,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"})
