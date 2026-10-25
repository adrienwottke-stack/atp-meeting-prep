import pytest

from atp.evidence import (
    MAX_BYTES,
    EvidenceError,
    EvidenceFetcher,
    page_text,
    quote_supported,
    resolve_public,
    validate_url,
)
from atp.models import Source, Usage


class Budget:
    def __init__(self):
        self.usage = Usage()

    def reserve(self, model=0, searches=0, evidence=0):
        self.usage.evidence_fetches += evidence

    def timeout(self, maximum):
        return maximum


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://localhost",
        "http://foo.local",
        "http://metadata.google.internal",
        "https://user:pass@example.com",
        "file:///etc/passwd",
        "http://example.com:8080",
        "https://musterwerk.example",
        "http://example.com\\@127.0.0.1",
        "https://example.com/\nX:bad",
    ],
)
def test_ssrf_blocked_urls(url):
    with pytest.raises(EvidenceError):
        validate_url(url)


def test_dns_mixed_public_private_and_ipv4_mapped_blocked(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(EvidenceError, match="address_blocked"):
        resolve_public("example.com", 443)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(10, 1, 6, "", ("::ffff:127.0.0.1", 443))])
    with pytest.raises(EvidenceError):
        resolve_public("example.com", 443)


class Response:
    def __init__(
        self, status=200, headers=None, content=b"Public company text with real supporting details."
    ):
        self.status, self.headers, self.content = status, headers or {"Content-Type": "text/plain"}, content

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read(self, size):
        chunk, self.content = self.content[:size], self.content[size:]
        return chunk


def factory_for(responses, calls):
    class Conn:
        def __init__(self, host, address, port, timeout):
            calls.append((host, address, port, timeout))
            self.sock = None

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            return responses.pop(0)

        def close(self):
            pass

    return Conn


async def test_public_address_is_pinned_and_attempt_counted():
    calls, budget = [], Budget()
    fetcher = EvidenceFetcher(
        resolver=lambda h, p: ["93.184.216.34"], connection_factory=factory_for([Response()], calls)
    )
    source = await fetcher.fetch("https://example.com/company", budget)
    assert calls[0][:3] == ("example.com", "93.184.216.34", 443)
    assert source.verified and source.content_hash and source.information_as_of is None
    assert budget.usage.evidence_fetches == 1


async def test_redirect_to_metadata_is_rejected_before_connection():
    calls, budget = [], Budget()
    fetcher = EvidenceFetcher(
        resolver=lambda h, p: ["93.184.216.34"],
        connection_factory=factory_for([Response(302, {"Location": "http://169.254.169.254/"})], calls),
    )
    with pytest.raises(EvidenceError):
        await fetcher.fetch("https://example.com/", budget)
    assert len(calls) == 1
    assert budget.usage.evidence_fetches == 1


async def test_every_redirect_dns_checked():
    calls, resolutions = [], []

    def resolver(host, port):
        resolutions.append(host)
        return ["93.184.216.34"] if len(resolutions) == 1 else ["127.0.0.1"]

    fetcher = EvidenceFetcher(
        resolver=resolver,
        connection_factory=factory_for([Response(302, {"Location": "https://other.com/"})], calls),
    )
    with pytest.raises(EvidenceError):
        await fetcher.fetch("https://example.com/", Budget())
    assert resolutions == ["example.com", "other.com"] and len(calls) == 1


@pytest.mark.parametrize(
    "response,code",
    [
        (Response(headers={"Content-Type": "application/octet-stream"}), "content_type"),
        (
            Response(headers={"Content-Type": "text/plain", "Content-Length": str(MAX_BYTES + 1)}),
            "size_limit",
        ),
        (Response(headers={"Content-Type": "text/plain", "Content-Encoding": "gzip"}), "encoding"),
        (Response(content=b"x" * (MAX_BYTES + 1)), "size_limit"),
    ],
)
async def test_fetch_limits(response, code):
    fetcher = EvidenceFetcher(
        resolver=lambda h, p: ["93.184.216.34"], connection_factory=factory_for([response], [])
    )
    with pytest.raises(EvidenceError, match=code):
        await fetcher.fetch("https://example.com/", Budget())


def test_html_scripts_removed_and_quote_must_exist():
    text, title = page_text(
        b"<title>Firma</title><script>send secret</script><p>120 employees work for this company.</p>",
        "text/html",
    )
    assert "send secret" not in text and title == "Firma"
    source = Source(url="https://example.com/", title=title, excerpt=text, verified=True)
    assert quote_supported(source, "120 employees work for this company.")
    assert not quote_supported(source, "120 employees with fictional profits.")
    assert not quote_supported(source, "120")
