"""Reading SMS text from screenshots/photos (local OCR with Tesseract – no data leaves the server).

Requires the `tesseract` program (Linux: `apt install tesseract-ocr`; Windows: the UB-Mannheim
installer, then set TESSERACT_CMD in .env) plus the Python packages Pillow and pytesseract.
"""
import io
import logging
import re
import shutil
import warnings
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger("rhm")

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
MAX_PIXELS = 40_000_000          # refuse decompression bombs
MAX_SIDE = 6000                  # downscale very large photos before OCR
OCR_TIMEOUT = 45                 # seconds per image

OCR_WARNING = ("Read from an image (OCR) – double-check the receipt number, amount and date "
               "(e.g. O/0, I/1, S/5 confusions).")


class OCRError(Exception):
    pass


def _tesseract_cmd():
    return getattr(settings, "TESSERACT_CMD", "") or shutil.which("tesseract") or ""


@lru_cache(maxsize=1)
def availability():
    """Return (available: bool, reason: str)."""
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False, "Python packages Pillow/pytesseract are not installed."
    cmd = _tesseract_cmd()
    if not cmd:
        return False, "The Tesseract OCR program is not installed on the server."
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = cmd
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - environment specific
        return False, f"Tesseract could not be started: {exc}"
    return True, ""


def is_available():
    return availability()[0]


def is_image_name(name):
    return (name or "").lower().endswith(IMAGE_EXTENSIONS)


# ------------------------------------------------------------------ preprocessing
def _load_image(data: bytes):
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            probe.verify()                      # structural check of the file
            img = Image.open(io.BytesIO(data))  # re-open after verify()
            img.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise OCRError("Image is too large.")
    except Exception:
        raise OCRError("The file is not a valid image (PNG, JPG, WEBP, BMP, GIF or TIFF).")
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    img = ImageOps.exif_transpose(img)
    return img


def _prepare(img):
    from PIL import Image, ImageOps, ImageStat

    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    gray = img.convert("L")
    if max(gray.size) > MAX_SIDE:
        gray.thumbnail((MAX_SIDE, MAX_SIDE))
    if ImageStat.Stat(gray).mean[0] < 110:      # dark-mode screenshot → dark text on light
        gray = ImageOps.invert(gray)
    if gray.width < 600:                        # only tiny screenshots need upscaling;
        factor = 2 if gray.width >= 350 else 3  # upscaling normal ones makes 1/I/L errors worse
        gray = gray.resize((gray.width * factor, gray.height * factor), Image.LANCZOS)
    return ImageOps.autocontrast(gray, cutoff=1)


# ------------------------------------------------------------------ text cleanup
_NOISE = [
    r"^\d{1,2}[:.]\d{2}(\s?[AaPp]\.?[Mm]\.?)?$",                                  # bubble timestamps
    r"^(today|yesterday|mon|tue|wed|thu|fri|sat|sun)[a-z]*\b[\w ,:/.]{0,22}$",   # date separators
    r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$",
    r"^(m-?pesa|mpesa|kcb|kcb bank|kcb-?mpesa)$",                                  # chat header
    r"^(text message|sms|imessage|delivered|read|sent|edited|reply|type a message.*)$",
    r"^\W{0,2}\s*[<«‹=]?\s*[il1]?\d{0,3}\s*(m-?pesa|kcb)?\s*$",       # "< 12 MPESA" header
    r"^\W{0,3}$",
]
_NOISE_RE = [re.compile(p, re.I) for p in _NOISE]


def _fix_amount_digits(m):
    return m.group(1) + m.group(2).replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")


def clean_ocr_text(text: str) -> str:
    """Drop chat-UI noise (timestamps, headers) and repair common OCR slips in amounts."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip().replace("|", "I").replace("“", '"').replace("”", '"').replace("’", "'")
        if not line:
            out.append("")
            continue
        if any(r.match(line) for r in _NOISE_RE) and not re.search(r"(ksh|kes)\s*\d", line, re.I):
            out.append("")
            continue
        out.append(line)
    text = "\n".join(out)
    # "Ksh1O,OOO.OO" / "KES 5l0.00" → digits
    text = re.sub(r"(\b(?:Ksh|KES|Kshs)\.?\s?)([\dOolI][\dOolI,]*(?:\.[\dOolI]{2})?)", _fix_amount_digits, text)
    text = re.sub(r"\bKsh\s+(?=\d)", "Ksh", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def regroup_messages(text: str) -> str:
    """Re-assemble SMS bodies from OCR lines.

    Chat bubbles wrap lines and OCR inserts random blank lines inside a bubble, so
    paragraphs are unreliable. Instead the lines are joined into one stream and split at
    real message starts ("ABC123XYZ9 Confirmed", "Dear …"). Fragments before the first
    start (chat header etc.) are dropped unless they contain an amount.
    Returns messages separated by blank lines (the format the extractor expects).
    """
    from core.parsing.extractor import _START, normalize

    stream = normalize(" ".join(ln.strip() for ln in text.splitlines() if ln.strip()))
    parts = [p.strip() for p in _START.split(stream) if p.strip()]
    if len(parts) > 1 or (parts and _START.match(parts[0])):
        msgs = [p for i, p in enumerate(parts) if _START.match(p) or (i == 0 and _has_amount(p))]
    else:  # no recognisable start markers – fall back to paragraphs
        paras = [normalize(p) for p in re.split(r"\n\s*\n", text) if p.strip()]
        msgs = [p for p in paras if len(p) >= 25 or _has_amount(p)]
    return "\n\n".join(msgs)


def _has_amount(s):
    return bool(re.search(r"(ksh|kes)\.?\s?\d", s, re.I))


# ------------------------------------------------------------------ public API
def ocr_image(data: bytes) -> str:
    ok, reason = availability()
    if not ok:
        raise OCRError(f"Image extraction is not available: {reason}")
    import pytesseract

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise OCRError(f"Image is larger than {settings.MAX_UPLOAD_MB} MB.")
    img = _prepare(_load_image(data))
    try:
        raw = pytesseract.image_to_string(
            img, lang=getattr(settings, "OCR_LANG", "eng"), config="--oem 1 --psm 4", timeout=OCR_TIMEOUT
        )
    except RuntimeError as exc:  # timeout
        raise OCRError(f"Reading the image took too long: {exc}")
    except Exception as exc:
        logger.warning("OCR failure: %s", exc)
        raise OCRError("The image could not be read.")
    text = regroup_messages(clean_ocr_text(raw))
    if not text:
        raise OCRError("No text was found in the image.")
    return text
