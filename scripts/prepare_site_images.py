"""Prepare owner-supplied site images. One-time conversion, not a site build."""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageCms

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "design" / "source-images"
SITE = ROOT / "site"
IMAGES = SITE / "images"

# Cursor stores the Gemini JPGs under this attachments folder.
ASSETS = Path(
    r"C:\Users\Zlatko.Lakisic\.cursor\projects\d-Projects-agent-coop\assets"
)

SOURCE_FILES = {
    "og-image.jpg": "c__Users_Zlatko.Lakisic_AppData_Roaming_Cursor_User_workspaceStorage_1dba2b4ae398b93bcdafbe8b5c36c2bb_images_og-image-532d4ca7-607d-421b-8f72-882c3485acc2.jpg",
    "step-1.jpg": "c__Users_Zlatko.Lakisic_AppData_Roaming_Cursor_User_workspaceStorage_1dba2b4ae398b93bcdafbe8b5c36c2bb_images_step-1-94351259-392c-43b2-bc30-716a223606b9.jpg",
    "step-2.jpg": "c__Users_Zlatko.Lakisic_AppData_Roaming_Cursor_User_workspaceStorage_1dba2b4ae398b93bcdafbe8b5c36c2bb_images_step-2-a46d54f3-5f0a-4c5c-95fc-39eaed01f2f1.jpg",
    "step-3.jpg": "c__Users_Zlatko.Lakisic_AppData_Roaming_Cursor_User_workspaceStorage_1dba2b4ae398b93bcdafbe8b5c36c2bb_images_step-3-3e363a39-ba65-4077-8663-40a6d95f34f6.jpg",
}

OG_W = 1200
OG_H = 630
STEP_SIZE = 640
STEP_MAX_BYTES = 25 * 1024
OG_MAX_BYTES = 300 * 1024
FLOOD_TOLERANCE = 12
CONTENT_TOLERANCE = 18
WHITE = (255, 255, 255)


def collect_sources() -> dict[str, Path]:
    """Copy Gemini JPGs into design/source-images/ if they are not there yet."""
    DESIGN.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    for dest_name, asset_name in SOURCE_FILES.items():
        dest = DESIGN / dest_name
        if not dest.exists():
            src = ASSETS / asset_name
            if not src.exists():
                raise FileNotFoundError("Missing source image: " + str(src))
            shutil.copy2(src, dest)
        found[dest_name] = dest
    return found


def to_srgb(im: Image.Image) -> Image.Image:
    """Return an RGB image in sRGB, dropping other profiles."""
    icc = im.info.get("icc_profile")
    if im.mode != "RGB":
        im = im.convert("RGB")
    if not icc:
        return im
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        dst = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(im, src, dst, outputMode="RGB")
    except Exception:
        return im.convert("RGB")


def content_bbox(im: Image.Image, tolerance: int = CONTENT_TOLERANCE) -> tuple[int, int, int, int]:
    """Bounding box of pixels that differ from the corner background color."""
    pixels = im.load()
    w, h = im.size
    seed = pixels[0, 0]
    min_x, min_y = w, h
    max_x, max_y = 0, 0
    found = False
    for y in range(h):
        for x in range(w):
            c = pixels[x, y]
            if (
                abs(c[0] - seed[0]) > tolerance
                or abs(c[1] - seed[1]) > tolerance
                or abs(c[2] - seed[2]) > tolerance
            ):
                found = True
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if not found:
        return (0, 0, w, h)
    return (min_x, min_y, max_x + 1, max_y + 1)


def _crop_contains(
    box: tuple[int, int, int, int],
    crop: tuple[int, int, int, int],
    margin: int = 2,
) -> bool:
    return (
        box[0] >= crop[0] + margin
        and box[1] >= crop[1] + margin
        and box[2] <= crop[2] - margin
        and box[3] <= crop[3] - margin
    )


def fit_og_image(im: Image.Image) -> tuple[Image.Image, str]:
    """Scale and center-crop to 1200x630 without cutting the planet ring."""
    w, h = im.size
    box = content_bbox(im)
    scale_a = OG_W / w
    height_a = max(1, round(h * scale_a))
    box_a = (
        round(box[0] * scale_a),
        round(box[1] * scale_a),
        round(box[2] * scale_a),
        round(box[3] * scale_a),
    )
    if height_a >= OG_H:
        top = (height_a - OG_H) // 2
        crop_a = (0, top, OG_W, top + OG_H)
        if _crop_contains(box_a, crop_a):
            scaled = im.resize((OG_W, height_a), Image.Resampling.LANCZOS)
            return scaled.crop(crop_a), "scale to 1200 wide, center crop height to 630"

    scale_b = OG_H / h
    width_b = max(1, round(w * scale_b))
    if width_b < OG_W:
        raise RuntimeError(
            "Source is too narrow to fill 1200x630 without padding. Size: %dx%d" % (w, h)
        )
    left = (width_b - OG_W) // 2
    crop_b = (left, 0, left + OG_W, OG_H)
    box_b = (
        round(box[0] * scale_b),
        round(box[1] * scale_b),
        round(box[2] * scale_b),
        round(box[3] * scale_b),
    )
    if not _crop_contains(box_b, crop_b):
        raise RuntimeError(
            "Both crops would cut the planet or ring. Content box %s, crop %s"
            % (box_b, crop_b)
        )
    scaled = im.resize((width_b, OG_H), Image.Resampling.LANCZOS)
    return scaled.crop(crop_b), "scale to 630 tall, center crop width to 1200"


def save_og_jpeg(im: Image.Image, dest: Path) -> int:
    """Write a progressive sRGB JPEG with no metadata."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(
        dest,
        format="JPEG",
        quality=85,
        optimize=True,
        progressive=True,
        subsampling=0,
        icc_profile=None,
        exif=b"",
    )
    size = dest.stat().st_size
    if size > OG_MAX_BYTES:
        raise RuntimeError("og-image.jpg is %d bytes, over 300 KB" % size)
    return size


def pad_square(im: Image.Image) -> Image.Image:
    """Center the image on a white square. Do not crop."""
    w, h = im.size
    if w == h:
        return im
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), WHITE)
    canvas.paste(im, ((side - w) // 2, (side - h) // 2))
    return canvas


def flood_background_white(im: Image.Image, tolerance: int = FLOOD_TOLERANCE) -> Image.Image:
    """Flood fill from all four corners. Do not replace near-white globally."""
    im = im.copy()
    pixels = im.load()
    w, h = im.size
    visited = bytearray(w * h)
    corners = ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))

    def index(x: int, y: int) -> int:
        return y * w + x

    for sx, sy in corners:
        seed = pixels[sx, sy]
        stack = [(sx, sy)]
        while stack:
            x, y = stack.pop()
            i = index(x, y)
            if visited[i]:
                continue
            c = pixels[x, y]
            if (
                abs(c[0] - seed[0]) > tolerance
                or abs(c[1] - seed[1]) > tolerance
                or abs(c[2] - seed[2]) > tolerance
            ):
                continue
            visited[i] = 1
            pixels[x, y] = WHITE
            if x > 0:
                stack.append((x - 1, y))
            if x + 1 < w:
                stack.append((x + 1, y))
            if y > 0:
                stack.append((x, y - 1))
            if y + 1 < h:
                stack.append((x, y + 1))
    return im


def save_step_webp(im: Image.Image, dest: Path) -> tuple[int, int]:
    """Save WebP, lowering quality if the file is over 25 KB."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    quality = 82
    while True:
        im.save(
            dest,
            format="WEBP",
            quality=quality,
            method=6,
            icc_profile=None,
            exif=b"",
        )
        size = dest.stat().st_size
        if size <= STEP_MAX_BYTES or quality <= 70:
            return size, quality
        quality -= 4


def corner_pixels(path: Path) -> list[tuple[int, int, int]]:
    """Read the four corner pixels of a saved image."""
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        px = rgb.load()
        w, h = rgb.size
        return [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]


def prepare_og(src: Path) -> dict[str, object]:
    with Image.open(src) as raw:
        im = to_srgb(raw)
        source_size = raw.size
        fitted, method = fit_og_image(im)
    dest = SITE / "og-image.jpg"
    size = save_og_jpeg(fitted, dest)
    return {
        "file": dest,
        "source_size": source_size,
        "pixels": fitted.size,
        "bytes": size,
        "quality": 85,
        "method": method,
        "corners": corner_pixels(dest),
    }


def prepare_step(src: Path, dest: Path) -> dict[str, object]:
    with Image.open(src) as raw:
        im = to_srgb(raw)
        source_size = raw.size
        im = pad_square(im)
        im = flood_background_white(im)
        im = im.resize((STEP_SIZE, STEP_SIZE), Image.Resampling.LANCZOS)
        im = flood_background_white(im)
    bytes_, quality = save_step_webp(im, dest)
    corners = corner_pixels(dest)
    if any(c != WHITE for c in corners):
        raise RuntimeError(
            "%s corner pixels are not white: %s" % (dest.name, corners)
        )
    return {
        "file": dest,
        "source_size": source_size,
        "pixels": (STEP_SIZE, STEP_SIZE),
        "bytes": bytes_,
        "quality": quality,
        "corners": corners,
    }


def report_line(name: str, info: dict[str, object]) -> str:
    px = info["pixels"]
    return (
        "%s: %dx%d, %d bytes, quality %s, corners %s"
        % (name, px[0], px[1], info["bytes"], info["quality"], info["corners"])
    )


def main() -> int:
    sources = collect_sources()
    SITE.mkdir(parents=True, exist_ok=True)
    IMAGES.mkdir(parents=True, exist_ok=True)

    og = prepare_og(sources["og-image.jpg"])
    steps = [
        prepare_step(sources["step-1.jpg"], IMAGES / "step-1.webp"),
        prepare_step(sources["step-2.jpg"], IMAGES / "step-2.webp"),
        prepare_step(sources["step-3.jpg"], IMAGES / "step-3.webp"),
    ]

    print("og-image.jpg method: " + str(og["method"]))
    print("source og-image.jpg: %dx%d" % og["source_size"])
    print(report_line("og-image.jpg", og))
    print(report_line("step-1.webp", steps[0]))
    print(report_line("step-2.webp", steps[1]))
    print(report_line("step-3.webp", steps[2]))
    for step in steps:
        if step["bytes"] > STEP_MAX_BYTES:
            print(
                "WARNING: %s is %d bytes at quality 70 (over 25 KB)"
                % (Path(str(step["file"])).name, step["bytes"])
            )
    extra_jpg = [p for p in SITE.rglob("*.jpg") if p.name != "og-image.jpg"]
    extra_jpg += list(SITE.rglob("*.jpeg"))
    extra_jpg += list(SITE.rglob("*.png"))
    if extra_jpg:
        raise RuntimeError("Unexpected image files in site/: " + str(extra_jpg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
