from pathlib import Path

import pytest
from PIL import Image

from app.services.cover_provider import CoverEntry, CoverProvider, normalize_music_text
from app.services.frame_builder import (
    COVER_POSITION,
    COVER_SIZE,
    QR_CARD_POSITION,
    QR_CARD_SIZE,
    create_frame,
)
from app.services.google_drive import DriveAudioStorage
from app.services.media import YOUTUBE_AUDIO_BITRATE
from app.services.qr import whatsapp_message, whatsapp_url
from app.services.title_builder import build_youtube_title


def test_smart_youtube_title_handles_chorus_and_limit():
    assert build_youtube_title("Vernis - Leyes del Corazón KARAOKE (Coro).mp3") == (
        "Vernis - Leyes del Corazón KARAOKE + COROS | Pista Musical"
    )
    title = build_youtube_title("Artista - " + ("Canción muy larga " * 12) + ".wav")
    assert len(title) <= 100
    assert title.endswith(" | Pista Musical")


def test_cover_matching_is_accent_and_karaoke_tolerant():
    provider = CoverProvider()
    provider._entries = [CoverEntry(
        artist="José José",
        title="El Triste",
        cover_url="https://images.example/el-triste.jpg",
        original_filename="Jose Jose - El Triste KARAOKE.mp3",
    )]
    provider._loaded_at = float("inf")
    match = provider.find("JOSE JOSE", "El Triste (Coro)", "JOSE JOSE - EL TRISTE KARAOKE (Coro).wav")
    assert normalize_music_text("José José — KARAOKE") == "jose jose"
    assert match is not None
    assert match.score >= provider.settings.cover_match_threshold


def test_cover_download_ignores_broken_desktop_proxy(monkeypatch, tmp_path: Path):
    provider = CoverProvider()
    observed = {}

    class Response:
        headers = {"content-type": "image/png"}
        def raise_for_status(self):
            return None
        def iter_content(self, _size):
            fixture = tmp_path / "fixture.png"
            Image.new("RGB", (8, 8), "#123456").save(fixture)
            yield fixture.read_bytes()

    class Session:
        trust_env = True
        def __enter__(self):
            observed["session"] = self
            return self
        def __exit__(self, *_args):
            return None
        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.services.cover_provider.requests.Session", Session)
    target = provider.download("https://res.cloudinary.com/example/image.png", tmp_path / "cover.png")
    assert target.is_file()
    assert observed["session"].trust_env is False


def test_frame_uses_new_fixed_cover_and_qr_contract(tmp_path: Path):
    assert COVER_POSITION == (142, 189)
    assert COVER_SIZE == (381, 381)
    assert QR_CARD_POSITION == (1066, 419)
    assert QR_CARD_SIZE == (138, 138)

    background = tmp_path / "background.png"
    cover = tmp_path / "cover.png"
    Image.new("RGB", (1280, 720), "#101722").save(background)
    Image.new("RGB", (600, 600), "#d82c55").save(cover)
    first = create_frame(background, cover, "Artista", "Canción", "https://demo/q/uno",
                         tmp_path / "one.png", "51999999999")
    second = create_frame(background, cover, "Artista", "Canción", "https://demo/q/dos",
                          tmp_path / "two.png", "51999999999")
    with Image.open(first) as image:
        assert image.size == (1280, 720)
        assert image.getpixel((0, 0)) == (16, 23, 34)
        cover_center = (
            COVER_POSITION[0] + COVER_SIZE[0] // 2,
            COVER_POSITION[1] + COVER_SIZE[1] // 2,
        )
        assert image.getpixel(cover_center) == (216, 44, 85)

        qr_center = (
            QR_CARD_POSITION[0] + QR_CARD_SIZE[0] // 2,
            QR_CARD_POSITION[1] + QR_CARD_SIZE[1] // 2,
        )
        assert image.getpixel(qr_center) in {(0, 0, 0), (255, 255, 255)}
    assert first.read_bytes() != second.read_bytes()


def test_frame_rejects_a_background_that_would_need_resizing(tmp_path: Path):
    background = tmp_path / "wrong.png"
    cover = tmp_path / "cover.png"
    Image.new("RGB", (1920, 1080), "black").save(background)
    Image.new("RGB", (600, 600), "red").save(cover)
    with pytest.raises(ValueError, match="exactamente 1280×720"):
        create_frame(background, cover, "Artista", "Canción", "https://demo/q/uno",
                     tmp_path / "frame.png", "51921675846")


def test_qr_points_directly_to_whatsapp_with_original_audio_name():
    filename = "Alma Bella - Mix Menéalo KARAOKE (Coro).wav"
    assert whatsapp_message(filename) == "*HOLA* me interesa esta Pista Musical (Alma Bella - Mix Menéalo KARAOKE (Coro))"
    url = whatsapp_url("+51 921675846", filename)
    assert url.startswith("https://wa.me/51921675846?text=")
    assert "127.0.0.1" not in url
    assert ".wav" not in url


def test_mock_drive_is_temporary_and_round_trips(tmp_path: Path):
    storage = DriveAudioStorage()
    previous_mode, previous_data = storage.settings.google_mode, storage.settings.data_root
    storage.settings.google_mode = "mock"
    storage.settings.data_root = tmp_path
    try:
        storage.settings.ensure_directories()
        source = tmp_path / "song.mp3"
        source.write_bytes(b"audio-fixture")
        uploaded = storage.upload(source, "Artista - Canción.mp3")
        downloaded = storage.download(uploaded["id"], tmp_path / "work" / "song.mp3")
        assert downloaded.read_bytes() == source.read_bytes()
        storage.delete(uploaded["id"])
        assert not list(storage.settings.mock_drive_dir.glob(f"{uploaded['id']}.*"))
    finally:
        storage.settings.google_mode = previous_mode
        storage.settings.data_root = previous_data


def test_wav_stays_identical_and_duplicate_is_found_in_drive(tmp_path: Path):
    import hashlib

    storage = DriveAudioStorage()
    previous_mode, previous_data = storage.settings.google_mode, storage.settings.data_root
    storage.settings.google_mode = "mock"
    storage.settings.data_root = tmp_path
    try:
        storage.settings.ensure_directories()
        source = tmp_path / "Artista - Canción.wav"
        original_bytes = b"RIFF" + bytes(range(256)) * 4
        source.write_bytes(original_bytes)
        sha256 = hashlib.sha256(original_bytes).hexdigest()
        md5 = hashlib.md5(original_bytes).hexdigest()
        uploaded = storage.upload(source, source.name, sha256=sha256)
        duplicate = storage.find_duplicate(sha256, md5, len(original_bytes))
        downloaded = storage.download(uploaded["id"], tmp_path / "downloaded.wav")
        assert duplicate is not None
        assert duplicate["id"] == uploaded["id"]
        assert downloaded.read_bytes() == original_bytes
        assert YOUTUBE_AUDIO_BITRATE == "384k"
    finally:
        storage.settings.google_mode = previous_mode
        storage.settings.data_root = previous_data
