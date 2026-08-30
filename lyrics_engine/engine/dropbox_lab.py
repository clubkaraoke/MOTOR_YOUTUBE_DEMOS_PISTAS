from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DROPBOX_API = "https://api.dropboxapi.com"
DROPBOX_CONTENT = "https://content.dropboxapi.com"


class DropboxLabError(RuntimeError):
    pass


class DropboxLabClient:
    """Cliente Dropbox mínimo para el LAB.

    Usa OAuth offline con refresh token y root namespace cuando Dropbox lo
    devuelve. No modifica archivos: solo lista y descarga CDG.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path(
            os.getenv("CDG_LAB_DATA_DIR", "/data/cdg_lab")
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.auth_path = self.base_dir / "dropbox_auth.json"
        self.state_path = self.base_dir / "dropbox_oauth_state.json"

        self.app_key = os.getenv("DROPBOX_APP_KEY", "").strip()
        self.app_secret = os.getenv("DROPBOX_APP_SECRET", "").strip()
        self.redirect_uri = os.getenv(
            "DROPBOX_REDIRECT_URI",
            "https://panel.kitkaraoke.com/cdg-lyrics/api/lab/dropbox/callback",
        ).strip()

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret and self.redirect_uri)

    def _read_auth(self) -> dict[str, Any]:
        try:
            return json.loads(self.auth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @property
    def connected(self) -> bool:
        auth = self._read_auth()
        return bool(auth.get("refresh_token") or auth.get("access_token"))

    def status(self) -> dict[str, Any]:
        auth = self._read_auth()
        return {
            "configured": self.configured,
            "connected": self.connected,
            "account_id": auth.get("account_id", ""),
            "display_name": auth.get("display_name", ""),
            "email": auth.get("email", ""),
            "root_namespace_id": auth.get("root_namespace_id", ""),
            "home_namespace_id": auth.get("home_namespace_id", ""),
            "redirect_uri": self.redirect_uri,
        }

    def authorization_url(self) -> str:
        if not self.configured:
            raise DropboxLabError(
                "Faltan DROPBOX_APP_KEY y DROPBOX_APP_SECRET en el servidor"
            )

        state = secrets.token_urlsafe(32)
        self.state_path.write_text(
            json.dumps({"state": state}),
            encoding="utf-8",
        )
        params = {
            "client_id": self.app_key,
            "response_type": "code",
            "token_access_type": "offline",
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        return "https://www.dropbox.com/oauth2/authorize?" + urllib.parse.urlencode(
            params
        )

    def _basic_auth(self) -> str:
        raw = f"{self.app_key}:{self.app_secret}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    @staticmethod
    def _json_request(
        request: urllib.request.Request,
    ) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise DropboxLabError(
                f"Dropbox HTTP {exc.code}: {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DropboxLabError(
                f"No se pudo conectar con Dropbox: {exc.reason}"
            ) from exc

        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DropboxLabError("Dropbox devolvió una respuesta inválida") from exc

    def exchange_code(self, code: str, state: str) -> dict[str, Any]:
        if not self.configured:
            raise DropboxLabError("Dropbox OAuth no está configurado")

        try:
            expected = json.loads(
                self.state_path.read_text(encoding="utf-8")
            ).get("state")
        except (OSError, json.JSONDecodeError):
            expected = None

        if not expected or state != expected:
            raise DropboxLabError("Estado OAuth inválido o vencido")

        form = urllib.parse.urlencode(
            {
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            DROPBOX_API + "/oauth2/token",
            data=form,
            method="POST",
            headers={
                "Authorization": self._basic_auth(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        token = self._json_request(request)

        auth = {
            "refresh_token": token.get("refresh_token", ""),
            "access_token": token.get("access_token", ""),
            "account_id": token.get("account_id", ""),
        }
        self.auth_path.write_text(
            json.dumps(auth, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        try:
            self.state_path.unlink(missing_ok=True)
        except OSError:
            pass

        account = self.get_current_account()
        auth.update(
            {
                "display_name": (
                    account.get("name", {}).get("display_name", "")
                    if isinstance(account.get("name"), dict)
                    else ""
                ),
                "email": account.get("email", ""),
                "root_namespace_id": (
                    account.get("root_info", {}).get("root_namespace_id", "")
                    if isinstance(account.get("root_info"), dict)
                    else ""
                ),
                "home_namespace_id": (
                    account.get("root_info", {}).get("home_namespace_id", "")
                    if isinstance(account.get("root_info"), dict)
                    else ""
                ),
            }
        )
        self.auth_path.write_text(
            json.dumps(auth, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.status()

    def _access_token(self) -> str:
        auth = self._read_auth()
        refresh = str(auth.get("refresh_token") or "").strip()

        if refresh:
            if not self.configured:
                raise DropboxLabError(
                    "Hay refresh token, pero faltan APP_KEY/APP_SECRET"
                )

            form = urllib.parse.urlencode(
                {
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                DROPBOX_API + "/oauth2/token",
                data=form,
                method="POST",
                headers={
                    "Authorization": self._basic_auth(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            token = self._json_request(request)
            value = str(token.get("access_token") or "").strip()
            if not value:
                raise DropboxLabError("Dropbox no devolvió access token")
            return value

        value = str(auth.get("access_token") or "").strip()
        if value:
            return value

        raise DropboxLabError("Dropbox todavía no está conectado")

    def _headers(self, *, content: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
        }
        auth = self._read_auth()
        root = str(auth.get("root_namespace_id") or "").strip()
        if root:
            headers["Dropbox-API-Path-Root"] = json.dumps(
                {".tag": "root", "root": root}
            )
        if not content:
            headers["Content-Type"] = "application/json"
        return headers

    def api(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            DROPBOX_API + endpoint,
            data=data,
            method="POST",
            headers=self._headers(),
        )
        return self._json_request(request)

    def get_current_account(self) -> dict[str, Any]:
        return self.api("/2/users/get_current_account", {})

    def list_cdgs(self, folder_path: str) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "path": folder_path,
            "recursive": True,
            "include_deleted": False,
            "include_has_explicit_shared_members": False,
            "include_mounted_folders": True,
            "limit": 2000,
        }
        response = self.api("/2/files/list_folder", payload)

        entries: list[dict[str, Any]] = []

        while True:
            for item in response.get("entries", []):
                if (
                    isinstance(item, dict)
                    and item.get(".tag") == "file"
                    and str(item.get("name", "")).lower().endswith(".cdg")
                ):
                    entries.append(item)

            if not response.get("has_more"):
                break

            cursor = response.get("cursor")
            if not cursor:
                break
            response = self.api(
                "/2/files/list_folder/continue",
                {"cursor": cursor},
            )

        return entries

    def download(self, dropbox_path: str, destination: Path) -> int:
        headers = self._headers(content=True)
        headers["Dropbox-API-Arg"] = json.dumps({"path": dropbox_path})
        request = urllib.request.Request(
            DROPBOX_CONTENT + "/2/files/download",
            method="POST",
            headers=headers,
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                total = 0
                with destination.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        handle.write(chunk)
                return total
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise DropboxLabError(
                f"Dropbox download HTTP {exc.code}: {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DropboxLabError(
                f"No se pudo descargar desde Dropbox: {exc.reason}"
            ) from exc
