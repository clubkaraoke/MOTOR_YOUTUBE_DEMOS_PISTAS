"""Verificación segura de Drive/Sheets; no imprime tokens ni secretos."""

import argparse
import json
import wave
from pathlib import Path

from app.core.config import get_settings
from app.services.google_drive import DriveAudioStorage, drive_service, sheets_service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload-probe", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    folder = drive_service().files().get(
        fileId=settings.drive_audio_folder_id,
        fields="id,name,mimeType,capabilities(canAddChildren)",
        supportsAllDrives=True,
    ).execute()
    sheet = sheets_service().spreadsheets().values().get(
        spreadsheetId=settings.cover_spreadsheet_id,
        range=f"'{settings.cover_sheet_name}'!A1:C3",
    ).execute()
    result = {
        "drive": folder.get("name"),
        "drive_can_add": folder.get("capabilities", {}).get("canAddChildren"),
        "sheet_headers": (sheet.get("values") or [[]])[0],
        "sheet_rows_read": len(sheet.get("values") or []),
    }
    if args.upload_probe:
        probe = settings.processing_dir / "google_upload_probe.wav"
        probe.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(probe), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * 8000)
        uploaded = DriveAudioStorage().upload(probe, "DJGABO_google_integration_probe.wav")
        metadata = drive_service().files().get(
            fileId=uploaded["id"], fields="id,name,parents,size", supportsAllDrives=True
        ).execute()
        result["upload_name"] = metadata.get("name")
        result["upload_parent_ok"] = settings.drive_audio_folder_id in metadata.get("parents", [])
        DriveAudioStorage().delete(uploaded["id"])
        probe.unlink(missing_ok=True)
        result["probe_deleted_after_test"] = True
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
