import os

import pytest

import utils


class TestCleanText:
    def test_collapses_whitespace_and_newlines(self):
        assert utils.clean_text("  a\n b\t c ") == "a b c"

    def test_replaces_non_breaking_space(self):
        assert utils.clean_text("a\xa0b") == "a b"

    def test_none_and_empty_become_not_specified(self):
        assert utils.clean_text(None) == utils.NOT_SPECIFIED
        assert utils.clean_text("") == utils.NOT_SPECIFIED
        assert utils.clean_text("   ") == utils.NOT_SPECIFIED

    def test_coerces_non_strings(self):
        assert utils.clean_text(42) == "42"


class TestParseFloat:
    def test_parses_numeric_strings_and_numbers(self):
        assert utils.parse_float("18.5") == 18.5
        assert utils.parse_float(7) == 7.0

    def test_returns_none_on_bad_input(self):
        assert utils.parse_float("n/a") is None
        assert utils.parse_float(None) is None


class TestSlugifyLocation:
    def test_slugifies_city_state(self):
        assert utils.slugify_location("Cupertino, CA") == "cupertino_ca"

    def test_strips_leading_trailing_separators(self):
        assert utils.slugify_location("  San Jose!  ") == "san_jose"

    def test_empty_falls_back_to_location(self):
        assert utils.slugify_location("!!!") == "location"


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "out.txt"
        utils.atomic_write_text(target, "hello\n")
        assert target.read_text(encoding="utf-8") == "hello\n"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "nested" / "deep" / "out.txt"
        utils.atomic_write_text(target, "data")
        assert target.read_text(encoding="utf-8") == "data"

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("old", encoding="utf-8")
        utils.atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "new"

    def test_leaves_no_temp_files_on_success(self, tmp_path):
        utils.atomic_write_text(tmp_path / "out.txt", "ok")
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "out.txt"]
        assert leftovers == []

    def test_failure_preserves_original_and_cleans_temp(self, tmp_path, monkeypatch):
        target = tmp_path / "out.txt"
        target.write_text("original", encoding="utf-8")

        def boom(*_args, **_kwargs):
            raise OSError("replace failed")

        monkeypatch.setattr(utils.os, "replace", boom)
        with pytest.raises(OSError):
            utils.atomic_write_text(target, "should not land")

        # Original file is untouched and no temp file was left behind.
        assert target.read_text(encoding="utf-8") == "original"
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "out.txt"]
        assert leftovers == []


class TestAtomicWriteBytes:
    def test_writes_binary(self, tmp_path):
        target = tmp_path / "r.pdf"
        utils.atomic_write_bytes(target, b"\x25PDF\x00\xff")
        assert target.read_bytes() == b"\x25PDF\x00\xff"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b.bin"
        utils.atomic_write_bytes(target, b"x")
        assert target.read_bytes() == b"x"

    def test_failure_preserves_original_and_cleans_temp(self, tmp_path, monkeypatch):
        target = tmp_path / "r.pdf"
        target.write_bytes(b"original")

        def boom(*_args, **_kwargs):
            raise OSError("replace failed")

        monkeypatch.setattr(utils.os, "replace", boom)
        with pytest.raises(OSError):
            utils.atomic_write_bytes(target, b"new")

        assert target.read_bytes() == b"original"
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "r.pdf"]
        assert leftovers == []


class TestSafeResumeFilename:
    EXTS = {".pdf", ".doc", ".docx", ".txt", ".rtf"}

    def test_strips_directory_components(self):
        assert utils.safe_resume_filename("../../etc/passwd.pdf", self.EXTS) == "passwd.pdf"

    def test_sanitizes_spaces_and_lowercases_extension(self):
        assert utils.safe_resume_filename("My Resume!.PDF", self.EXTS) == "My_Resume.pdf"

    def test_rejects_disallowed_extension(self):
        assert utils.safe_resume_filename("evil.exe", self.EXTS) is None

    def test_rejects_no_extension(self):
        assert utils.safe_resume_filename("resume", self.EXTS) is None

    def test_rejects_empty_or_none(self):
        assert utils.safe_resume_filename("", self.EXTS) is None
        assert utils.safe_resume_filename(None, self.EXTS) is None

    def test_falls_back_when_stem_is_all_unsafe(self):
        assert utils.safe_resume_filename("!!!.pdf", self.EXTS) == "resume.pdf"

    def test_strips_null_bytes(self):
        assert utils.safe_resume_filename("re\x00sume.pdf", self.EXTS) == "resume.pdf"
