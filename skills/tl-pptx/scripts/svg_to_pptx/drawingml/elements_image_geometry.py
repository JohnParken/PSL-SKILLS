"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

import hashlib
import io
import math
import re
from typing import Any
from xml.etree import ElementTree as ET


from .context import (
    ConvertContext,
)
from .utils import (
    SVG_NS,
    PROJECT_IMAGE_ASPECT_RATIO_ANCHORS,
    px_to_emu,
    parse_svg_length,
    rect_to_dml_xfrm,
    parse_project_image_aspect_ratio,
    quantize_ooxml_alpha,
    matrix_multiply,
    parse_transform_matrix,
)



# Adapted from elements.py; upstream MIT, Hugo He, 2025-2026.
def _picture_xfrm_from_rect(
    ctx: ConvertContext,
    x: float,
    y: float,
    w: float,
    h: float,
) -> tuple[str, int, int, int, int, tuple[int, int, int, int]]:
    """Build DrawingML xfrm data for a picture rectangle.

    Coordinates ``x``, ``y``, ``w``, ``h`` MUST already be in ctx-resolved
    space (i.e. callers have applied ``ctx_x`` / ``ctx_w`` upstream). When
    ``ctx.use_transform_matrix`` is set, raw SVG-space coordinates are
    expected and the matrix path applies the transform itself.
    """
    if ctx.use_transform_matrix:
        return rect_to_dml_xfrm(x, y, w, h, ctx.transform_matrix)

    off_x = px_to_emu(x)
    off_y = px_to_emu(y)
    ext_cx = px_to_emu(w)
    ext_cy = px_to_emu(h)
    return '', off_x, off_y, ext_cx, ext_cy, (off_x, off_y, off_x + ext_cx, off_y + ext_cy)


def _picture_xfrm_from_svg_rect(
    ctx: ConvertContext,
    raw_x: float,
    raw_y: float,
    raw_w: float,
    raw_h: float,
    resolved_x: float,
    resolved_y: float,
    resolved_w: float,
    resolved_h: float,
    transform: str | None,
) -> tuple[str, int, int, int, int, tuple[int, int, int, int]]:
    """Build picture xfrm data, honoring element-level SVG transforms.

    ``raw_*`` values stay in the element's source SVG coordinate space for
    matrix decomposition; ``resolved_*`` values are the existing scalar path.
    """
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _picture_xfrm_from_rect,
    )
    if ctx.use_transform_matrix:
        matrix = ctx.transform_matrix
        if transform:
            matrix = matrix_multiply(matrix, parse_transform_matrix(transform))
        return rect_to_dml_xfrm(raw_x, raw_y, raw_w, raw_h, matrix)

    if transform:
        context_matrix = (
            ctx.scale_x, 0.0,
            0.0, ctx.scale_y,
            ctx.translate_x, ctx.translate_y,
        )
        matrix = matrix_multiply(context_matrix, parse_transform_matrix(transform))
        return rect_to_dml_xfrm(raw_x, raw_y, raw_w, raw_h, matrix)

    return _picture_xfrm_from_rect(ctx, resolved_x, resolved_y, resolved_w, resolved_h)


def _picture_rendered_frame_size(
    ctx: ConvertContext,
    raw_x: float,
    raw_y: float,
    raw_w: float,
    raw_h: float,
    resolved_x: float,
    resolved_y: float,
    resolved_w: float,
    resolved_h: float,
    transform: str | None,
) -> tuple[float, float]:
    """Return final DrawingML picture-axis lengths in rendered SVG pixels."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _picture_xfrm_from_svg_rect,
    )
    _attr, _x, _y, ext_cx, ext_cy, _bounds = _picture_xfrm_from_svg_rect(
        ctx,
        raw_x,
        raw_y,
        raw_w,
        raw_h,
        resolved_x,
        resolved_y,
        resolved_w,
        resolved_h,
        transform,
    )
    emu_per_px = px_to_emu(1.0)
    return ext_cx / emu_per_px, ext_cy / emu_per_px


def _read_svg_image_size(data: bytes) -> tuple[float, float] | None:
    """Read an SVG image's viewport ratio from root dimensions or viewBox."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if root.tag != f'{{{SVG_NS}}}svg':
        return None

    try:
        width = parse_svg_length(root.get('width'))
        height = parse_svg_length(root.get('height'))
    except ValueError:
        width = 0.0
        height = 0.0
    if (
        width > 0
        and height > 0
        and math.isfinite(width)
        and math.isfinite(height)
    ):
        return width, height

    view_box = root.get('viewBox')
    if view_box:
        parts = [
            part for part in re.split(r'[\s,]+', view_box.strip())
            if part
        ]
        try:
            values = [float(part) for part in parts]
        except ValueError:
            values = []
        if (
            len(values) == 4
            and all(math.isfinite(value) for value in values)
            and values[2] > 0
            and values[3] > 0
        ):
            return values[2], values[3]
    return None


def _read_image_size(data: bytes) -> tuple[float | None, float | None]:
    """Read intrinsic image dimensions (width, height) from raw bytes.

    Used by ``convert_image`` to translate SVG ``preserveAspectRatio`` into
    DrawingML ``<a:srcRect>`` so the original image is preserved and remains
    croppable inside PowerPoint.

    SVG images use valid root ``width`` / ``height`` as the viewport ratio and
    fall back to ``viewBox`` when either dimension is unavailable. Raster
    images use EXIF-normalized pixel dimensions. Returns ``(None, None)`` on
    any failure.
    """
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _read_svg_image_size,
    )
    svg_size = _read_svg_image_size(data)
    if svg_size is not None:
        return svg_size

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError  # type: ignore
    except ImportError:
        return (None, None)
    try:
        with Image.open(io.BytesIO(data)) as img:
            oriented = ImageOps.exif_transpose(img)
            try:
                return oriented.size
            finally:
                if oriented is not img:
                    oriented.close()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        ZeroDivisionError,
    ):
        return (None, None)


def _image_has_alpha(img: Any) -> bool:
    """Return whether a PIL image carries useful transparency."""
    if img.mode in ('RGBA', 'LA'):
        return True
    return 'transparency' in getattr(img, 'info', {})


def _prepare_raster_for_geometry(img: Any) -> Any:
    """Apply EXIF orientation and materialize palette/tRNS transparency."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _image_has_alpha,
    )
    try:
        from PIL import ImageOps  # type: ignore
    except ImportError:
        return img

    prepared = ImageOps.exif_transpose(img)
    if prepared.mode == 'P':
        prepared = prepared.convert(
            'RGBA' if _image_has_alpha(prepared) else 'RGB'
        )
    elif (
        'transparency' in getattr(prepared, 'info', {})
        and prepared.mode not in {'RGBA', 'LA'}
    ):
        prepared = prepared.convert('RGBA')
    return prepared


def _has_exif_geometry_transform(img: Any) -> bool:
    """Return whether EXIF requires a physical mirror or rotation."""
    try:
        return int(img.getexif().get(274, 1)) in range(2, 9)
    except (AttributeError, TypeError, ValueError):
        return False


def _image_target_size(
    display_w: float,
    display_h: float,
    *,
    max_dimension: int | None,
    scale: float,
) -> tuple[int, int]:
    """Resolve optimized pixel dimensions from rendered SVG dimensions."""
    target_w = max(1, int(round(display_w * max(scale, 1.0))))
    target_h = max(1, int(round(display_h * max(scale, 1.0))))
    if max_dimension and max(target_w, target_h) > max_dimension:
        ratio = max_dimension / max(target_w, target_h)
        target_w = max(1, int(round(target_w * ratio)))
        target_h = max(1, int(round(target_h * ratio)))
    return target_w, target_h


def _visible_source_scale_floor(
    img_w: int,
    img_h: int,
    display_w: float,
    display_h: float,
    visible_source_fraction: tuple[float, float],
) -> float:
    """Keep each rendered axis at or below its visible source pixels."""
    fraction_w, fraction_h = visible_source_fraction
    visible_w = img_w * fraction_w
    visible_h = img_h * fraction_h
    if visible_w <= 0 or visible_h <= 0:
        return 1.0
    return min(1.0, max(display_w / visible_w, display_h / visible_h))


def _fit_full_image_target(
    img_w: int,
    img_h: int,
    display_w: float,
    display_h: float,
    *,
    sizing: str,
    max_dimension: int | None,
    scale: float,
    visible_source_fraction: tuple[float, float] | None = None,
) -> tuple[int, int]:
    """Size the full source image; never crop pixels.

    ``cap`` mode limits oversized sources unless that would leave a cropped
    or stretched picture with fewer visible source pixels than its final
    rendered frame. ``display`` mode targets the configured rendered-frame
    scale while retaining the same one-source-pixel-per-display-pixel floor.
    """
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _image_target_size,
        _visible_source_scale_floor,
    )
    if img_w <= 0 or img_h <= 0:
        return (1, 1)

    enforce_visible_floor = visible_source_fraction is not None
    if enforce_visible_floor and (
        any(
            not math.isfinite(fraction) or fraction <= 0
            for fraction in visible_source_fraction
        )
    ):
        return (img_w, img_h)

    if sizing == 'cap':
        ratio = 1.0
        if max_dimension and max(img_w, img_h) > max_dimension:
            ratio = max_dimension / max(img_w, img_h)
        if enforce_visible_floor:
            ratio = max(
                ratio,
                _visible_source_scale_floor(
                    img_w,
                    img_h,
                    display_w,
                    display_h,
                    visible_source_fraction,
                ),
            )
        ratio = min(1.0, ratio)
        target_w = max(1, int(math.ceil(img_w * ratio)))
        target_h = max(1, int(math.ceil(img_h * ratio)))
        return target_w, target_h

    target_display_w, target_display_h = _image_target_size(
        display_w,
        display_h,
        max_dimension=None,
        scale=scale,
    )

    if enforce_visible_floor:
        fraction_w, fraction_h = visible_source_fraction
        preferred_scale = min(
            1.0,
            max(
                target_display_w / (img_w * fraction_w),
                target_display_h / (img_h * fraction_h),
            ),
        )
        visible_floor = _visible_source_scale_floor(
            img_w,
            img_h,
            display_w,
            display_h,
            visible_source_fraction,
        )
        resize_scale = preferred_scale
        if max_dimension and max(img_w, img_h) * resize_scale > max_dimension:
            resize_scale = max(
                visible_floor,
                max_dimension / max(img_w, img_h),
            )
        target_w = int(math.ceil(img_w * resize_scale))
        target_h = int(math.ceil(img_h * resize_scale))
    else:
        ratio = min(
            target_display_w / img_w,
            target_display_h / img_h,
            1.0,
        )
        target_w = int(round(img_w * ratio))
        target_h = int(round(img_h * ratio))

    target_w = max(1, target_w)
    target_h = max(1, target_h)
    if (
        not enforce_visible_floor
        and max_dimension
        and max(target_w, target_h) > max_dimension
    ):
        ratio = max_dimension / max(target_w, target_h)
        target_w = max(1, int(round(target_w * ratio)))
        target_h = max(1, int(round(target_h * ratio)))
    return target_w, target_h


def _resize_for_target(img: Any, target_w: int, target_h: int) -> Any:
    """Downscale a PIL image to the target dimensions without upsampling."""
    width, height = img.size
    if target_w >= width and target_h >= height:
        return img
    ratio = min(target_w / width, target_h / height)
    if ratio >= 1.0:
        return img
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return img
    new_size = (
        max(1, int(math.ceil(width * ratio))),
        max(1, int(math.ceil(height * ratio))),
    )
    return img.resize(new_size, Image.Resampling.LANCZOS)


def _encode_optimized_image(img: Any, *, prefer_jpeg: bool, quality: int) -> tuple[bytes, str] | None:
    """Encode a PIL image for PPTX media."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _image_has_alpha,
    )
    buf = io.BytesIO()
    try:
        if prefer_jpeg and not _image_has_alpha(img):
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img.save(buf, format='JPEG', quality=max(1, min(quality, 100)), optimize=True)
            return buf.getvalue(), 'jpg'
        if img.mode == 'P':
            img = img.convert('RGBA' if _image_has_alpha(img) else 'RGB')
        elif img.mode not in {'1', 'L', 'LA', 'I', 'I;16', 'RGB', 'RGBA'}:
            img = img.convert('RGBA' if _image_has_alpha(img) else 'RGB')
        img.save(buf, format='PNG', optimize=True)
        return buf.getvalue(), 'png'
    except (OSError, ValueError):
        return None


def _optimize_image_for_pptx(
    ctx: ConvertContext,
    img_data: bytes,
    img_format: str,
    display_w: float,
    display_h: float,
    *,
    visible_source_fraction: tuple[float, float] | None = None,
) -> tuple[bytes, str]:
    """Optimize full raster image bytes for native PPTX embedding."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _encode_optimized_image,
        _fit_full_image_target,
        _has_exif_geometry_transform,
        _prepare_raster_for_geometry,
        _resize_for_target,
    )
    if not ctx.image_optimize:
        return img_data, img_format
    if img_format.lower() in {'svg', 'emf', 'wmf'}:
        return img_data, img_format

    try:
        from PIL import Image, UnidentifiedImageError  # type: ignore
    except ImportError:
        return img_data, img_format

    try:
        img = Image.open(io.BytesIO(img_data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return img_data, img_format

    # Multi-frame images (animated GIF / WebP / APNG): resize/re-encode
    # below keeps frame 0 only, flattening the animation in the exported
    # PPTX. Pass the original bytes through — animations are exempt from
    # optimization and the size cap (before this optimizer existed, the
    # native path embedded raster bytes verbatim and animations survived).
    if getattr(img, 'is_animated', False):
        return img_data, img_format

    geometry_normalized = _has_exif_geometry_transform(img)
    img = _prepare_raster_for_geometry(img)
    target_w, target_h = _fit_full_image_target(
        img.size[0],
        img.size[1],
        display_w,
        display_h,
        sizing=ctx.image_sizing,
        max_dimension=ctx.image_max_dimension,
        scale=ctx.image_scale,
        visible_source_fraction=visible_source_fraction,
    )

    original_size = img.size
    img = _resize_for_target(img, target_w, target_h)
    resized = img.size != original_size
    if (
        ctx.image_sizing == 'cap'
        and not geometry_normalized
        and not resized
    ):
        return img_data, img_format
    # Preserve source semantics: only an original JPEG stays lossy. PNG and
    # other static raster formats use lossless PNG after any resize.
    prefer_jpeg = img_format.lower() in {'jpg', 'jpeg'}
    encoded = _encode_optimized_image(img, prefer_jpeg=prefer_jpeg, quality=ctx.image_quality)
    if encoded is None:
        return img_data, img_format

    optimized_data, optimized_format = encoded
    if (
        not geometry_normalized
        and not resized
        and len(optimized_data) >= len(img_data)
    ):
        return img_data, img_format

    return optimized_data, optimized_format


def _compute_slice_src_rect(
    img_w: float, img_h: float,
    box_w: float, box_h: float,
    align: str,
) -> tuple[int, int, int, int] | None:
    """Compute DrawingML ``<a:srcRect>`` (l, t, r, b) for SVG slice mode.

    SVG ``preserveAspectRatio="<align> slice"`` means: scale the image so it
    fully covers the box (CSS object-fit: cover) and crop the overflow at the
    given alignment anchor. DrawingML ``srcRect`` expresses the same intent
    by specifying which sub-rectangle of the source image to display, in
    units of 1/1000 of a percent (0–100000).

    Returns ``None`` when no cropping is required (image and box already
    match) or when inputs are degenerate.
    """
    if img_w <= 0 or img_h <= 0 or box_w <= 0 or box_h <= 0:
        return None

    # Scale factor that makes the image cover the box (cover semantics).
    scale = max(box_w / img_w, box_h / img_h)
    visible_w = box_w / scale  # ≤ img_w
    visible_h = box_h / scale  # ≤ img_h

    if (
        math.isclose(visible_w, img_w, rel_tol=1e-9, abs_tol=1e-9)
        and math.isclose(visible_h, img_h, rel_tol=1e-9, abs_tol=1e-9)
    ):
        return None  # No crop needed

    x_anchor, y_anchor = PROJECT_IMAGE_ASPECT_RATIO_ANCHORS[align]
    crop_w_total = max(
        0,
        min(99999, int(round((1.0 - visible_w / img_w) * 100000))),
    )
    crop_h_total = max(
        0,
        min(99999, int(round((1.0 - visible_h / img_h) * 100000))),
    )
    l = max(0, min(crop_w_total, int(round(crop_w_total * x_anchor))))
    t = max(0, min(crop_h_total, int(round(crop_h_total * y_anchor))))
    r = crop_w_total - l
    b = crop_h_total - t
    if not any((l, t, r, b)):
        return None

    return (l, t, r, b)


def _resolve_image_src_rect_values(
    elem: ET.Element,
    img_data: bytes,
    box_w: float, box_h: float,
) -> tuple[int, int, int, int] | None:
    """Resolve DrawingML source-crop values for an SVG slice image.

    Slice mode is resolved into a srcRect so the original image is embedded
    intact and PowerPoint's crop tool / "Reset Picture" continue to work.
    Meet mode is handled separately by ``_resolve_image_meet_fit`` (which
    shrinks the picture frame to match image aspect ratio); none mode keeps
    the legacy stretch behaviour intentionally.
    """
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _compute_slice_src_rect,
        _read_image_size,
    )
    align, mode = parse_project_image_aspect_ratio(
        elem.get('preserveAspectRatio')
    )

    if align == 'none' or mode != 'slice':
        return None

    img_w, img_h = _read_image_size(img_data)
    if img_w is None or img_h is None:
        return None

    return _compute_slice_src_rect(
        float(img_w),
        float(img_h),
        box_w,
        box_h,
        align,
    )


def _src_rect_xml(rect: tuple[int, int, int, int] | None) -> str:
    """Serialize an optional DrawingML source crop."""
    if rect is None:
        return ''
    l, t, r, b = rect
    return f'<a:srcRect l="{l}" t="{t}" r="{r}" b="{b}"/>'


def _resolve_image_meet_fit(
    elem: ET.Element,
    img_data: bytes,
    box_w: float, box_h: float,
) -> tuple[float, float, float, float] | None:
    """For SVG ``preserveAspectRatio="<align> meet"``, compute the letterboxed
    sub-rectangle ``(dx, dy, fit_w, fit_h)`` inside the original box that
    matches the image's intrinsic aspect ratio.

    PowerPoint has no native ``meet`` semantic — ``<a:stretch><a:fillRect/>``
    fills the entire frame and would distort the image whenever the SVG
    container ratio differs from the source image ratio. The fix is to shrink
    the ``<p:pic>`` frame itself (off + ext) so the frame and image share an
    aspect ratio; the stretch then fills a correctly-shaped frame.

    Returns ``None`` when the adjustment is not applicable:
      - mode is ``slice`` (handled by srcRect path)
      - align is ``none`` (SVG spec says: stretch — do not adjust)
      - intrinsic image dimensions cannot be read
      - frame already matches image ratio (no-op)
    """
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _read_image_size,
    )
    align, mode = parse_project_image_aspect_ratio(
        elem.get('preserveAspectRatio')
    )

    if align == 'none' or mode == 'slice':
        return None

    img_w, img_h = _read_image_size(img_data)
    if img_w is None or img_h is None or img_w <= 0 or img_h <= 0:
        return None
    if box_w <= 0 or box_h <= 0:
        return None

    scale = min(box_w / img_w, box_h / img_h)
    fit_w = img_w * scale
    fit_h = img_h * scale

    if abs(fit_w - box_w) < 0.5 and abs(fit_h - box_h) < 0.5:
        return None  # already matches — no adjustment

    x_anchor, y_anchor = PROJECT_IMAGE_ASPECT_RATIO_ANCHORS[align]

    dx = (box_w - fit_w) * x_anchor
    dy = (box_h - fit_h) * y_anchor

    return (dx, dy, fit_w, fit_h)


def _build_image_blip_xml(r_id: str, opacity: float | None) -> str:
    """Build an image blip with native DrawingML transparency when requested."""
    if opacity is None:
        return f'<a:blip r:embed="{r_id}"/>'
    alpha = quantize_ooxml_alpha(opacity)
    return (
        f'<a:blip r:embed="{r_id}">'
        f'<a:alphaModFix amt="{alpha}"/>'
        '</a:blip>'
    )


def _register_image_media(
    ctx: ConvertContext,
    img_format: str,
    img_data: bytes,
    *,
    reuse_text_fill: bool = False,
) -> str:
    """Register image bytes and return a DrawingML relationship id."""
    cache_key: tuple[str, str] | None = None
    if reuse_text_fill:
        cache_key = (img_format, hashlib.sha256(img_data).hexdigest())
        if cache_key in ctx.text_image_fill_cache:
            return ctx.text_image_fill_cache[cache_key]

    img_idx = len(ctx.media_files) + 1
    img_filename = f's{ctx.slide_num}_img{img_idx}.{img_format}'
    ctx.media_files[img_filename] = img_data
    r_id = ctx.next_rel_id()
    ctx.rel_entries.append({
        'id': r_id,
        'type': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image',
        'target': f'../media/{img_filename}',
    })
    if cache_key is not None:
        ctx.text_image_fill_cache[cache_key] = r_id
    return r_id
