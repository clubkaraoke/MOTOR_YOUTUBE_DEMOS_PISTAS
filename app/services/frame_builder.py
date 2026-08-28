from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageOps


CANVAS_SIZE = (1280, 720)

# Medidas tomadas de PLANTILLA VACIA.png y DISEÑO FINAL.png. Estas coordenadas
# son parte del contrato visual: no deben adaptarse al contenido.
COVER_POSITION = (145, 193)
COVER_SIZE = (374, 373)
COVER_RADIUS = 61
QR_CARD_POSITION = (841, 216)
QR_CARD_SIZE = (317, 317)
QR_CARD_RADIUS = 18
QR_MAX_SIZE = 309


def _rounded_mask(size: tuple[int, int], radius: int, scale: int = 4) -> Image.Image:
    """Create an antialiased fixed-size rounded mask."""
    width, height = size
    mask = Image.new("L", (width * scale, height * scale), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1),
        radius=radius * scale,
        fill=255,
    )
    return mask.resize(size, Image.Resampling.LANCZOS)


def _cover_crop(path: Path) -> Image.Image:
    """Crop-to-fill from the center without ever stretching the artwork."""
    with Image.open(path) as image:
        return ImageOps.fit(
            image.convert("RGB"),
            COVER_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )


def _qr_image(value: str) -> Image.Image:
    """Render a large sharp QR matching the supplied final-design footprint."""
    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    code.add_data(value)
    code.make(fit=True)
    matrix = code.get_matrix()
    modules = len(matrix)
    box_size = max(1, QR_MAX_SIZE // modules)
    side = modules * box_size
    image = Image.new("RGB", (side, side), "white")
    draw = ImageDraw.Draw(image)
    for row, values in enumerate(matrix):
        for column, enabled in enumerate(values):
            if enabled:
                x = column * box_size
                y = row * box_size
                draw.rectangle((x, y, x + box_size - 1, y + box_size - 1), fill="black")
    return image


def create_frame(base_background: Path, cover_path: Path, artist: str, title: str, qr_url: str,
                 output_path: Path, whatsapp_number: str, include_qr: bool = True) -> Path:
    """Composite the cover and, when requested, the QR over an immutable template."""
    del artist, title, whatsapp_number
    with Image.open(base_background) as source:
        if source.size != CANVAS_SIZE:
            raise ValueError(
                f"La plantilla debe medir exactamente 1280×720 px; mide {source.width}×{source.height} px"
            )
        canvas = source.convert("RGBA")

    cover = _cover_crop(cover_path)
    canvas.paste(cover, COVER_POSITION, _rounded_mask(COVER_SIZE, COVER_RADIUS))

    if include_qr:
        qr_card = Image.new("RGBA", QR_CARD_SIZE, (255, 255, 255, 0))
        qr_card.putalpha(_rounded_mask(QR_CARD_SIZE, QR_CARD_RADIUS))
        qr = _qr_image(qr_url)
        qr_x = (QR_CARD_SIZE[0] - qr.width) // 2
        qr_y = (QR_CARD_SIZE[1] - qr.height) // 2
        qr_card.paste(qr, (qr_x, qr_y))
        canvas.alpha_composite(qr_card, QR_CARD_POSITION)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, "PNG", optimize=True)
    return output_path
