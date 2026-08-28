from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def process_environment(pid: str) -> dict[str, str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    entries = (part.decode("utf-8") for part in raw.split(b"\0") if part)
    return dict(entry.split("=", 1) for entry in entries if "=" in entry)


def status(opener: urllib.request.OpenerDirector, url: str) -> int:
    try:
        with opener.open(url, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def login(role: str, password: str) -> tuple[urllib.request.OpenerDirector, str]:
    cookies = CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        NoRedirect(),
    )
    payload = json.dumps({"args": [role, password]}).encode("utf-8")
    request = urllib.request.Request(
        "https://panel.kitkaraoke.com/api/call/iniciarSesionConsola",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=15) as response:
        result = json.load(response)
    if result.get("rol") != role or not result.get("token"):
        raise RuntimeError(f"Inicio de sesión inválido para {role}")
    return opener, result["token"]


def logout(opener: urllib.request.OpenerDirector, token: str) -> None:
    payload = json.dumps({"args": [token]}).encode("utf-8")
    request = urllib.request.Request(
        "https://panel.kitkaraoke.com/api/call/cerrarSesionConsola",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener.open(request, timeout=15).close()


def main() -> None:
    environment = process_environment(sys.argv[1])
    results: dict[str, tuple[int, int]] = {}
    for role, variable in (
        ("ADMIN", "DJGABO_ADMIN_PASSWORD"),
        ("CORRECTORA", "DJGABO_CORRECTORA_PASSWORD"),
    ):
        opener, token = login(role, environment[variable])
        try:
            results[role] = (
                status(opener, "https://panel.kitkaraoke.com/cdg/"),
                status(opener, "https://panel.kitkaraoke.com/p-youtube/"),
            )
        finally:
            logout(opener, token)
    if results["ADMIN"] != (200, 200):
        raise RuntimeError(f"Permisos ADMIN inesperados: {results['ADMIN']}")
    if results["CORRECTORA"] != (200, 302):
        raise RuntimeError(f"Acceso CDG de CORRECTORA inesperado: {results['CORRECTORA']}")
    print("ADMIN: CDG=OK P-YOUTUBE=OK")
    print("CORRECTORA: CDG=OK P-YOUTUBE=REDIRIGIDO")


if __name__ == "__main__":
    main()
