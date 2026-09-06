"""Fetching the mushaf source archive.

This is the first code a new contributor runs, and it downloads 91 MB from a
third party, so its failure modes matter more than most. The real download is
exercised against local file:// URLs here — the tests never touch the network.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import source_archive  # noqa: E402


@pytest.fixture(autouse=True)
def small_archives_are_fine(monkeypatch):
    """The real threshold rejects anything under 10 MB; these fixtures are tiny."""
    monkeypatch.setattr(source_archive, "MIN_PLAUSIBLE_BYTES", 1)


def make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mushaf-content.json", '{"ok": true}')
        zf.writestr("001.svg", "<svg/>")
    return path


def as_url(path: Path) -> str:
    return path.resolve().as_uri()


class TestEnsure:
    def test_an_archive_already_there_is_left_alone(self, tmp_path):
        target = make_zip(tmp_path / "hafs.zip")
        before = target.stat().st_mtime_ns

        assert source_archive.ensure(target, url="http://example.invalid/x.zip") == target
        assert target.stat().st_mtime_ns == before

    def test_a_missing_archive_is_downloaded(self, tmp_path):
        remote = make_zip(tmp_path / "remote.zip")
        target = tmp_path / "assets" / "hafs.zip"

        source_archive.ensure(target, url=as_url(remote))

        assert target.exists()
        assert zipfile.is_zipfile(target)
        # The directory is created on the way, since assets/ may not exist yet.
        assert target.parent.is_dir()

    def test_no_download_refuses_instead_of_fetching(self, tmp_path):
        remote = make_zip(tmp_path / "remote.zip")
        target = tmp_path / "hafs.zip"

        with pytest.raises(SystemExit) as exit_info:
            source_archive.ensure(target, url=as_url(remote), allow_download=False)

        assert not target.exists()
        # The message has to name the URL, so someone who declined the download
        # knows what to fetch by hand.
        assert as_url(remote) in str(exit_info.value)
        assert "--no-download" in str(exit_info.value)


class TestDownloadFailures:
    def test_an_error_page_is_not_accepted_as_an_archive(self, tmp_path):
        # A host that answers 200 with an HTML error page must not leave
        # something behind that later fails deep inside zipfile.
        not_a_zip = tmp_path / "error.html"
        not_a_zip.write_text("<html>403 Forbidden</html>")
        target = tmp_path / "hafs.zip"

        with pytest.raises(SystemExit):
            source_archive.download(target, as_url(not_a_zip))

        assert not target.exists()
        assert not (tmp_path / "hafs.zip.part").exists()

    def test_a_truncated_archive_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(source_archive, "MIN_PLAUSIBLE_BYTES", 10 * 1024 * 1024)
        small = make_zip(tmp_path / "small.zip")

        with pytest.raises(SystemExit) as exit_info:
            source_archive.download(tmp_path / "hafs.zip", as_url(small))

        assert "not a usable archive" in str(exit_info.value)

    def test_an_unreachable_url_says_where_to_get_it(self, tmp_path):
        target = tmp_path / "hafs.zip"

        with pytest.raises(SystemExit) as exit_info:
            source_archive.download(target, as_url(tmp_path / "does-not-exist.zip"))

        assert "Download it yourself" in str(exit_info.value)
        assert not target.exists()

    def test_a_failed_download_leaves_no_partial_file(self, tmp_path):
        # An interrupted download must not leave a half archive that the next
        # run would mistake for a complete one.
        with pytest.raises(SystemExit):
            source_archive.download(tmp_path / "hafs.zip", as_url(tmp_path / "missing.zip"))

        assert list(tmp_path.glob("*.part")) == []

    def test_an_existing_partial_is_overwritten_not_appended(self, tmp_path):
        stale = tmp_path / "hafs.zip.part"
        stale.write_bytes(b"junk from an interrupted run")
        remote = make_zip(tmp_path / "remote.zip")

        source_archive.download(tmp_path / "hafs.zip", as_url(remote))

        assert zipfile.is_zipfile(tmp_path / "hafs.zip")
        assert not stale.exists()


def test_the_source_url_and_credit_are_the_documented_ones():
    # Both appear in the README; if one moves, the other has to follow.
    assert source_archive.SOURCE_URL == "https://quranpedia.net/api-quran-svg/hafs.zip"
    assert "quranpedia.net" in source_archive.CREDIT

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert source_archive.SOURCE_URL in readme
    assert "api.quranpedia.net" in readme
