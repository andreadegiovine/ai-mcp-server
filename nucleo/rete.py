"""Rete: controllo degli URL (anti-SSRF), download sicuri e conversione delle immagini."""

import base64
import contextlib
import functools
import io
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from mcp.types import ImageContent
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from nucleo.configurazione import env_bool
from nucleo.server import UserError

try:
    from PIL import Image as PILImage
except ImportError:  # pragma: no cover
    PILImage = None


WEB_ALLOW_PRIVATE_URLS = env_bool("WEB_ALLOW_PRIVATE_URLS", False)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# --- Rete: controllo URL (anti-SSRF) e download sicuri --------------------------------
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"})
HTTP.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=2, backoff_factor=0.5, status_forcelist=(502, 503, 504), allowed_methods=("GET", "HEAD")
        )
    ),
)


@functools.lru_cache(maxsize=1024)
def _host_privato(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False  # non risolvibile localmente (es. dietro proxy): verrà gestito dalla richiesta
    for info in infos:
        with contextlib.suppress(ValueError):
            if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
                return True
    return False


def url_consentito(url: str) -> tuple[bool, str]:
    try:
        p = urlparse(url)
    except Exception:
        return False, "URL non valido"
    if p.scheme not in ("http", "https"):
        return False, f"schema '{p.scheme}' non consentito (solo http/https)"
    if not p.hostname:
        return False, "host mancante"
    if not WEB_ALLOW_PRIVATE_URLS and _host_privato(p.hostname):
        return False, "indirizzi di rete locale non consentiti (imposta WEB_ALLOW_PRIVATE_URLS=true per abilitarli)"
    return True, ""


def controlla_url(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    ok, motivo = url_consentito(url)
    if not ok:
        raise UserError(f"URL rifiutato ({url}): {motivo}")
    return url


def _ip_verificato(host: str) -> str | None:
    """
    Risolve il nome una sola volta e verifica tutti gli indirizzi: la connessione userà proprio quello verificato,
    così un DNS che cambia risposta tra il controllo e la connessione ("DNS rebinding") non porta in rete locale.
    None = host già numerico o non risolvibile localmente (lo gestisce la richiesta).
    """
    nome = host.strip("[]")
    with contextlib.suppress(ValueError):
        ipaddress.ip_address(nome)
        return None
    try:
        indirizzi = [info[4][0].split("%")[0] for info in socket.getaddrinfo(nome, None, type=socket.SOCK_STREAM)]
    except (OSError, UnicodeError):
        return None
    if not WEB_ALLOW_PRIVATE_URLS:
        for ip in indirizzi:
            if not ipaddress.ip_address(ip).is_global:
                raise UserError(f"URL rifiutato: {nome} punta a un indirizzo di rete locale ({ip}).")
    return indirizzi[0] if indirizzi else None


class _AdapterIPFisso(HTTPAdapter):
    """Si collega all'IP già verificato, con il nome originale per l'header Host e per TLS (SNI e certificato)."""

    def __init__(self, host: str, ip: str):
        self.host, self.ip = host, ip
        super().__init__(max_retries=0)

    def send(self, request, **kwargs):
        parti = urlparse(request.url)
        netloc_ip = f"[{self.ip}]" if ":" in self.ip else self.ip
        if parti.port:
            netloc_ip += f":{parti.port}"
        request.headers["Host"] = parti.netloc.rsplit("@", 1)[-1]
        request.url = parti._replace(netloc=netloc_ip).geturl()
        if parti.scheme == "https":
            self.poolmanager.connection_pool_kw["server_hostname"] = self.host
            self.poolmanager.connection_pool_kw["assert_hostname"] = self.host
        return super().send(request, **kwargs)


def richiesta_sicura(metodo: str, url: str, **kwargs) -> requests.Response:
    """Richiesta HTTP verso un URL già controllato, collegandosi all'IP verificato (anti DNS rebinding)."""
    host = urlparse(url).hostname or ""
    # solo il proxy che si applica davvero a questo URL (NO_PROXY e le altre variabili non contano)
    proxy = requests.utils.select_proxy(url, requests.utils.get_environ_proxies(url))
    ip = None if proxy else _ip_verificato(host)
    if ip is None:
        return HTTP.request(metodo, url, **kwargs)  # IP numerico o tramite proxy: risolve il proxy
    with requests.Session() as sessione:
        sessione.headers.update(HTTP.headers)
        sessione.trust_env = False
        adapter = _AdapterIPFisso(host, ip)
        sessione.mount("http://", adapter)
        sessione.mount("https://", adapter)
        return sessione.request(metodo, url, **kwargs)


def http_get(
    url: str,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    timeout: float = 15,
    headers: dict | None = None,
    verso_file: Path | None = None,
) -> tuple[bytes, dict, str]:
    """GET con controllo SSRF su ogni redirect e limite di dimensione."""
    for _ in range(6):
        url = controlla_url(url)
        r = richiesta_sicura("GET", url, headers=headers, timeout=timeout, stream=True, allow_redirects=False)
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            nuovo = r.headers.get("location")
            r.close()
            if not nuovo:
                raise UserError("Redirect senza destinazione")
            url = urljoin(url, nuovo)
            continue
        try:
            r.raise_for_status()
            dichiarati = int(r.headers.get("content-length") or 0)
            if dichiarati and dichiarati > max_bytes:
                raise UserError(f"File troppo grande ({dichiarati / 1e6:.1f} MB, limite {max_bytes / 1e6:.0f} MB)")
            letti = 0
            buf = io.BytesIO()
            fh = open(verso_file, "wb") if verso_file else None
            try:
                for chunk in r.iter_content(64 * 1024):
                    letti += len(chunk)
                    if letti > max_bytes:
                        raise UserError(f"File troppo grande (limite {max_bytes / 1e6:.0f} MB)")
                    (fh or buf).write(chunk)
            finally:
                if fh:
                    fh.close()
            return buf.getvalue(), dict(r.headers), url
        finally:
            r.close()
    raise UserError("Troppi redirect")


def immagine_a_contenuto(dati: bytes, max_lato: int = 768) -> ImageContent:
    """Ridimensiona e converte un'immagine in ImageContent JPEG (base64)."""
    if PILImage is None:
        raise UserError("Pillow non installato: impossibile elaborare immagini")
    with PILImage.open(io.BytesIO(dati)) as img:
        img.seek(0)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            sfondo = PILImage.new("RGB", img.size, (255, 255, 255))
            sfondo.paste(img, mask=img.split()[-1])
            img = sfondo
        else:
            img = img.convert("RGB")
        img.thumbnail((max_lato, max_lato))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=80, optimize=True)
    return ImageContent(type="image", data=base64.b64encode(out.getvalue()).decode(), mimeType="image/jpeg")
