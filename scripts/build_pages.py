#!/usr/bin/env python3
"""Render the mushaf pages from hafs.zip (SVG) into PNGs ready to send on Telegram.

    uv run scripts/build_pages.py
    uv run scripts/build_pages.py --width 1400 --force

Why normalize? Pages 1 and 2 are 235x235 in the source while pages 3-604 are
345x550. Left alone, the first album renders inconsistently, so every page is
centered on a white canvas with the fixed 345:550 ratio.

The source art is monochrome (fill=#231f20 only), so quantizing to 32 gray levels
is visually free and cuts each page from ~737KB to ~180KB.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = PROJECT_ROOT / "assets" / "hafs.zip"
DEFAULT_OUT = PROJECT_ROOT / "assets" / "pages"

TOTAL_PAGES = 604
PAGE_RATIO = 550 / 345  # the standard mushaf page ratio in this source
GRAY_LEVELS = 32


def check_tools() -> str:
    """Check that rsvg-convert and ImageMagick exist; return the ImageMagick command."""
    if not shutil.which("rsvg-convert"):
        sys.exit(
            "rsvg-convert is not installed.\n"
            "  Arch:   sudo pacman -S librsvg\n"
            "  Debian: sudo apt install librsvg2-bin"
        )
    for candidate in ("magick", "convert"):
        if shutil.which(candidate):
            return candidate
    sys.exit(
        "ImageMagick is not installed.\n"
        "  Arch:   sudo pacman -S imagemagick\n"
        "  Debian: sudo apt install imagemagick"
    )


def render_page(svg: bytes, out_path: Path, width: int, height: int, magick: str) -> None:
    """SVG to PNG at a fixed width, centered on a white canvas, quantized to gray."""
    png = subprocess.run(
        ["rsvg-convert", "-w", str(width), "-b", "white", "-f", "png"],
        input=svg,
        capture_output=True,
        check=True,
    ).stdout

    subprocess.run(
        [
            magick,
            "png:-",
            "-background",
            "white",
            "-gravity",
            "center",
            "-extent",
            f"{width}x{height}",
            "-colorspace",
            "Gray",
            "-colors",
            str(GRAY_LEVELS),
            f"PNG8:{out_path}",
        ],
        input=png,
        capture_output=True,
        check=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the mushaf page images")
    ap.add_argument("--zip", type=Path, default=DEFAULT_ZIP, help="source archive")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    ap.add_argument("--width", type=int, default=1240, help="image width in pixels")
    ap.add_argument("--jobs", type=int, default=0, help="parallel workers (0=auto)")
    ap.add_argument("--force", action="store_true", help="rebuild pages that already exist")
    args = ap.parse_args()

    if not args.zip.exists():
        sys.exit(f"archive not found: {args.zip}")

    magick = check_tools()
    height = round(args.width * PAGE_RATIO)
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"source : {args.zip}")
    print(f"output : {args.out}")
    print(f"size   : {args.width}x{height}  ({GRAY_LEVELS} gray levels)\n")

    with zipfile.ZipFile(args.zip) as zf:
        names = set(zf.namelist())
        missing = [p for p in range(1, TOTAL_PAGES + 1) if f"{p:03d}.svg" not in names]
        if missing:
            sys.exit(f"pages missing from the archive: {missing[:10]}... ({len(missing)} pages)")

        todo = [
            p
            for p in range(1, TOTAL_PAGES + 1)
            if args.force or not (args.out / f"{p:03d}.png").exists()
        ]
        if not todo:
            print(f"all {TOTAL_PAGES} pages already built. Use --force to rebuild.")
            return 0

        # ZipFile reads are not thread-safe, so read each chunk's bytes up front
        done = 0
        tty = sys.stdout.isatty()
        failures: list[tuple[int, str]] = []
        workers = args.jobs or min(8, (len(todo) + 7) // 8 or 1)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for chunk_start in range(0, len(todo), 64):
                chunk = todo[chunk_start : chunk_start + 64]
                payload = [(p, zf.read(f"{p:03d}.svg")) for p in chunk]

                futures = {
                    pool.submit(
                        render_page,
                        svg,
                        args.out / f"{p:03d}.png",
                        args.width,
                        height,
                        magick,
                    ): p
                    for p, svg in payload
                }
                for fut, page in futures.items():
                    try:
                        fut.result()
                    except subprocess.CalledProcessError as e:
                        failures.append((page, e.stderr.decode("utf-8", "replace")[:200]))
                    done += 1
                    if tty:
                        print(f"\r  {done}/{len(todo)} pages...", end="", flush=True)
                    elif done % 100 == 0 or done == len(todo):
                        print(f"  {done}/{len(todo)} pages...", flush=True)

    if tty:
        print()
    if failures:
        for page, err in failures[:5]:
            print(f"  page {page} failed: {err}", file=sys.stderr)
        sys.exit(f"\n{len(failures)} pages failed to build.")

    files = sorted(args.out.glob("*.png"))
    total = sum(f.stat().st_size for f in files)
    print(
        f"\ndone: {len(files)} pages, {total / 1024 / 1024:.0f} MB total, "
        f"{total / len(files) / 1024:.0f} KB average"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
