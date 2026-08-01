"""Image preparation.

Two traps this module exists to avoid:

  * Format. EPUB 3's core image types are GIF, JPEG, PNG, SVG and WebP. A TIFF
    passes straight through an unwary packager and then fails to display on
    most devices — publishers do deposit TIFFs. Anything outside the core set
    is converted; anything that cannot be converted is dropped and *reported*.
  * Size. Journal figures are deposited at print resolution. Shipped as-is they
    make a volume hundreds of megabytes and time out e-ink readers, so they are
    bounded to a sensible long edge and re-encoded.

No font is ever embedded, here or anywhere else in the edition, so there is no
font licence to honour and nothing that fails to load on a fixed-font device.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 300_000_000

log = logging.getLogger(__name__)

CORE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/svg+xml", "image/webp"}
MAX_EDGE = 1600          # generous for a 300 ppi 6" e-ink panel
JPEG_QUALITY = 82


@dataclass(slots=True)
class PreparedImage:
    data: bytes
    filename: str
    mimetype: str
    width: int
    height: int
    note: str = ""


def prepare(data: bytes, filename: str, mimetype: str | None,
            max_edge: int = MAX_EDGE) -> PreparedImage | None:
    """Return an EPUB-safe image, or None if the bytes are not a usable image."""
    if (mimetype or "") == "image/svg+xml" or filename.lower().endswith(".svg"):
        return PreparedImage(data, filename, "image/svg+xml", 0, 0)

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:  # noqa: BLE001 - any decode failure is the same outcome
        log.warning("cannot decode image %s: %s", filename, e)
        return None

    note = ""
    src_format = (im.format or "").upper()
    w0, h0 = im.size

    if max(im.size) > max_edge:
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        note = f"resized from {w0}x{h0}"

    # Decide the output encoding.
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if src_format == "GIF" and getattr(im, "n_frames", 1) > 1:
        # Keep animation rather than freezing it to a still.
        if max(im.size) <= max_edge and (w0, h0) == im.size:
            return PreparedImage(data, filename, "image/gif", w0, h0)
        out_format, mime, ext = "GIF", "image/gif", ".gif"
    elif has_alpha or src_format in ("PNG", "TIFF", "BMP") and _looks_flat(im):
        out_format, mime, ext = "PNG", "image/png", ".png"
    else:
        out_format, mime, ext = "JPEG", "image/jpeg", ".jpg"

    if out_format == "JPEG" and im.mode not in ("RGB", "L"):
        im = im.convert("RGB") if im.mode != "LA" else im.convert("L")
    if out_format == "PNG" and im.mode == "CMYK":
        im = im.convert("RGB")

    buf = io.BytesIO()
    save_kw = {"optimize": True}
    if out_format == "JPEG":
        save_kw.update(quality=JPEG_QUALITY, progressive=True)
    try:
        im.save(buf, out_format, **save_kw)
    except OSError as e:
        log.warning("cannot re-encode %s as %s: %s", filename, out_format, e)
        return None

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    new_name = stem + ext
    if src_format not in (out_format, None) and src_format:
        note = (note + "; " if note else "") + f"converted {src_format}->{out_format}"

    data_out = buf.getvalue()
    # Re-encoding a already-small JPEG can grow it; keep the smaller one.
    if out_format == "JPEG" and src_format == "JPEG" and len(data_out) >= len(data) \
            and (w0, h0) == im.size:
        return PreparedImage(data, filename, "image/jpeg", w0, h0)

    return PreparedImage(data_out, new_name, mime, im.size[0], im.size[1], note)


def _looks_flat(im: Image.Image) -> bool:
    """Line art and charts compress far better as PNG than JPEG; photographs
    do not. A cheap colour-count probe separates the two well enough."""
    try:
        small = im.convert("RGB").resize((min(im.width, 200), min(im.height, 200)))
        colours = small.getcolors(maxcolors=4096)
        return colours is not None and len(colours) <= 512
    except Exception:  # noqa: BLE001
        return False
