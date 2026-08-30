from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageOps


CANVAS_SIZE = (1280, 720)

# Contrato visual de las plantillas nuevas 1280×720.
# El cover es INAMOVIBLE: estas coordenadas coinciden con el cuadro fijo
# reservado para la imagen jalada desde 01_CEREBRO2.
# Caja exterior reservada en la plantilla. Se conserva exactamente en su sitio.
COVER_POSITION = (142, 189)
COVER_SIZE = (381, 381)
COVER_RADIUS = 61

# El cover real va 8 px hacia dentro para recuperar el marco blanco visible
# que tenían las miniaturas originales, sin mover la caja exterior.
COVER_BORDER = 8
COVER_IMAGE_POSITION = (COVER_POSITION[0] + COVER_BORDER, COVER_POSITION[1] + COVER_BORDER)
COVER_IMAGE_SIZE = (COVER_SIZE[0] - COVER_BORDER * 2, COVER_SIZE[1] - COVER_BORDER * 2)
COVER_IMAGE_RADIUS = max(1, COVER_RADIUS - COVER_BORDER)

# El QR dinámico se genera dentro del nuevo recuadro del diseño, sin invadir
# el texto "ESCANEA Y RECÍBELA". La tarjeta blanca tapa cualquier QR de muestra
# de la plantilla y deja visible el borde verde exterior.
QR_CARD_POSITION = (1066, 419)
QR_CARD_SIZE = (138, 138)
QR_CARD_RADIUS = 8
QR_MAX_SIZE = 138


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
            COVER_IMAGE_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )


def _qr_image(value: str) -> Image.Image:
    """Render a sharp QR sized for the small TV/thumbnail QR slot."""
    code = qrcode.QRCode(
        version=None,
        # A clean, high-contrast rendered QR does not need embedded-logo
        # recovery. L keeps the matrix smaller so modules stay larger on TV.
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        # Quiet zone reducido a 2 módulos: el QR ocupa ~132×132 dentro
        # del cuadro de 138×138, dejando apenas ~3 px visuales por lado.
        border=2,
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
    """Composite the fixed cover and optional dynamic QR over a 1280×720 template."""
    del artist, title, whatsapp_number
    with Image.open(base_background) as source:
        if source.size != CANVAS_SIZE:
            raise ValueError(
                f"La plantilla debe medir exactamente 1280×720 px; mide {source.width}×{source.height} px"
            )
        canvas = source.convert("RGBA")

    # Marco blanco real del motor. Así no dependemos de que la plantilla
    # tenga el borde dibujado y el cover nunca vuelve a taparlo.
    cover_frame = Image.new("RGBA", COVER_SIZE, (255, 255, 255, 255))
    cover_frame.putalpha(_rounded_mask(COVER_SIZE, COVER_RADIUS))
    canvas.alpha_composite(cover_frame, COVER_POSITION)

    cover = _cover_crop(cover_path)
    canvas.paste(
        cover,
        COVER_IMAGE_POSITION,
        _rounded_mask(COVER_IMAGE_SIZE, COVER_IMAGE_RADIUS),
    )

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
