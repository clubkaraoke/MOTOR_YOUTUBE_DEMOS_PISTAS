import hashlib
import shutil
import uuid
from pathlib import Path

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from app.core.config import get_settings


SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


class GoogleStorageError(RuntimeError):
    pass


def google_credentials():
    settings = get_settings()
    if not settings.google_credentials_file:
        raise GoogleStorageError("GOOGLE_CREDENTIALS_FILE no configurado")
    path = Path(settings.google_credentials_file)
    if not path.is_file():
        raise GoogleStorageError(f"Credenciales Google inexistentes: {path}")
    credentials, _ = google.auth.load_credentials_from_file(str(path), scopes=SCOPES)
    return credentials


def drive_service():
    return build("drive", "v3", credentials=google_credentials(), cache_discovery=False)


def sheets_service():
    return build("sheets", "v4", credentials=google_credentials(), cache_discovery=False)


class DriveAudioStorage:
    def __init__(self):
        self.settings = get_settings()

    def find_duplicate(self, sha256: str, md5: str, size_bytes: int) -> dict | None:
        """Find the exact same bytes in the engine's temporary Drive folder."""
        if self.settings.google_mode == "mock":
            for path in self.settings.mock_drive_dir.glob("mock_*.*"):
                if path.stat().st_size != size_bytes:
                    continue
                digest = hashlib.md5()
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() == md5:
                    return {"id": path.stem, "name": path.name, "size": str(size_bytes), "md5Checksum": md5}
            return None
        try:
            service = drive_service()
            page_token = None
            while True:
                result = service.files().list(
                    q=f"'{self.settings.drive_audio_folder_id}' in parents and trashed = false",
                    spaces="drive",
                    fields="nextPageToken,files(id,name,size,md5Checksum,appProperties,createdTime)",
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()
                for item in result.get("files", []):
                    properties = item.get("appProperties") or {}
                    same_sha = properties.get("sha256") == sha256
                    same_md5 = item.get("md5Checksum") == md5 and int(item.get("size", -1)) == size_bytes
                    if same_sha or same_md5:
                        return item
                page_token = result.get("nextPageToken")
                if not page_token:
                    return None
        except Exception as exc:
            raise GoogleStorageError(f"No se pudo comprobar duplicados en Drive: {exc}") from exc

    def get_metadata(self, file_id: str) -> dict:
        if self.settings.google_mode == "mock":
            matches = list(self.settings.mock_drive_dir.glob(f"{file_id}.*"))
            if not matches:
                raise GoogleStorageError(f"Audio mock no encontrado: {file_id}")
            return {"id": file_id, "name": matches[0].name, "size": str(matches[0].stat().st_size), "trashed": False}
        try:
            return drive_service().files().get(
                fileId=file_id,
                fields="id,name,size,md5Checksum,trashed,parents",
                supportsAllDrives=True,
            ).execute()
        except Exception as exc:
            raise GoogleStorageError(f"El audio existente ya no está disponible en Drive: {exc}") from exc

    def upload(self, source: Path, original_name: str, sha256: str | None = None) -> dict:
        if self.settings.google_mode == "mock":
            file_id = f"mock_{uuid.uuid4().hex}"
            target = self.settings.mock_drive_dir / f"{file_id}{source.suffix.lower()}"
            shutil.copy2(source, target)
            return {"id": file_id, "name": original_name}
        # MediaFileUpload sends the original bytes. WAV remains WAV; Drive does
        # not transcode or recompress the source master.
        media = MediaFileUpload(str(source), resumable=True)
        body = {"name": original_name, "parents": [self.settings.drive_audio_folder_id]}
        if sha256:
            body["appProperties"] = {"sha256": sha256}
        try:
            return drive_service().files().create(
                body=body,
                media_body=media, fields="id,name", supportsAllDrives=True,
            ).execute()
        except Exception as exc:
            raise GoogleStorageError(f"No se pudo subir el audio a Drive: {exc}") from exc

    def download(self, file_id: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.google_mode == "mock":
            matches = list(self.settings.mock_drive_dir.glob(f"{file_id}.*"))
            if not matches:
                raise GoogleStorageError(f"Audio mock no encontrado: {file_id}")
            shutil.copy2(matches[0], destination)
            return destination
        try:
            request = drive_service().files().get_media(fileId=file_id, supportsAllDrives=True)
            with destination.open("wb") as handle:
                downloader = MediaIoBaseDownload(handle, request, chunksize=8 * 1024 * 1024)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            return destination
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise GoogleStorageError(f"No se pudo descargar el audio de Drive: {exc}") from exc

    def delete(self, file_id: str) -> None:
        if self.settings.google_mode == "mock":
            for path in self.settings.mock_drive_dir.glob(f"{file_id}.*"):
                path.unlink(missing_ok=True)
            return
        try:
            drive_service().files().delete(fileId=file_id, supportsAllDrives=True).execute()
        except Exception as exc:
            raise GoogleStorageError(f"No se pudo eliminar el audio de Drive: {exc}") from exc
