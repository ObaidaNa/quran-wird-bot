"""Fetching the mushaf source archive, once.

`hafs.zip` is 91 MB of SVG pages and Quran text from Quranpedia
(https://api.quranpedia.net). It is not kept in the repository — it is large,
and it is their work rather than this project's — so the build scripts fetch it
on first use and leave it in `assets/` for every run after that.

Only the standard library is used here: a build-time download is not worth a
runtime dependency, and this runs at most once per checkout.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SOURCE_URL = "https://quranpedia.net/api-quran-svg/hafs.zip"
CREDIT = "Quranpedia — https://api.quranpedia.net"

# The real archive is ~91 MB; anything far smaller is an error page, not a zip.
MIN_PLAUSIBLE_BYTES = 10 * 1024 * 1024

# The host answers Python-urllib's default User-Agent with 403, so it has to be
# asked for the way a browser would ask.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def _progress(done: int, total: int) -> None:
    mb = done / 1024 / 1024
    if total > 0:
        percent = min(100, done * 100 // total)
        bar = "#" * (percent // 4)
        sys.stderr.write(f"\r  {bar:<25} {percent:3d}%  {mb:6.1f} MB")
    else:
        sys.stderr.write(f"\r  {mb:6.1f} MB")
    sys.stderr.flush()


def download(target: Path, url: str = SOURCE_URL) -> Path:
    """Download the archive to `target`, atomically. Returns the path."""
    target.parent.mkdir(parents=True, exist_ok=True)
    # Downloaded beside the target and moved into place only once complete, so
    # an interrupted download never leaves a half archive that looks valid.
    partial = target.with_suffix(target.suffix + ".part")

    print(f"source archive not found; downloading from {url}")
    print(f"  courtesy of {CREDIT}")

    try:
        request = urllib.request.Request(url, headers=HEADERS)  # noqa: S310 - fixed https URL
        with urllib.request.urlopen(request) as response:  # noqa: S310
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with partial.open("wb") as out:
                while chunk := response.read(1024 * 256):
                    out.write(chunk)
                    done += len(chunk)
                    _progress(done, total)
        sys.stderr.write("\n")
    except (urllib.error.URLError, OSError) as exc:
        partial.unlink(missing_ok=True)
        sys.exit(
            f"could not download the source archive: {exc}\n"
            f"Download it yourself from {url} and save it as {target}"
        )

    size = partial.stat().st_size
    if size < MIN_PLAUSIBLE_BYTES or not zipfile.is_zipfile(partial):
        partial.unlink(missing_ok=True)
        sys.exit(
            f"what was downloaded is not a usable archive ({size} bytes).\n"
            f"Check {url} in a browser, or save the file yourself as {target}"
        )

    partial.replace(target)
    print(f"  saved {target} ({size / 1024 / 1024:.0f} MB)")
    return target


def ensure(target: Path, *, url: str = SOURCE_URL, allow_download: bool = True) -> Path:
    """Return the archive path, downloading it if it is not there yet."""
    if target.exists():
        return target
    if not allow_download:
        sys.exit(f"archive not found: {target}\nFetch it from {url}, or drop --no-download")
    return download(target, url)
