"""Privacy of the screenshots: rendered from made-up data, without metadata."""

from __future__ import annotations

from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parent.parent
SKIPPED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
# Chunks that hold the picture itself; everything else (text, time, EXIF,
# colour profile) can carry where and when an image was made
IMAGE_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
README_IMAGE = re.compile(
    r"https://raw\.githubusercontent\.com/n-schilling/ha-network-plus/main/(docs/images/[\w.-]+)"
)


def _png_problems(data: bytes) -> list[str]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return ["no PNG"]
    problems = []
    i = 8
    while i < len(data):
        length = struct.unpack(">I", data[i : i + 4])[0]
        kind = data[i + 4 : i + 8]
        if kind not in IMAGE_CHUNKS:
            problems.append(f"chunk {kind!r}")
        i += 12 + length
    return problems


def test_images_carry_no_metadata() -> None:
    """Every image in the repository is a PNG without metadata."""
    images = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        and not SKIPPED_DIRS & set(path.relative_to(ROOT).parts)
    ]
    assert any(p.parent.name == "images" for p in images), "the screenshots are checked"
    for path in images:
        assert path.suffix == ".png", f"{path}: only PNG, so it can be checked"
        problems = _png_problems(path.read_bytes())
        assert not problems, f"{path}: {problems}"


def test_metadata_check_catches_metadata() -> None:
    """The check above finds what it looks for."""
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 0) + b"tEXt" + b"\0" * 4
    assert _png_problems(png) == ["chunk b'tEXt'"]
    assert _png_problems(b"nope") == ["no PNG"]


def test_readme_shows_the_images() -> None:
    """The README links every screenshot, from main so HACS shows them too."""
    linked = set(README_IMAGE.findall((ROOT / "README.md").read_text(encoding="utf-8")))
    on_disk = {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs" / "images").glob("*.png")}
    assert linked == on_disk
