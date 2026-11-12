"""Safe raster derivatives for displaying quarantined-media-approved assets.

Only decoded PNG/JPEG/WebP/GIF inputs are accepted. The preview is re-encoded
as metadata-free PNG so untrusted source bytes are never served inline.
"""
from __future__ import annotations

from io import BytesIO


MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 12_000
MAX_PREVIEW_BYTES = 20 * 1024 * 1024
_ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "GIF"})


class AnnotationImagePreviewError(ValueError):
    """The clean asset is not a supported, bounded raster image."""


def create_safe_png_preview(data: bytes) -> bytes:
    """Decode and re-encode one bounded raster frame as a fresh PNG."""
    try:
        from PIL import Image, UnidentifiedImageError
        from PIL.Image import DecompressionBombError
    except ImportError as exc:
        raise RuntimeError("annotation_image_preview_dependency_missing") from exc

    if not isinstance(data, bytes) or not data:
        raise AnnotationImagePreviewError("empty_image")

    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in _ALLOWED_FORMATS:
                raise AnnotationImagePreviewError("unsupported_image_format")
            width, height = source.size
            if (width < 1 or height < 1 or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_IMAGE_PIXELS):
                raise AnnotationImagePreviewError("image_dimensions_exceeded")
            if getattr(source, "n_frames", 1) != 1:
                raise AnnotationImagePreviewError("animated_images_not_supported")
            source.verify()

        with Image.open(BytesIO(data)) as decoded:
            if decoded.format not in _ALLOWED_FORMATS or getattr(decoded, "n_frames", 1) != 1:
                raise AnnotationImagePreviewError("unsupported_image_content")
            decoded.load()
            # Convert to a fresh pixel buffer; source metadata and profiles are not copied.
            mode = "RGBA" if "A" in decoded.getbands() else "RGB"
            clean = decoded.convert(mode)
            output = BytesIO()
            clean.save(output, format="PNG", optimize=False, compress_level=6)
            preview = output.getvalue()
            clean.close()
    except AnnotationImagePreviewError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, DecompressionBombError) as exc:
        raise AnnotationImagePreviewError("invalid_image_content") from exc

    if not preview or len(preview) > MAX_PREVIEW_BYTES:
        raise AnnotationImagePreviewError("preview_size_exceeded")
    return preview
