import json

from app.core.config import get_settings


class YouTubeOAuthConfigError(RuntimeError):
    pass


def youtube_oauth_client() -> dict:
    """Return a Google web OAuth client without exposing its secret in logs."""
    settings = get_settings()
    if settings.youtube_client_id and settings.youtube_client_secret:
        return {
            "client_id": settings.youtube_client_id,
            "client_secret": settings.youtube_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    path = settings.youtube_oauth_client_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        client = payload["web"]
        if not client.get("client_id") or not client.get("client_secret"):
            raise KeyError("client_id/client_secret")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise YouTubeOAuthConfigError(
            f"Configura el cliente OAuth web en {path}"
        ) from exc
    return client


def google_client_config(redirect_uri: str) -> dict:
    client = youtube_oauth_client()
    return {"web": {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "auth_uri": client.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
        "token_uri": client.get("token_uri", "https://oauth2.googleapis.com/token"),
        "redirect_uris": [redirect_uri],
    }}
