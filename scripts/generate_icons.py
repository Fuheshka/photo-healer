#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post-processing and icon generation for Photo Healer.

Masks the raw AI-generated master image into Apple HIG continuous squircle (superellipse) geometry,
applies soft photorealistic contact drop shadow, exports transparent master PNG (1024x1024),
and packages multi-resolution Windows ICO (16x16 up to 256x256).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter


def create_apple_squircle_mask(
    width: int = 1024,
    height: int = 1024,
    radius_a: float = 407.0,
    radius_b: float = 407.0,
    exponent_n: float = 4.65,
    scale: int = 4,
) -> Image.Image:
    """Generate 4x supersampled continuous Apple squircle (superellipse) mask."""
    sw, sh = width * scale, height * scale
    scx, scy = (width / 2.0) * scale, (height / 2.0) * scale
    sa, sb = radius_a * scale, radius_b * scale

    mask_4x = Image.new("L", (sw, sh), 0)
    draw = ImageDraw.Draw(mask_4x)

    half_h = sh // 2
    for sy in range(half_h):
        y_norm = (sy + 0.5) / sb
        if y_norm < 1.0:
            rem = 1.0 - y_norm**exponent_n
            if rem > 0:
                x_norm = rem ** (1.0 / exponent_n)
                span = int(x_norm * sa)
            else:
                span = 0
        else:
            span = 0

        if span > 0:
            y_top = int(scy - sy - 1)
            draw.line([(int(scx - span), y_top), (int(scx + span), y_top)], fill=255)
            y_bot = int(scy + sy)
            draw.line([(int(scx - span), y_bot), (int(scx + span), y_bot)], fill=255)

    return mask_4x.resize((width, height), Image.Resampling.LANCZOS)


def mask_apple_squircle(
    src_path: Path | str,
    out_png_path: Path | str,
    radius: float = 407.0,
    exponent_n: float = 4.65,
    shadow_blur: int = 20,
    shadow_offset_y: int = 10,
    shadow_opacity: float = 0.55,
) -> Image.Image:
    """Mask source image into Apple squircle with contact drop shadow and 4x supersampling."""
    im = Image.open(src_path).convert("RGBA")
    w, h = im.size

    # 1. Continuous Apple squircle mask (4x supersampling via superellipse)
    mask = create_apple_squircle_mask(
        width=w,
        height=h,
        radius_a=radius,
        radius_b=radius,
        exponent_n=exponent_n,
        scale=4,
    )

    # 2. Photorealistic contact drop shadow
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow_alpha = int(255 * shadow_opacity)
    black_plate = Image.new("RGBA", (w, h), (0, 0, 0, shadow_alpha))
    shadow_silhouette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow_silhouette.paste(black_plate, (0, shadow_offset_y), mask)
    shadow = shadow_silhouette.filter(ImageFilter.GaussianBlur(shadow_blur))

    # 3. Cut original image cleanly inside the dark plate rim (zero white halo)
    cut = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cut.paste(im, (0, 0), mask)

    # 4. Composite: shadow behind, cut on top
    comp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    comp.paste(shadow, (0, 0), shadow)
    comp.paste(cut, (0, 0), cut)

    out_png = Path(out_png_path)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    comp.save(out_png, "PNG")
    return comp


def export_windows_ico(master_img: Image.Image, out_ico_path: Path | str) -> None:
    """Export master image to multi-resolution Windows ICO file with 7 standard layers."""
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    out_ico = Path(out_ico_path)
    out_ico.parent.mkdir(parents=True, exist_ok=True)
    master_img.save(out_ico, format="ICO", sizes=sizes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Photo Healer master PNG and ICO.")
    parser.add_argument(
        "--src",
        type=Path,
        required=True,
        help="Path to raw generated 1024x1024 source image",
    )
    parser.add_argument(
        "--out-png",
        type=Path,
        default=Path("assets/icon.png"),
        help="Destination path for masked master PNG (default: assets/icon.png)",
    )
    parser.add_argument(
        "--out-ico",
        type=Path,
        default=Path("assets/icon.ico"),
        help="Destination path for Windows ICO (default: assets/icon.ico)",
    )
    args = parser.parse_args()

    print(f"Masking Apple squircle from {args.src} -> {args.out_png}...")
    comp = mask_apple_squircle(args.src, args.out_png)
    print(f"Exporting Windows multi-resolution ICO -> {args.out_ico}...")
    export_windows_ico(comp, args.out_ico)
    print("Icon generation complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
