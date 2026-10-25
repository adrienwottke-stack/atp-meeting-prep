"""Expired document sessions return to login without changing API contracts."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_http_reliability import app_with_repo


def test_browser_login_returns_to_help_and_rejects_external_destinations():
    import asyncio
    import socket
    import threading

    import uvicorn
    from playwright.sync_api import sync_playwright

    app = app_with_repo(SimpleNamespace(list_meetings=lambda *_args, **_kwargs: []))

    async def login(_email, _password):
        return {"access_token": "synthetic-browser-test", "expires_in": 3600}

    app.state.auth.login = login
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    app.state.settings.app_base_url = base
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[sock])), daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/help?chapter=einstieg")
                page.wait_for_url("**/login?next=*")
                page.get_by_label("E-Mail-Adresse").fill("synthetic@example.com")
                page.get_by_label("Passwort", exact=True).fill("synthetic")
                page.locator("#main").get_by_role("button", name="Anmelden", exact=True).click()
                page.wait_for_url("**/help?chapter=einstieg")
                assert page.locator("h1").count() > 0
                page.goto(base + "/login?next=//evil.example")
                destinations = page.evaluate("""async () => {
                    const {loginDestination} = await import('/static/auth-navigation.js?v=auth-navigation-1');
                    return ['//evil.example', '/\\\\evil.example', 'https://evil.example',
                        '/v1/auth/logout', '/login', '/%2f%2fevil.example',
                        '/help', '/history?q=x#test', '/briefings/abc'].map(loginDestination);
                }""")
                assert destinations[:6] == [None] * 6
                assert destinations[6:] == ["/help", "/history?q=x#test", "/briefings/abc"]
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(5)


@pytest.mark.parametrize("expired", [False, True])
async def test_browser_help_redirects_to_login_and_keeps_destination(expired):
    async def authenticate(_):
        raise ValueError("UNAUTHENTICATED")

    app = app_with_repo(SimpleNamespace(), authenticate)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit.example"
    ) as client:
        if expired:
            client.cookies.set("atp_access", "expired", domain="unit.example", path="/")
        response = await client.get("/help?chapter=einstieg", headers={"Accept": "text/html"})
        assert response.status_code == 303
        location = urlsplit(response.headers["location"])
        assert location.path == "/login" and not location.netloc
        assert parse_qs(location.query) == {"next": ["/help?chapter=einstieg"]}
        assert response.headers["cache-control"] == "no-store"
        login = await client.get(response.headers["location"])
        assert login.status_code == 200 and "<html" in login.text


async def test_browser_accept_does_not_redirect_api_or_mask_auth_outage():
    async def unavailable(_):
        raise ValueError("AUTH_UNAVAILABLE")

    app = app_with_repo(SimpleNamespace(), unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit.example"
    ) as client:
        response = await client.get("/v1/meetings", headers={"Accept": "text/html"})
        assert response.status_code == 401
        assert response.json()["code"] == "AUTH_REQUIRED"
        response = await client.get(
            "/help", headers={"Accept": "text/html", "Authorization": "Bearer unit"}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "AUTH_UNAVAILABLE"
