#!/usr/bin/env python3
"""يحوّل صفحات المصحف من hafs.zip (SVG) إلى صور PNG جاهزة للإرسال في تيليجرام.

    uv run scripts/build_pages.py
    uv run scripts/build_pages.py --width 1400 --force

لماذا التوحيد؟ الصفحتان 1 و 2 مقاسهما في المصدر 235×235 (مربّع)، وباقي الصفحات
345×550. بلا توحيد يبدو ألبوم الصفحتين الأولى مشوّهًا، لذلك تُوسَّط كل صفحة على
لوحة بيضاء بنسبة 345:550 الثابتة.

الصور رمادية بالكامل في المصدر (fill=#231f20 فقط)، لذلك التكميم إلى 32 درجة
رمادية بلا خسارة مرئية ويقلّص الحجم من ~737KB إلى ~180KB للصفحة.
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
PAGE_RATIO = 550 / 345  # نسبة صفحة المصحف القياسية في هذا المصدر
GRAY_LEVELS = 32


def check_tools() -> str:
    """يتحقّق من وجود rsvg-convert وأداة ImageMagick، ويعيد اسم أمر ImageMagick."""
    if not shutil.which("rsvg-convert"):
        sys.exit(
            "rsvg-convert غير مثبّت.\n"
            "  Arch:   sudo pacman -S librsvg\n"
            "  Debian: sudo apt install librsvg2-bin"
        )
    for candidate in ("magick", "convert"):
        if shutil.which(candidate):
            return candidate
    sys.exit(
        "ImageMagick غير مثبّت.\n"
        "  Arch:   sudo pacman -S imagemagick\n"
        "  Debian: sudo apt install imagemagick"
    )


def render_page(svg: bytes, out_path: Path, width: int, height: int, magick: str) -> None:
    """SVG → PNG بعرض ثابت، ثم توسيط على لوحة بيضاء وتكميم إلى تدرّج رمادي."""
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
    ap = argparse.ArgumentParser(description="بناء صور صفحات المصحف")
    ap.add_argument("--zip", type=Path, default=DEFAULT_ZIP, help="أرشيف المصدر")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="مجلد الإخراج")
    ap.add_argument("--width", type=int, default=1240, help="عرض الصورة بالبكسل")
    ap.add_argument("--jobs", type=int, default=0, help="عدد العمليات المتوازية (0=تلقائي)")
    ap.add_argument("--force", action="store_true", help="أعد بناء الصفحات الموجودة")
    args = ap.parse_args()

    if not args.zip.exists():
        sys.exit(f"الأرشيف غير موجود: {args.zip}")

    magick = check_tools()
    height = round(args.width * PAGE_RATIO)
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"المصدر : {args.zip}")
    print(f"الإخراج: {args.out}")
    print(f"المقاس : {args.width}×{height}  ({GRAY_LEVELS} درجة رمادية)\n")

    with zipfile.ZipFile(args.zip) as zf:
        names = set(zf.namelist())
        missing = [p for p in range(1, TOTAL_PAGES + 1) if f"{p:03d}.svg" not in names]
        if missing:
            sys.exit(f"صفحات ناقصة في الأرشيف: {missing[:10]}… ({len(missing)} صفحة)")

        todo = [
            p
            for p in range(1, TOTAL_PAGES + 1)
            if args.force or not (args.out / f"{p:03d}.png").exists()
        ]
        if not todo:
            print(f"كل الصفحات موجودة ({TOTAL_PAGES}). استخدم --force لإعادة البناء.")
            return 0

        # القراءة من ZipFile ليست آمنة بين الخيوط، لذلك تُقرأ البايتات مسبقًا دفعةً دفعة
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
                        print(f"\r  {done}/{len(todo)} صفحة…", end="", flush=True)
                    elif done % 100 == 0 or done == len(todo):
                        print(f"  {done}/{len(todo)} صفحة…", flush=True)

    if tty:
        print()
    if failures:
        for page, err in failures[:5]:
            print(f"  فشلت الصفحة {page}: {err}", file=sys.stderr)
        sys.exit(f"\nفشل بناء {len(failures)} صفحة.")

    files = sorted(args.out.glob("*.png"))
    total = sum(f.stat().st_size for f in files)
    print(
        f"\nتمّ: {len(files)} صفحة · {total / 1024 / 1024:.0f} MB "
        f"· متوسط {total / len(files) / 1024:.0f} KB للصفحة"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
