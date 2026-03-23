"""Tests for the edit_file tool — the most critical tool in Shipyard."""

import os
import tempfile

import pytest

from shipyard.tools.base import FileReadTracker
from shipyard.tools.edit_file import edit_file
from shipyard.tools.read_file import read_file


@pytest.fixture
def tracker():
    return FileReadTracker()


@pytest.fixture
def small_file(tmp_path):
    """A small file with 10 lines."""
    f = tmp_path / "small.py"
    f.write_text(
        'def hello():\n    print("Hello, world!")\n\ndef goodbye():\n    print("Goodbye!")\n\nif __name__ == "__main__":\n    hello()\n    goodbye()\n',
        encoding="utf-8",
    )
    return str(f)


@pytest.fixture
def large_file(tmp_path):
    """A file with 300+ lines to test behavior above 200 lines."""
    lines = []
    for i in range(300):
        lines.append(f"def func_{i}():\n    return {i}\n")
    f = tmp_path / "large.py"
    f.write_text("\n".join(lines), encoding="utf-8")
    return str(f)


@pytest.fixture
def duplicate_file(tmp_path):
    """A file with duplicate patterns."""
    f = tmp_path / "dup.py"
    f.write_text(
        'x = 1\nprint(x)\ny = 2\nprint(y)\nz = 3\nprint(z)\n',
        encoding="utf-8",
    )
    return str(f)


class TestEditFile:
    def test_successful_edit(self, tracker, small_file):
        """Basic successful edit."""
        read_file(small_file, tracker=tracker)
        result = edit_file(
            small_file,
            old_string='print("Hello, world!")',
            new_string='print("Hi there!")',
            tracker=tracker,
        )
        assert not result.is_error
        assert "Successfully edited" in result.output

        # Verify the file was actually changed
        content = open(small_file, encoding="utf-8").read()
        assert 'print("Hi there!")' in content
        assert 'print("Hello, world!")' not in content

    def test_not_read_first(self, tracker, small_file):
        """Edit without reading first should fail."""
        result = edit_file(
            small_file,
            old_string='print("Hello, world!")',
            new_string='print("Hi")',
            tracker=tracker,
        )
        assert result.is_error
        assert "not been read" in result.output

    def test_no_match(self, tracker, small_file):
        """Editing with a string that doesn't exist should fail."""
        read_file(small_file, tracker=tracker)
        result = edit_file(
            small_file,
            old_string="this_does_not_exist()",
            new_string="replacement()",
            tracker=tracker,
        )
        assert result.is_error
        assert "not found" in result.output

    def test_multiple_matches(self, tracker, duplicate_file):
        """Editing an ambiguous string should fail with match count."""
        read_file(duplicate_file, tracker=tracker)
        result = edit_file(
            duplicate_file,
            old_string="print(",
            new_string="log(",
            tracker=tracker,
        )
        assert result.is_error
        assert "3 matches" in result.output

    def test_disambiguated_edit(self, tracker, duplicate_file):
        """Adding more context should disambiguate."""
        read_file(duplicate_file, tracker=tracker)
        result = edit_file(
            duplicate_file,
            old_string="print(x)",
            new_string="log(x)",
            tracker=tracker,
        )
        assert not result.is_error
        content = open(duplicate_file, encoding="utf-8").read()
        assert "log(x)" in content
        assert content.count("print(") == 2  # y and z still have print

    def test_large_file_edit(self, tracker, large_file):
        """Edit works correctly on files >200 lines."""
        read_file(large_file, tracker=tracker)
        result = edit_file(
            large_file,
            old_string="def func_150():\n    return 150",
            new_string="def func_150():\n    return 999  # modified",
            tracker=tracker,
        )
        assert not result.is_error
        content = open(large_file, encoding="utf-8").read()
        assert "return 999  # modified" in content

    def test_empty_old_string(self, tracker, small_file):
        """Empty old_string should fail."""
        read_file(small_file, tracker=tracker)
        result = edit_file(small_file, old_string="", new_string="new", tracker=tracker)
        assert result.is_error
        assert "must not be empty" in result.output

    def test_identical_strings(self, tracker, small_file):
        """old_string == new_string should fail."""
        read_file(small_file, tracker=tracker)
        result = edit_file(small_file, old_string="hello", new_string="hello", tracker=tracker)
        assert result.is_error
        assert "identical" in result.output

    def test_file_not_found(self, tracker):
        """Editing a nonexistent file should fail."""
        result = edit_file("/nonexistent/file.py", old_string="a", new_string="b", tracker=tracker)
        assert result.is_error
        assert "not found" in result.output

    def test_whitespace_hint(self, tracker, small_file):
        """When exact match fails but whitespace-normalized match exists, show a hint."""
        read_file(small_file, tracker=tracker)
        # Use tabs instead of spaces — won't be a substring match
        result = edit_file(
            small_file,
            old_string='\tprint("Hello, world!")',
            new_string='print("Hi")',
            tracker=tracker,
        )
        assert result.is_error
        assert "not found" in result.output

    def test_sequential_edits(self, tracker, small_file):
        """Multiple edits to the same file should work."""
        read_file(small_file, tracker=tracker)

        result1 = edit_file(
            small_file,
            old_string='print("Hello, world!")',
            new_string='print("Hi!")',
            tracker=tracker,
        )
        assert not result1.is_error

        result2 = edit_file(
            small_file,
            old_string='print("Goodbye!")',
            new_string='print("Bye!")',
            tracker=tracker,
        )
        assert not result2.is_error

        content = open(small_file, encoding="utf-8").read()
        assert 'print("Hi!")' in content
        assert 'print("Bye!")' in content
