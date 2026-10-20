"""End-to-end tests of the gateway: a real server on a free port, a throwaway SQLite database."""

import http.client
import importlib
import re
import sys
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))


@pytest.fixture(scope="module")
def gw(tmp_path_factory):
    import os

    os.environ["GW_DB"] = str(tmp_path_factory.mktemp("db") / "gw.db")
    os.environ["GW_PUBLIC_URL"] = "https://llm.example.com"
    app = importlib.import_module("app")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, srv.server_address[1]
    srv.shutdown()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = urllib.parse.urlencode(body).encode() if body is not None else None
    h = {"Content-Type": "application/x-www-form-urlencoded"} if data else {}
    c.request(method, path, data, {**h, **(headers or {})})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read().decode()


def create(gw, name, kind="person", days=90):
    app, port = gw
    st, _, html = call(port, "POST", "/admin/create", {"csrf": app.csrf_token(), "name": name, "kind": kind, "days": days, "hours": 24})
    assert st == 200
    link = re.search(r"https://llm\.example\.com/claim/([\w-]+)", html).group(1)
    key = re.search(r"sk-gw-[\w-]{43}", html)
    return link, key.group(0) if key else None


def auth(port, key):
    return call(port, "GET", "/auth", headers={"Authorization": f"Bearer {key}"})[0]


def test_unknown_or_missing_key_is_refused(gw):
    _, port = gw
    assert auth(port, "sk-gw-nope") == 401
    assert call(port, "GET", "/auth")[0] == 401


def test_project_key_works_and_is_stored_hashed(gw):
    app, port = gw
    _, key = create(gw, "projet-a", kind="project")
    assert auth(port, key) == 200
    assert call(port, "GET", "/auth", headers={"X-Api-Key": key})[0] == 200
    rows = app.q("SELECT key_hash, requests FROM keys WHERE name='projet-a'")
    assert rows[0]["key_hash"] == app.sha(key) and rows[0]["requests"] == 2
    dump = "\n".join(app._db.iterdump())
    assert key not in dump.split("INSERT INTO \"claims\"")[0]  # only the pending link holds it


def test_person_link_is_single_use_and_secret_is_erased(gw):
    app, port = gw
    token, shown = create(gw, "Marie")
    assert shown is None  # a person's key is never shown to the admin
    assert call(port, "GET", f"/claim/{token}")[0] == 200
    st, _, html = call(port, "POST", f"/claim/{token}")
    key = re.search(r"sk-gw-[\w-]{43}", html).group(0)
    assert st == 200 and auth(port, key) == 200
    assert call(port, "POST", f"/claim/{token}")[0] == 410
    assert call(port, "GET", f"/claim/{token}")[0] == 410
    assert app.q("SELECT secret FROM claims WHERE token_hash=?", app.sha(token))[0]["secret"] is None


def test_revoked_key_stops_working(gw):
    app, port = gw
    _, key = create(gw, "projet-b", kind="project")
    kid = app.q("SELECT id FROM keys WHERE name='projet-b'")[0]["id"]
    assert call(port, "POST", "/admin/revoke", {"csrf": app.csrf_token(), "id": kid})[0] == 303
    assert auth(port, key) == 401


def test_expired_key_is_refused(gw):
    app, port = gw
    _, key = create(gw, "projet-c", kind="project")
    app.q("UPDATE keys SET expires_at=? WHERE name='projet-c'", int(time.time()) - 1)
    assert auth(port, key) == 401


def test_rotation_invalidates_the_old_key(gw):
    app, port = gw
    _, old = create(gw, "projet-d", kind="project")
    kid = app.q("SELECT id FROM keys WHERE name='projet-d'")[0]["id"]
    st, _, html = call(port, "POST", "/admin/rotate", {"csrf": app.csrf_token(), "id": kid})
    new = re.search(r"sk-gw-[\w-]{43}", html).group(0)
    assert st == 200 and new != old
    assert auth(port, old) == 401 and auth(port, new) == 200


@pytest.mark.parametrize(
    "csrf,site",
    [("", "same-origin"), ("123.forged", "same-origin"), (None, "cross-site")],
)
def test_admin_actions_need_csrf_and_same_origin(gw, csrf, site):
    app, port = gw
    tok = app.csrf_token() if csrf is None else csrf
    st, _, _ = call(port, "POST", "/admin/create", {"csrf": tok, "name": "x"}, {"Sec-Fetch-Site": site})
    assert st == 403
    assert not app.q("SELECT 1 FROM keys WHERE name='x'")


def test_responses_carry_security_headers(gw):
    _, port = gw
    _, h, _ = call(port, "GET", "/admin")
    assert h["Cache-Control"] == "no-store" and h["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
