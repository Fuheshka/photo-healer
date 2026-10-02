# -*- coding: utf-8 -*-
"""Lightweight cross-platform GitHub Release update checker for Photo Healer.

Follows app-update-checker and app-i18n-localization standards with zero external
dependencies (native urllib.request), 24-hour rate limit protection, SemVer comparison,
platform asset detection, and fail-safe operation.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_healer import __version__
from photo_healer.cli.i18n import t

DEFAULT_REPO: str = "fuheshka/photo-healer"
DEFAULT_COOLDOWN_SECONDS: int = 86400  # 24 hours
DEFAULT_CACHE_DIR: Path = Path.home() / ".photo-healer"
DEFAULT_CACHE_FILE: Path = DEFAULT_CACHE_DIR / "update_check.json"


@dataclass
class UpdateResult:
    """Result of an update check operation."""

    status: str  # "update_available", "up_to_date", "throttled", "error"
    current_version: str
    latest_version: str | None = None
    download_url: str | None = None
    asset_name: str | None = None
    release_notes: str | None = None
    error_message: str | None = None
    cached: bool = False


def parse_semver(version_str: str) -> list[int]:
    """Parse a SemVer string into a list of integers, stripping v/V and metadata."""
    clean = str(version_str).strip().lstrip("vV")
    if not clean:
        return [0]

    parts: list[int] = []
    for chunk in clean.split("."):
        num_str = ""
        for ch in chunk:
            if ch.isdigit():
                num_str += ch
            else:
                break
        parts.append(int(num_str) if num_str else 0)

    return parts or [0]


def is_version_newer(candidate: str, current: str) -> bool:
    """Return True if candidate version is strictly newer than current version."""
    p1 = parse_semver(candidate)
    p2 = parse_semver(current)
    length = max(len(p1), len(p2))
    p1.extend([0] * (length - len(p1)))
    p2.extend([0] * (length - len(p2)))
    return p1 > p2


def find_platform_asset(assets: list[dict[str, Any]], target_platform: str | None = None) -> dict[str, str] | None:
    """Find the best matching asset for the target platform.

    Returns a dict with {"name": asset_name, "url": download_url} or None if no suitable asset found.
    """
    if not assets:
        return None

    if target_platform is None:
        target_platform = sys.platform

    plat = target_platform.lower()
    if plat.startswith("win"):
        target_os = "windows"
    elif plat.startswith("darwin") or "mac" in plat:
        target_os = "macos"
    elif plat.startswith("linux"):
        target_os = "linux"
    else:
        target_os = "other"

    # Filter out source archives or checksum files
    filtered_assets = [
        a for a in assets
        if not any(ignored in a.get("name", "").lower() for ignored in ("source", "checksum", "sha256", "md5"))
    ]

    if target_os == "windows":
        preferred_matchers = [
            lambda n: ("photo-healer-windows" in n or "photo-healer-win" in n) and n.endswith(".zip"),
            lambda n: ("windows" in n or "win" in n) and any(n.endswith(ext) for ext in (".zip", ".exe", ".msi")),
            lambda n: n.endswith(".exe"),
            lambda n: n.endswith(".msi"),
            lambda n: "photo-healer" in n and n.endswith(".zip"),
            lambda n: n.endswith(".zip"),
        ]
    elif target_os == "macos":
        preferred_matchers = [
            lambda n: ("photo-healer-macos" in n or "photo-healer-mac" in n) and n.endswith(".dmg"),
            lambda n: n.endswith(".dmg"),
            lambda n: ("photo-healer-macos" in n or "photo-healer-mac" in n) and n.endswith(".zip"),
            lambda n: ("macos" in n or "darwin" in n or "mac" in n) and any(n.endswith(ext) for ext in (".dmg", ".zip", ".tar.gz")),
            lambda n: "photo-healer" in n and (n.endswith(".zip") or n.endswith(".tar.gz")),
        ]
    elif target_os == "linux":
        preferred_matchers = [
            lambda n: "photo-healer-linux" in n and n.endswith(".appimage"),
            lambda n: n.endswith(".appimage"),
            lambda n: "photo-healer-linux" in n,
            lambda n: "linux" in n and any(n.endswith(ext) for ext in (".deb", ".rpm", ".tar.gz", ".appimage")),
            lambda n: n.endswith(".deb"),
            lambda n: n.endswith(".rpm"),
            lambda n: "photo-healer" in n and n.endswith(".tar.gz"),
        ]
    else:
        preferred_matchers = []

    for matcher in preferred_matchers:
        for asset in filtered_assets:
            name = asset.get("name", "").lower()
            if matcher(name):
                return {
                    "name": asset.get("name", ""),
                    "url": asset.get("browser_download_url", ""),
                }

    return None


def load_cache(cache_file: Path) -> dict[str, Any]:
    """Safely load cached update check data without raising exceptions."""
    if not cache_file.is_file():
        return {}
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(cache_file: Path, data: dict[str, Any]) -> None:
    """Safely save update check metadata to cache file."""
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def check_cooldown(cache_file: Path, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> bool:
    """Return True if cooldown is active (i.e. check was performed recently)."""
    cache = load_cache(cache_file)
    last_check = cache.get("last_check_timestamp")
    if last_check is None:
        return False
    try:
        elapsed = time.time() - float(last_check)
        return elapsed < cooldown_seconds
    except (ValueError, TypeError):
        return False


def check_for_updates(
    current_version: str = __version__,
    repo: str = DEFAULT_REPO,
    cache_file: Path | None = None,
    force: bool = False,
    timeout: float = 4.0,
    target_platform: str | None = None,
    lang: str | None = None,
) -> UpdateResult:
    """Check GitHub Releases for newer version of the application."""
    if cache_file is None:
        cache_file = DEFAULT_CACHE_FILE

    if not force and check_cooldown(cache_file):
        return UpdateResult(
            status="throttled",
            current_version=current_version,
            cached=True,
        )

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": f"photo-healer/{current_version}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = getattr(resp, "status", 200)
            if status_code != 200:
                return UpdateResult(
                    status="error",
                    current_version=current_version,
                    error_message=f"HTTP {status_code}",
                )
            raw_data = resp.read()
            release = json.loads(raw_data.decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            save_cache(cache_file, {"last_check_timestamp": time.time()})
        return UpdateResult(
            status="error",
            current_version=current_version,
            error_message=f"HTTP Error {e.code}: {e.reason}",
        )
    except urllib.error.URLError as e:
        return UpdateResult(
            status="error",
            current_version=current_version,
            error_message=f"Network error: {e.reason}",
        )
    except (TimeoutError, OSError) as e:
        return UpdateResult(
            status="error",
            current_version=current_version,
            error_message=f"Request timed out or failed: {e}",
        )
    except Exception as e:
        return UpdateResult(
            status="error",
            current_version=current_version,
            error_message=str(e),
        )

    if not isinstance(release, dict):
        return UpdateResult(
            status="error",
            current_version=current_version,
            error_message="Invalid release payload format",
        )

    raw_tag = release.get("tag_name", "")
    latest_version = raw_tag.strip().lstrip("vV")
    html_url = release.get("html_url", f"https://github.com/{repo}/releases/latest")
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        assets = []

    matched_asset = find_platform_asset(assets, target_platform=target_platform)
    download_url = matched_asset["url"] if matched_asset else html_url
    asset_name = matched_asset["name"] if matched_asset else None

    # Record successful check in cache
    save_cache(cache_file, {
        "last_check_timestamp": time.time(),
        "latest_version": latest_version,
        "download_url": download_url,
    })

    if is_version_newer(latest_version, current_version):
        return UpdateResult(
            status="update_available",
            current_version=current_version,
            latest_version=latest_version,
            download_url=download_url,
            asset_name=asset_name,
            release_notes=release.get("body"),
        )

    return UpdateResult(
        status="up_to_date",
        current_version=current_version,
        latest_version=latest_version,
        download_url=download_url,
    )


def format_update_notice(result: UpdateResult, lang: str | None = None) -> str:
    """Format a non-obtrusive, bilingual CLI update notification banner."""
    if result.status != "update_available" or not result.latest_version:
        return ""

    title = t("updater.update_available", default="Update Available", lang=lang)
    msg = t(
        "updater.new_version_notice",
        version=result.latest_version,
        url=result.download_url or "",
        default=f"A new version {result.latest_version} is available. Download: {result.download_url}",
        lang=lang,
    )
    current_lbl = "Current" if (lang or "").startswith("en") else "Текущая"
    content_lines = [
        f"★ {title}: Photo Healer v{result.latest_version} ({current_lbl}: v{result.current_version})",
        f"  ➜ {msg}",
    ]
    max_w = max(len(line) for line in content_lines)
    border = "─" * (max_w + 2)
    return (
        f"\n┌{border}┐\n"
        + "\n".join(f"│ {line.ljust(max_w)} │" for line in content_lines)
        + f"\n└{border}┘\n"
    )


def check_and_notify_background(
    current_version: str = __version__,
    repo: str = DEFAULT_REPO,
    cache_file: Path | None = None,
    lang: str | None = None,
    stream: Any = sys.stderr,
) -> UpdateResult | None:
    """Check for updates quietly in background with rate limit guard and print notice if found.

    Fail-safe: never raises exceptions.
    """
    try:
        result = check_for_updates(
            current_version=current_version,
            repo=repo,
            cache_file=cache_file,
            force=False,
            lang=lang,
        )
        if result.status == "update_available":
            notice = format_update_notice(result, lang=lang)
            if notice and stream:
                stream.write(notice)
                if hasattr(stream, "flush"):
                    stream.flush()
        return result
    except Exception:
        return None
