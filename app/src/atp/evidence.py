"""Bounded public evidence retrieval with DNS validation and pinned connections.

No proxy/environment configuration is consulted. Every redirect is validated and
resolved afresh, then the validated address is used directly for the connection.
TLS still verifies the original DNS hostname and sends its SNI name.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .models import Source
from .passages import select_passages

MAX_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 10
MAX_REDIRECTS = 3
MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PDF_SECONDS = 20
MAX_PDFS = 3


class EvidenceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_url(url: str) -> tuple[str, str, int, str]:
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise EvidenceError("evidence_url_invalid")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower().encode("idna").decode("ascii")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (ValueError, UnicodeError):
        raise EvidenceError("evidence_url_invalid") from None
    if (
        parsed.scheme not in ("http", "https")
        or not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise EvidenceError("evidence_url_invalid")
    if (
        port not in (80, 443)
        or (parsed.scheme == "https" and port != 443)
        or (parsed.scheme == "http" and port != 80)
    ):
        raise EvidenceError("evidence_port_blocked")
    if host in ("localhost", "metadata.google.internal", "metadata") or host.endswith(
        (".localhost", ".local", ".internal", ".home", ".test", ".example", ".invalid")
    ):
        raise EvidenceError("evidence_host_blocked")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise EvidenceError("evidence_address_blocked")
    path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    return parsed.scheme, host, port, path


def resolve_public(host: str, port: int) -> list[str]:
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise EvidenceError("evidence_dns_failed") from None
    addresses = list(dict.fromkeys(r[4][0] for r in results))
    if not addresses:
        raise EvidenceError("evidence_dns_failed")
    for value in addresses:
        address = ipaddress.ip_address(value.split("%", 1)[0])
        # IPv4-mapped IPv6 must also meet the IPv4 policy.
        if not address.is_global or (
            getattr(address, "ipv4_mapped", None) and not address.ipv4_mapped.is_global
        ):
            raise EvidenceError("evidence_address_blocked")
    return addresses


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, address, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), timeout=self.timeout)


class _PinnedHTTPS(_PinnedHTTP):
    def connect(self):
        super().connect()
        self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title = [], []
        self.hidden = 0
        self.in_title = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)
        if tag in ("script", "style", "noscript", "svg", "template"):
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "td", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "template") and self.hidden:
            self.hidden -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)
        if self.in_title:
            self.title.append(value)


def page_text(data: bytes, content_type: str) -> tuple[str, str]:
    charset = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    try:
        content = data.decode(charset.group(1) if charset else "utf-8", errors="replace")
    except LookupError:
        content = data.decode("utf-8", errors="replace")
    if "html" in content_type:
        page = BeautifulSoup(content, "html.parser")
        title = page.title.get_text(" ", strip=True) if page.title else ""
        for node in page.select("script, style, noscript, svg, template, nav, footer, [role=navigation]"):
            node.decompose()
        for node in list(page.find_all(True)):
            if not node.attrs:
                continue
            marker = " ".join([str(node.get("id", "")), " ".join(node.get("class", []))])
            if re.search(r"cookie|consent[-_ ]?(?:banner|dialog|notice)|onetrust|cmp[-_]", marker, re.I):
                node.decompose()
        content = page.get_text(" ", strip=True)
    else:
        title = "Öffentlicher Textbeleg"
    return re.sub(r"\s+", " ", content).strip(), re.sub(r"\s+", " ", title).strip()[:500]


def company_links(data: bytes, url: str) -> list[str]:
    """Discover bounded public HTML links; every actual fetch still passes SSRF checks."""
    page = _PageText()
    page.feed(data.decode("utf-8", errors="replace"))
    base = (urlsplit(url).hostname or "").removeprefix("www.")
    found = []
    for link in page.links:
        try:
            target = urlsplit(urljoin(url, link))
        except ValueError:
            continue
        host = (target.hostname or "").removeprefix("www.")
        if host != base and not host.endswith("." + base):
            continue
        if not re.search(
            r"unternehmen|company|about|impressum|corporate|management|vorstand|ueber-uns|über-uns|profil|team",
            target.path,
            re.I,
        ):
            continue
        if re.search(r"\.(?:jpg|png|zip)$", target.path, re.I):
            continue
        clean = urlunsplit((target.scheme, target.netloc, target.path, "", ""))
        try:
            validate_url(clean)
        except EvidenceError:
            continue
        if clean not in found:
            found.append(clean)
    return found[:12]


class EvidenceFetcher:
    def __init__(self, *, resolver=resolve_public, connection_factory=None):
        self.resolver = resolver
        self.connection_factory = connection_factory
        self._pdf_lock = threading.Lock()

    def _fetch(self, url, timeout, reserve_pdf=None):
        deadline = time.monotonic() + min(timeout, MAX_SECONDS)
        for redirect in range(MAX_REDIRECTS + 1):
            scheme, host, port, path = validate_url(url)
            addresses = self.resolver(host, port)
            # Validate injected/custom resolvers too; validation must not be bypassable.
            if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
                raise EvidenceError("evidence_address_blocked")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EvidenceError("evidence_timeout")
            factory = self.connection_factory or (_PinnedHTTPS if scheme == "https" else _PinnedHTTP)
            conn = factory(host, addresses[0], port, remaining)
            try:
                conn.request(
                    "GET",
                    path,
                    headers={
                        "Host": host,
                        "User-Agent": "ATP-Evidence/1.0",
                        "Accept": "text/html,text/plain,application/xhtml+xml,application/pdf",
                        "Accept-Encoding": "identity",
                    },
                )
                response = conn.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader("Location")
                    if not location or redirect >= MAX_REDIRECTS:
                        raise EvidenceError("evidence_redirect_limit")
                    url = urljoin(url, location)
                    continue
                if response.status != 200:
                    raise EvidenceError("evidence_http_" + str(response.status))
                content_type = response.getheader("Content-Type", "").lower()
                if not any(
                    content_type.startswith(t)
                    for t in ("text/html", "text/plain", "application/xhtml+xml", "application/pdf")
                ):
                    raise EvidenceError("evidence_content_type_blocked")
                pdf = content_type.startswith("application/pdf")
                if pdf and reserve_pdf:
                    reserve_pdf()
                maximum_bytes = MAX_PDF_BYTES if pdf else MAX_BYTES
                if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
                    raise EvidenceError("evidence_encoding_blocked")
                length = response.getheader("Content-Length")
                if length and (not length.isdigit() or int(length) > maximum_bytes):
                    raise EvidenceError("evidence_size_limit")
                chunks, size = [], 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise EvidenceError("evidence_timeout")
                    if conn.sock:
                        conn.sock.settimeout(remaining)
                    chunk = response.read(min(65536, maximum_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > maximum_bytes:
                        raise EvidenceError("evidence_size_limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                return raw, content_type, url
            except (OSError, http.client.HTTPException):
                raise EvidenceError("evidence_network_error") from None
            finally:
                conn.close()
        raise EvidenceError("evidence_redirect_limit")

    async def fetch(self, url: str, budget) -> Source:
        # All attempts count, including URL/DNS rejection and failed requests.
        budget.reserve(evidence=1)
        timeout = budget.timeout(MAX_SECONDS)

        def reserve_pdf():
            with self._pdf_lock:
                count = getattr(budget, "_pdf_fetches", 0)
                if count >= MAX_PDFS:
                    raise EvidenceError("evidence_pdf_count_limit")
                budget._pdf_fetches = count + 1

        try:
            raw, content_type, final_url = await asyncio.wait_for(
                asyncio.to_thread(self._fetch, url, timeout, reserve_pdf), timeout=timeout
            )
            if content_type.startswith("application/pdf"):
                text, title = await pdf_text(raw, budget.timeout(MAX_PDF_SECONDS))
            else:
                text, title = page_text(raw, content_type)
            if not text:
                raise EvidenceError("evidence_empty")
            return Source(
                url=final_url,
                title=title or urlsplit(final_url).hostname,
                content_hash=hashlib.sha256(raw).hexdigest(),
                excerpt=select_passages(text),
                verified=True,
                limitation="Informationsstand unbekannt; Abrufdatum belegt keine Aktualität.",
                discovered_urls=company_links(raw, final_url) if "html" in content_type else [],
            )
        except TimeoutError:
            raise EvidenceError("evidence_timeout") from None


async def pdf_text(data, timeout=MAX_PDF_SECONDS):
    """Cancellation/timeout always stops the parser child; no detached work."""
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "atp.pdf_evidence_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **kwargs,
        )
    except OSError:
        raise EvidenceError("evidence_pdf_process_failed") from None
    try:
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(data), timeout=min(timeout, MAX_PDF_SECONDS)
            )
        except TimeoutError:
            raise EvidenceError("evidence_pdf_timeout") from None
        try:
            parsed = json.loads(stdout)
        except (ValueError, UnicodeError):
            raise EvidenceError("evidence_pdf_invalid") from None
        if process.returncode or parsed.get("error"):
            raise EvidenceError(parsed.get("error", "evidence_pdf_invalid"))
        return parsed["text"], parsed["title"]
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def quote_supported(source: Source, quote: str) -> bool:
    def normal(value):
        return re.sub(r"\s+", " ", str(value)).strip().casefold()

    # A single word is not a concrete supporting passage.
    return source.verified and len(normal(quote)) >= 20 and normal(quote) in normal(source.excerpt)
