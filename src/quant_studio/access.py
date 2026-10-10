"""Single-owner HTTPS access. Passwords and session secrets never enter URLs."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
import ssl
import threading
import time
from collections import deque
from html import escape
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from quant_studio import QuantStudioError

ITERATIONS = 600_000
COOKIE = "__Host-quant_session"
NONCE = "__Host-quant_login"


def _host(host):
    if not isinstance(host, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", host
    ):
        raise QuantStudioError("受控访问须指定确切的主机名或 IPv4 地址")
    if host in {"0.0.0.0", "255.255.255.255"}:
        raise QuantStudioError("不允许通配监听地址")
    return host.lower()


def password_record(password: str, hosts: list[str]) -> dict:
    if not isinstance(password, str) or not 16 <= len(password) <= 256:
        raise QuantStudioError("访问密码须为 16–256 个字符")
    if not hosts:
        raise QuantStudioError("必须指定允许的主机")
    salt = secrets.token_bytes(32)
    return {
        "schema_version": "quant-studio.access/v1",
        "hosts": sorted({_host(h) for h in hosts}),
        "salt": salt.hex(),
        "iterations": ITERATIONS,
        "password_hash": hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, ITERATIONS
        ).hex(),
    }


class AccessPolicy:
    def __init__(self, path: str | Path, *, clock=time.monotonic):
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
            if (
                set(value)
                != {"schema_version", "hosts", "salt", "iterations", "password_hash"}
                or value["schema_version"] != "quant-studio.access/v1"
                or value["iterations"] != ITERATIONS
                or not value["hosts"]
            ):
                raise ValueError("invalid schema")
            self.hosts = {_host(host) for host in value["hosts"]}
            self.salt = bytes.fromhex(value["salt"])
            self.digest = bytes.fromhex(value["password_hash"])
            if len(self.salt) != 32 or len(self.digest) != 32:
                raise ValueError("invalid digest")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QuantStudioError("受控访问配置损坏或缺失") from exc
        self.clock = clock
        self._lock = threading.RLock()
        self._sessions = {}
        self._attempts = deque(maxlen=50)

    def login(self, password, peer):
        with self._lock:
            now = self.clock()
            while self._attempts and self._attempts[0][0] <= now - 300:
                self._attempts.popleft()
            if (
                len(self._attempts) >= 50
                or sum(ip == peer for _, ip in self._attempts) >= 5
            ):
                raise QuantStudioError("登录尝试过多，请 5 分钟后稍后重试")
            self._attempts.append((now, peer))
            if not isinstance(password, str) or len(password) > 256:
                return None
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), self.salt, ITERATIONS
            )
            if not secrets.compare_digest(self.digest, digest):
                return None
            self._attempts = deque(
                ((t, ip) for t, ip in self._attempts if ip != peer), maxlen=50
            )
            self._sessions = {
                k: v for k, v in self._sessions.items() if now < v["expires"]
            }
            if len(self._sessions) >= 32:
                self._sessions.pop(next(iter(self._sessions)))
            token = secrets.token_urlsafe(32)
            self._sessions[_token_key(token)] = {
                "expires": now + 8 * 3600,
                "csrf": secrets.token_urlsafe(32),
            }
            return token

    def session(self, token):
        with self._lock:
            key = _token_key(token)
            value = self._sessions.get(key)
            if value and self.clock() >= value["expires"]:
                self._sessions.pop(key, None)
                return None
            return dict(value) if value else None

    def revoke(self, token):
        with self._lock:
            self._sessions.pop(_token_key(token), None)


def _token_key(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def tls_context(cert, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(cert, key)
    except (OSError, ssl.SSLError) as exc:
        raise QuantStudioError("无法加载 HTTPS 证书或私钥") from exc
    return context


def controlled_address(host):
    ip = ipaddress.ip_address(host)
    return (
        ip.version == 4
        and (
            ip.is_loopback
            or ip.is_private
            or ip in ipaddress.ip_network("100.64.0.0/10")
        )
        and not (ip.is_unspecified or ip.is_multicast or ip.is_reserved)
    )


def validate_binding(host, access):
    _host(host)
    try:
        if not controlled_address(host):
            raise QuantStudioError("受控服务只接受明确的本机或私网 IPv4 地址")
    except ValueError as exc:
        raise QuantStudioError("监听须填写本机或私网 IPv4 地址") from exc
    if host not in access.hosts:
        raise QuantStudioError("监听地址不在允许主机列表中")


class AccessHandler:
    access_policy = None

    def _cookie(self, name):
        values = self.headers.get_all("Cookie") or []
        if len(values) != 1:
            return ""
        try:
            cookie = SimpleCookie()
            cookie.load(values[0])
            return cookie[name].value if name in cookie else ""
        except CookieError:
            return ""

    def _auth_gate(self):
        if self.access_policy is None:
            return True
        path = urlsplit(self.path).path
        if path == "/login":
            if self.command == "POST":
                self._login_post()
            else:
                self._login_page()
            return False
        token = self._cookie(COOKIE)
        session = self.access_policy.session(token)
        if not session:
            self._discard_request_body()
            self._redirect("/login")
            return False
        self.csrf_token = session["csrf"]
        if path == "/logout":
            if self.command == "POST":
                try:
                    data = self._login_form()
                    if set(data) != {"_csrf_token"} or not self._valid_csrf_token(
                        data["_csrf_token"]
                    ):
                        raise QuantStudioError("退出令牌无效")
                except (QuantStudioError, ValueError):
                    self.send_error(403)
                    return False
                self.access_policy.revoke(token)
                self._pending_cookie = (
                    f"{COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0"
                )
                self._redirect("/login")
            else:
                self._send_html(
                    '<html lang="zh-CN"><meta charset="utf-8"><h1>退出工作台</h1>'
                    '<form method="post" action="/logout">'
                    '<input type="hidden" name="_csrf_token" '
                    f'value="{self.csrf_token}">'
                    "<button>确认退出</button></form></html>"
                )
            return False
        return True

    def _login_form(self):
        lengths = self.headers.get_all("Content-Length") or []
        types = self.headers.get_all("Content-Type") or []
        if (
            len(lengths) != 1
            or len(types) != 1
            or self.headers.get_all("Transfer-Encoding")
            or types[0].split(";", 1)[0].lower() != "application/x-www-form-urlencoded"
        ):
            raise QuantStudioError("登录请求格式错误")
        size = int(lengths[0])
        if not 0 < size <= 4096:
            raise QuantStudioError("登录请求过大")
        return parse_qs(self.rfile.read(size).decode("utf-8"), keep_blank_values=True)

    def _login_post(self):
        try:
            data = self._login_form()
            nonce = self._cookie(NONCE)
            if (
                set(data) != {"password", "nonce"}
                or any(len(v) != 1 for v in data.values())
                or not nonce
                or not secrets.compare_digest(nonce.encode(), data["nonce"][0].encode())
            ):
                raise QuantStudioError("登录页面已失效，请重试")
            token = self.access_policy.login(
                data["password"][0], self.client_address[0]
            )
            if token is None:
                self._login_page("密码错误", status=401)
                return
            self._pending_cookie = (
                f"{COOKIE}={token}; Path=/; Secure; HttpOnly; "
                "SameSite=Strict; Max-Age=28800"
            )
            self._redirect("/")
        except (QuantStudioError, ValueError, UnicodeError) as exc:
            self._login_page(str(exc), status=400)

    def _login_page(self, error="", status=200):
        nonce = secrets.token_urlsafe(32)
        self._pending_cookie = (
            f"{NONCE}={nonce}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=300"
        )
        self._send_text(
            f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · Quant Studio</title><style>
body{{font:16px system-ui;background:#f3f5f8;color:#14283e;margin:0}}
main{{max-width:420px;margin:12vh auto;padding:32px;
background:white;border-radius:16px}}
input,button{{box-sizing:border-box;width:100%;padding:14px;
margin:10px 0;font:inherit}}
button{{background:#17635c;color:white;border:0;border-radius:8px}}</style>
<main><h1>Quant Studio</h1><p>连接你的本机研究工作台</p>
<p role="alert">{escape(error)}</p><form method="post" action="/login">
<input type="hidden" name="nonce" value="{nonce}"><label>访问密码
<input name="password" type="password" autocomplete="current-password"
maxlength="256" required autofocus></label><button>登录工作台</button></form>
<small>会话 8 小时后到期；重启服务会退出所有设备。</small></main></html>''',
            status=status,
            content_type="text/html; charset=utf-8",
        )
