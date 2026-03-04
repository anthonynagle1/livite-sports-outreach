"""Receipt/invoice image preprocessing for better AI extraction.

Auto-crops to receipt area, detects blur, deskews tilted photos,
enhances contrast (thermal paper), sharpens, and resizes large phone
photos before sending to Claude Vision API.
Reduces token cost and improves extraction accuracy.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ExifTags

logger = logging.getLogger(__name__)

# Max dimension for resized output (Claude Vision handles up to ~1568px well)
MAX_DIMENSION = 1600

# Minimum content area as fraction of image — if crop is tiny, skip it
MIN_CROP_FRACTION = 0.05

# Padding around detected content (pixels before resize)
CROP_PADDING = 20

# Blur detection threshold (edge variance below this = blurry)
BLUR_THRESHOLD = 80.0

# Max deskew rotation (degrees)
MAX_DESKEW_ANGLE = 5


# ── Blur Detection ──


def _get_numpy():
    """Lazy-load numpy; returns None if not installed."""
    try:
        import numpy as np
        return np
    except ImportError:
        return None


def compute_blur_score(img: Image.Image) -> float:
    """Compute blur score using variance of edge detection.

    Higher values = sharper image. Below BLUR_THRESHOLD = likely blurry.
    Uses Laplacian-like edge filter + numpy variance for fast scoring.
    Falls back to Pillow-only statistics if numpy is unavailable.
    """
    gray = img.convert("L")

    # Resize for speed if very large
    w, h = gray.size
    if w > 800 or h > 800:
        ratio = min(800 / w, 800 / h)
        gray = gray.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    edges = gray.filter(ImageFilter.FIND_EDGES)

    np = _get_numpy()
    if np is not None:
        arr = np.array(edges, dtype=np.float64)
        return float(np.var(arr))

    # Fallback: compute variance using Pillow's getdata()
    pixels = list(edges.getdata())
    n = len(pixels)
    if n == 0:
        return 0.0
    mean = sum(pixels) / n
    var = sum((p - mean) ** 2 for p in pixels) / n
    return float(var)


def detect_blur(img: Image.Image) -> dict:
    """Check if image is too blurry for reliable extraction.

    Returns dict with score, is_blurry, and message.
    """
    score = compute_blur_score(img)
    is_blurry = score < BLUR_THRESHOLD

    if is_blurry:
        msg = ("Image appears blurry (score: %.0f). "
               "Consider retaking for better results." % score)
    elif score < BLUR_THRESHOLD * 2:
        msg = ("Image quality is acceptable but could be "
               "sharper (score: %.0f)." % score)
    else:
        msg = "Image is sharp (score: %.0f)." % score

    return {"score": round(score, 1), "is_blurry": is_blurry, "message": msg}


# ── EXIF Orientation ──


def _fix_exif_orientation(img: Image.Image) -> Image.Image:
    """Rotate image based on EXIF orientation tag.

    Phone cameras store orientation in metadata rather than rotating pixels.
    Without this, photos shot in portrait mode appear sideways.
    """
    try:
        exif = img.getexif()
        if not exif:
            return img

        orientation_key = None
        for tag_id, tag_name in ExifTags.TAGS.items():
            if tag_name == "Orientation":
                orientation_key = tag_id
                break

        if orientation_key is None or orientation_key not in exif:
            return img

        orientation = exif[orientation_key]

        transforms = {
            2: Image.FLIP_LEFT_RIGHT,
            3: Image.ROTATE_180,
            4: Image.FLIP_TOP_BOTTOM,
            5: Image.TRANSPOSE,
            6: Image.ROTATE_270,
            7: Image.TRANSVERSE,
            8: Image.ROTATE_90,
        }

        if orientation in transforms:
            img = img.transpose(transforms[orientation])

    except Exception as e:
        logger.debug("EXIF orientation fix failed (non-critical): %s", e)

    return img


# ── Auto-Crop with Adaptive Thresholding ──


def _auto_crop(img: Image.Image, manual_bbox: tuple = None) -> Image.Image:
    """Crop to the receipt/content area, removing background.

    Uses adaptive thresholding based on image statistics instead of a
    fixed brightness value.  Handles varying lighting conditions better.

    Args:
        img: PIL Image
        manual_bbox: Optional (x1, y1, x2, y2) for manual crop override.
    """
    if manual_bbox:
        x1, y1, x2, y2 = manual_bbox
        return img.crop((x1, y1, x2, y2))

    w, h = img.size

    gray = img.convert("L")

    # Compute adaptive threshold from image statistics
    np = _get_numpy()
    if np is not None:
        arr = np.array(gray, dtype=np.float64)
        mean_val = float(np.mean(arr))
        std_val = float(np.std(arr))
    else:
        # Fallback: Pillow stat module
        from PIL import ImageStat
        stat = ImageStat.Stat(gray)
        mean_val = stat.mean[0]
        std_val = stat.stddev[0]

    # Paper is brighter than background.  Adaptive threshold adapts to
    # overall exposure — dark room vs bright room.
    threshold = max(mean_val - 0.3 * std_val, 60)

    # Auto-contrast to sharpen the paper/background boundary
    gray = ImageOps.autocontrast(gray, cutoff=5)

    bw = gray.point(lambda p: 255 if p > threshold else 0)

    bbox = bw.getbbox()
    if bbox is None:
        return img

    x1, y1, x2, y2 = bbox
    crop_w = x2 - x1
    crop_h = y2 - y1

    # Too small = bad detection
    if (crop_w * crop_h) < (w * h * MIN_CROP_FRACTION):
        logger.debug("Auto-crop area too small (%.1f%%), skipping",
                      crop_w * crop_h / (w * h) * 100)
        return img

    # Nearly full image = nothing to crop
    if crop_w > w * 0.9 and crop_h > h * 0.9:
        return img

    x1 = max(0, x1 - CROP_PADDING)
    y1 = max(0, y1 - CROP_PADDING)
    x2 = min(w, x2 + CROP_PADDING)
    y2 = min(h, y2 + CROP_PADDING)

    cropped = img.crop((x1, y1, x2, y2))
    logger.info("Auto-cropped: %dx%d -> %dx%d (%.0f%% reduction)",
                w, h, x2 - x1, y2 - y1,
                (1 - (x2 - x1) * (y2 - y1) / (w * h)) * 100)
    return cropped


# ── Deskew (Straighten Tilted Photos) ──


def _projection_score(img: Image.Image) -> float:
    """Score based on horizontal projection profile variance.

    When text lines are perfectly horizontal, row-sums of a binary image
    have high variance (dense rows alternate with whitespace rows).
    A rotated image smears this pattern, lowering variance.
    """
    np = _get_numpy()
    if np is None:
        return 0.0  # Skip deskew if numpy unavailable
    gray = np.array(img.convert("L"), dtype=np.float64)
    threshold = np.mean(gray)
    binary = (gray < threshold).astype(np.float64)
    row_sums = np.sum(binary, axis=1)
    return float(np.var(row_sums))


def _deskew(img: Image.Image) -> Image.Image:
    """Straighten a slightly rotated receipt/document.

    Uses projection profile analysis: tries small rotation angles,
    picks the one that maximizes horizontal text-line clarity.
    Only corrects angles up to MAX_DESKEW_ANGLE degrees.
    """
    w, h = img.size

    # Work on a center strip for speed
    strip_h = min(h, 500)
    y_start = (h - strip_h) // 2
    strip = img.crop((0, y_start, w, y_start + strip_h))

    # Score at 0 degrees (baseline)
    score_0 = _projection_score(strip)

    # Coarse search: every 1 degree
    best_angle = 0.0
    best_score = score_0

    for angle in range(-MAX_DESKEW_ANGLE, MAX_DESKEW_ANGLE + 1):
        if angle == 0:
            continue
        rotated = strip.rotate(angle, Image.BICUBIC, expand=False,
                               fillcolor=(255, 255, 255))
        score = _projection_score(rotated)
        if score > best_score:
            best_score = score
            best_angle = float(angle)

    # Fine search: 0.25-degree steps around best
    if best_angle != 0:
        coarse_best = best_angle
        for offset_4x in range(-4, 5):
            angle = coarse_best + offset_4x * 0.25
            if angle == coarse_best:
                continue
            if abs(angle) > MAX_DESKEW_ANGLE:
                continue
            rotated = strip.rotate(angle, Image.BICUBIC, expand=False,
                                   fillcolor=(255, 255, 255))
            score = _projection_score(rotated)
            if score > best_score:
                best_score = score
                best_angle = angle

    # Only apply if meaningful improvement (> 5%) and angle >= 0.5 degrees
    if abs(best_angle) >= 0.5 and best_score > score_0 * 1.05:
        logger.info("Deskewed by %.2f degrees", best_angle)
        img = img.rotate(best_angle, Image.BICUBIC, expand=True,
                         fillcolor=(255, 255, 255))

    return img


# ── Enhancement ──


def _enhance_for_ocr(img: Image.Image) -> Image.Image:
    """Enhance image for better text extraction.

    Applies auto-contrast, sharpening, and slight contrast boost.
    Especially helps with faded thermal paper receipts.
    """
    img = ImageOps.autocontrast(img, cutoff=2)
    img = img.filter(ImageFilter.SHARPEN)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.3)
    return img


# ── Resize ──


def _resize_if_large(img: Image.Image) -> Image.Image:
    """Resize image if either dimension exceeds MAX_DIMENSION.

    Phone cameras shoot 4000x3000+ which costs more API tokens.
    Claude Vision works well at 1600px max dimension.
    """
    w, h = img.size
    if w <= MAX_DIMENSION and h <= MAX_DIMENSION:
        return img

    ratio = min(MAX_DIMENSION / w, MAX_DIMENSION / h)
    new_w = int(w * ratio)
    new_h = int(h * ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    logger.info("Resized: %dx%d -> %dx%d", w, h, new_w, new_h)
    return img


# ── Hashing for Duplicate Detection ──


def compute_file_hash(data: bytes) -> str:
    """SHA-256 hash of file bytes for duplicate detection."""
    return hashlib.sha256(data).hexdigest()


# ── Main Pipelines ──


def preprocess_receipt(image_bytes: bytes,
                       manual_crop: tuple = None) -> tuple[bytes, str]:
    """Full preprocessing pipeline for receipt/invoice images.

    Applies: EXIF rotation -> deskew -> auto-crop -> enhance -> resize.
    Returns (processed_jpeg_bytes, "image/jpeg").

    Args:
        image_bytes: Raw image file bytes (any format PIL can read)
        manual_crop: Optional (x1, y1, x2, y2) to override auto-crop

    Returns:
        Tuple of (processed_bytes, media_type) ready for Claude Vision API
    """
    img = Image.open(io.BytesIO(image_bytes))

    if img.mode != "RGB":
        img = img.convert("RGB")

    original_size = img.size

    img = _fix_exif_orientation(img)
    img = _deskew(img)
    img = _auto_crop(img, manual_bbox=manual_crop)
    img = _enhance_for_ocr(img)
    img = _resize_if_large(img)

    final_size = img.size
    logger.info("Preprocessed: %dx%d -> %dx%d",
                original_size[0], original_size[1],
                final_size[0], final_size[1])

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


def preprocess_with_metadata(image_bytes: bytes) -> dict:
    """Preprocess and return metadata for the preview UI.

    Returns dict with preview_b64, blur info, sizes, and crop info.
    """
    img = Image.open(io.BytesIO(image_bytes))

    if img.mode != "RGB":
        img = img.convert("RGB")

    original_size = img.size

    # Blur detection on original
    blur_info = detect_blur(img)

    # Run full pipeline
    processed_bytes, media_type = preprocess_receipt(image_bytes)
    processed_img = Image.open(io.BytesIO(processed_bytes))
    processed_size = processed_img.size

    # Generate preview (smaller for quick display)
    preview_img = processed_img.copy()
    max_preview = 400
    pw, ph = preview_img.size
    if pw > max_preview or ph > max_preview:
        ratio = min(max_preview / pw, max_preview / ph)
        preview_img = preview_img.resize(
            (int(pw * ratio), int(ph * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    preview_img.save(buf, format="JPEG", quality=75)
    preview_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return {
        "preview": "data:image/jpeg;base64," + preview_b64,
        "blur": blur_info,
        "original_size": "%dx%d" % original_size,
        "processed_size": "%dx%d" % processed_size,
        "file_hash": compute_file_hash(image_bytes),
        "size_reduction_pct": round(
            (1 - len(processed_bytes) / len(image_bytes)) * 100, 0
        ) if len(image_bytes) > 0 else 0,
    }
