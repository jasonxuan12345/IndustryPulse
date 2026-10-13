"""Smoke test：包可导入且版本号可解析。"""

from industrypulse import __version__


def test_version_is_semver_like() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)
