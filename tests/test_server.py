import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from ariadne import server


@pytest.fixture()
def srv():
    s = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s.server_address[1]
    s.shutdown()


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read()


def test_serves_the_app_shell(srv):
    status, body = get(srv, "/")
    assert status == 200 and b"Ariadne" in body
    assert get(srv, "/app.js")[0] == 200 and get(srv, "/style.css")[0] == 200


def test_status_is_idle_before_start(srv):
    status, body = get(srv, "/api/status")
    assert status == 200 and json.loads(body)["phase"] == "idle"


def test_chain_endpoints_refuse_until_started(srv):
    status, body = get(srv, "/api/pool")
    assert status == 500 and "not running" in json.loads(body)["error"]


def test_no_path_traversal_and_unknown_routes(srv):
    assert get(srv, "/../ariadne/wire.py")[0] == 404
    assert get(srv, "/api/nope")[0] == 404
