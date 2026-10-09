"""Create owner-only local TLS credentials; only the CA certificate is shareable."""

import argparse
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

from quant_studio.access import password_record


def provision(directory: Path, host: str, openssl: str):
    ip = ipaddress.ip_address(host)
    if not (ip.is_private or ip.is_loopback) or ip.is_unspecified:
        raise ValueError("Use a specific LAN IPv4 address")
    if directory.exists():
        raise ValueError("Choose a new private directory; existing credentials are never overwritten")
    directory.mkdir(parents=True, mode=0o700)
    if os.name == "nt":
        owner = subprocess.check_output(["whoami"], text=True).strip()
        subprocess.run(["icacls", str(directory), "/inheritance:r", "/grant:r",
                        f"{owner}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                       check=True, capture_output=True)
    def invoke(*args):
        subprocess.run([openssl, *args], cwd=directory, check=True,
                       capture_output=True, timeout=30)
    invoke("req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes", "-days", "365",
           "-keyout", "ca.key", "-out", "ca.crt", "-subj", "/CN=Quant Studio Private CA",
           "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
           "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    invoke("req", "-new", "-newkey", "rsa:3072", "-nodes", "-keyout", "server.key",
           "-out", "server.csr", "-subj", "/CN=Quant Studio Workbench")
    (directory / "server.ext").write_text(
        f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{host}\n"
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n", encoding="ascii")
    invoke("x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
           "-CAcreateserial", "-out", "server.crt", "-days", "90", "-sha256", "-extfile", "server.ext")
    password = secrets.token_urlsafe(24)
    (directory / "access.json").write_text(
        json.dumps(password_record(password, [host, "127.0.0.1", "localhost"]), indent=2),
        encoding="utf-8")
    (directory / "login-password.txt").write_text(password + "\n", encoding="utf-8")
    print(f"Created TLS and access credentials in {directory}. Password is in login-password.txt.")
    print("Share ca.crt with your own client device. Keep private keys on the server.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--openssl", default=shutil.which("openssl"))
    args = parser.parse_args()
    if not args.openssl:
        parser.error("Provide --openssl (Git for Windows includes usr/bin/openssl.exe)")
    provision(args.output.resolve(), args.host, args.openssl)
