"""The proof client dials a fixed address, sends the URL's name as Host and SNI, and keeps cookies per host."""

import http.server
import ssl
import subprocess
import threading

import pytest

from setup.wiring.web import Web


@pytest.fixture
def server(tmp_path):
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert, "-days", "1"]
        + ["-subj", "/CN=app.example.test", "-addext", "subjectAltName=DNS:app.example.test"],
        check=True,
        capture_output=True,
    )
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Host"), self.headers.get("Cookie")))
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/end")
                self.send_header("Set-Cookie", "session=abc; Path=/; Secure; HttpOnly")
            else:
                self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield ("127.0.0.1", httpd.server_address[1]), seen
    httpd.shutdown()


def test_dials_the_fixed_address_with_the_urls_name_and_carries_cookies_across_a_redirect(server):
    address, seen = server
    web = Web(address, verify=False)
    reply, final = web.follow("https://app.example.test/start")
    assert (reply.status, reply.body, final) == (200, b"ok", "https://app.example.test/end")
    assert seen == [("/start", "app.example.test", None), ("/end", "app.example.test", "session=abc")]


def test_cookies_stay_with_the_host_that_set_them(server):
    address, seen = server
    web = Web(address, verify=False)
    web.send("GET", "https://app.example.test/start")
    web.send("GET", "https://auth.example.test/end")
    assert seen[-1] == ("/end", "auth.example.test", None)


def test_a_certificate_that_is_not_trusted_is_refused_by_default(server):
    address, _ = server
    with pytest.raises(ssl.SSLError):
        Web(address).send("GET", "https://app.example.test/end")


def test_a_redirect_loop_is_an_error_not_a_hang(server):
    address, _ = server

    class Loop(Web):
        def send(self, method, url, headers=None, body=None):
            from setup.wiring.web import Reply

            return Reply(302, {"location": url}, b"")

    with pytest.raises(RuntimeError, match="redirects"):
        Loop(address).follow("https://app.example.test/start", hops=3)
