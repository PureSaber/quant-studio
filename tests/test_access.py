import json

import pytest

from quant_studio import QuantStudioError
from quant_studio.access import AccessPolicy, password_record
from tests.test_server import _raw_request, _start_http_server


def test_auth_expiry_revoke_and_no_plaintext_on_disk(tmp_path):
    path = tmp_path / "access.json"
    password = "a-long-test-only-password"
    record = password_record(password, ["127.0.0.1", "192.168.3.73"])
    path.write_text(json.dumps(record))
    assert password not in path.read_text()
    clock = [100.0]
    policy = AccessPolicy(path, clock=lambda: clock[0])
    assert policy.login("wrong", "peer") is None
    token = policy.login(password, "peer")
    assert policy.session(token)["csrf"]
    clock[0] += 8 * 3600 + 1
    assert policy.session(token) is None
    token = policy.login(password, "peer")
    policy.revoke(token)
    assert policy.session(token) is None


def test_login_throttles_even_correct_password_after_failures(tmp_path):
    path = tmp_path / "access.json"
    path.write_text(
        json.dumps(password_record("test-password-long-enough", ["127.0.0.1"]))
    )
    policy = AccessPolicy(path)
    for _ in range(5):
        assert policy.login("wrong", "peer") is None
    with pytest.raises(QuantStudioError, match="稍后"):
        policy.login("test-password-long-enough", "peer")


@pytest.mark.parametrize(
    "hosts", [["*"], ["0.0.0.0"], ["example.com/path"], ["user@host"]]
)
def test_no_wildcard_or_ambiguous_allowed_authority(hosts):
    with pytest.raises(QuantStudioError):
        password_record("test-password-long-enough", hosts)


def test_authenticated_routes_csrf_login_logout_and_authority(tmp_path):
    import re
    from urllib.parse import urlencode

    path = tmp_path / "access.json"
    path.write_text(
        json.dumps(password_record("test-password-long-enough", ["localhost"]))
    )
    server, thread = _start_http_server(tmp_path / "runs")
    server.RequestHandlerClass.access_policy = AccessPolicy(path)
    authority = f"localhost:{server.server_port}"

    def request(method, path, fields=None, cookie="", origin=None, host=None):
        body = urlencode(fields or {})
        headers = [("Host", host or authority)]
        if cookie:
            headers.append(("Cookie", cookie))
        if method == "POST":
            headers.extend(
                [
                    ("Origin", origin or f"https://{authority}"),
                    ("Content-Type", "application/x-www-form-urlencoded"),
                    ("Content-Length", str(len(body.encode()))),
                ]
            )
        return _raw_request(server, method, path, headers=headers, body=body)

    try:
        for protected in (
            "/",
            "/research",
            "/data",
            "/setup",
            "/jobs",
            "/runs/a/files/nav.csv",
        ):
            status, headers, _ = request("GET", protected)
            assert status == 303 and headers["Location"] == "/login"
        assert (
            request("GET", "/login", host=f"evil.test:{server.server_port}")[0] == 403
        )
        _, headers, page = request("GET", "/login")
        nonce_cookie = headers["Set-Cookie"].split(";", 1)[0]
        nonce = re.search(rb'name="nonce" value="([^"]+)"', page).group(1).decode()
        fields = {"password": "test-password-long-enough", "nonce": nonce}
        assert (
            request(
                "POST", "/login", fields, nonce_cookie, origin=f"http://{authority}"
            )[0]
            == 403
        )
        status, headers, _ = request("POST", "/login", fields, nonce_cookie)
        assert status == 303
        assert all(
            flag in headers["Set-Cookie"]
            for flag in ("Secure", "HttpOnly", "SameSite=Strict")
        )
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        assert request("GET", "/research", cookie=cookie)[0] == 200
        assert (
            request(
                "POST", "/research/new", {"_csrf_token": "test-only-csrf-token"}, cookie
            )[0]
            == 403
        )
        _, _, page = request("GET", "/logout", cookie=cookie)
        csrf = re.search(rb'name="_csrf_token" value="([^"]+)"', page).group(1).decode()
        assert request("POST", "/logout", {"_csrf_token": csrf}, cookie)[0] == 303
        assert request("GET", "/research", cookie=cookie)[0] == 303
        assert (
            request("GET", "/research", cookie=cookie)[1]["Cache-Control"] == "no-store"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_controlled_mesh_binding_still_requires_explicit_host():
    from types import SimpleNamespace

    import pytest

    from quant_studio import QuantStudioError
    from quant_studio.access import validate_binding

    policy = SimpleNamespace(hosts={"100.101.102.103"})
    validate_binding("100.101.102.103", policy)
    for address in ("0.0.0.0", "8.8.8.8", "100.101.102.104"):
        with pytest.raises(QuantStudioError):
            validate_binding(address, policy)
