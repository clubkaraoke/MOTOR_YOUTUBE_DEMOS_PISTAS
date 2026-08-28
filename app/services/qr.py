from pathlib import Path
from urllib.parse import quote


def whatsapp_message(original_filename: str) -> str:
    """Build the exact customer message from the uploaded audio name."""
    original_name = Path(original_filename).stem
    return f"*HOLA* me interesa esta Pista Musical ({original_name})"


def whatsapp_url(number: str, original_filename: str) -> str:
    normalized_number = number.replace("+", "").replace(" ", "")
    return f"https://wa.me/{normalized_number}?text={quote(whatsapp_message(original_filename), safe='')}"
