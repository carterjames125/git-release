from pathlib import Path

import pytest

from gitlab_release.artifacts import discover, normalize_version
from gitlab_release.errors import ArtifactError


def test_discover_returns_sorted_matches(tmp_path: Path) -> None:
    (tmp_path / "b.rpm").write_bytes(b"b")
    (tmp_path / "a.rpm").write_bytes(b"a")
    (tmp_path / "ignored.txt").write_bytes(b"x")

    result = discover(tmp_path, "*.rpm")

    assert [p.name for p in result] == ["a.rpm", "b.rpm"]


def test_discover_raises_when_no_matches(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError):
        discover(tmp_path, "*.rpm")


def test_discover_raises_when_match_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()

    with pytest.raises(ArtifactError):
        discover(tmp_path, "*")


def test_discover_raises_when_match_is_a_broken_symlink(tmp_path: Path) -> None:
    # is_dir() follows symlinks and would silently miss this; is_file() correctly
    # rejects it since the target doesn't exist.
    broken = tmp_path / "broken.rpm"
    broken.symlink_to(tmp_path / "does-not-exist.rpm")

    with pytest.raises(ArtifactError):
        discover(tmp_path, "*.rpm")


def test_discover_accepts_symlink_to_a_real_file(tmp_path: Path) -> None:
    real = tmp_path / "real.rpm"
    real.write_bytes(b"data")
    link = tmp_path / "link.rpm"
    link.symlink_to(real)

    result = discover(tmp_path, "link.rpm")

    assert [p.name for p in result] == ["link.rpm"]


def test_normalize_version_strips_leading_v() -> None:
    assert normalize_version("v1.2.3") == "1.2.3"


def test_normalize_version_strips_leading_uppercase_v() -> None:
    assert normalize_version("V1.2.3") == "1.2.3"


def test_normalize_version_strips_illegal_characters() -> None:
    assert normalize_version("v1.2.3+build_42") == "1.2.3+build42"


def test_normalize_version_raises_when_nothing_usable_remains() -> None:
    with pytest.raises(ArtifactError):
        normalize_version("v___")
