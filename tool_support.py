from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen


USER_AGENT = "AnimalCrossingOfflineAssistant/1.0"
TOOLS_DIRNAME = "tools"
NHSE_TOOL_DIRNAME = "NHSE"
PATTERN_EDITOR_DIRNAME = "ACNHDesignPatternEditor"
PATTERN_MIRROR_DIRNAME = "ACNH-Pattern-Dump-Index"


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=300) as response:
        return response.read()


def ensure_empty_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def download_zip_to_dir(url: str, target_dir: Path) -> None:
    ensure_empty_dir(target_dir)
    zip_path = target_dir.parent / f"{target_dir.name}.zip"
    zip_path.write_bytes(fetch_bytes(url))
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(target_dir)
    finally:
        if zip_path.exists():
            zip_path.unlink()


def find_first_executable(root: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        matches = list(root.rglob(pattern))
        if matches:
            return matches[0]
    return None


def get_latest_nhse_download_url() -> tuple[str, str]:
    data = fetch_json("https://dev.azure.com/project-pokemon/NHSE/_apis/build/builds?api-version=6.0")
    builds = [build for build in data.get("value", []) if build.get("sourceBranch") in {"refs/heads/main", "refs/heads/master"}]
    if not builds:
        raise RuntimeError("Unable to find a public NHSE build.")
    latest = builds[0]
    build_id = latest["id"]
    project_id = latest["definition"]["project"]["id"]
    url = f"https://dev.azure.com/project-pokemon/{project_id}/_apis/build/builds/{build_id}/artifacts?artifactName=NHSE&api-version=7.0&%24format=zip"
    return str(build_id), url


def install_nhse(base_dir: Path) -> tuple[Path, str]:
    build_id, url = get_latest_nhse_download_url()
    target_dir = base_dir / TOOLS_DIRNAME / NHSE_TOOL_DIRNAME
    download_zip_to_dir(url, target_dir)
    exe_path = find_first_executable(target_dir, ["NHSE.exe", "*.exe"])
    if exe_path is None:
        raise RuntimeError("NHSE downloaded, but no executable was found.")
    return exe_path, build_id


def install_pattern_editor(base_dir: Path) -> tuple[Path, str]:
    release = fetch_json("https://api.github.com/repos/FluffyFishGames/ACNHDesignPatternEditor/releases/latest")
    asset = next((asset for asset in release.get("assets", []) if asset.get("name", "").endswith("Win64.zip")), None)
    if asset is None:
        raise RuntimeError("Could not find a Win64 release for ACNHDesignPatternEditor.")
    target_dir = base_dir / TOOLS_DIRNAME / PATTERN_EDITOR_DIRNAME
    download_zip_to_dir(asset["browser_download_url"], target_dir)
    exe_path = find_first_executable(target_dir, ["*.exe"])
    if exe_path is None:
        raise RuntimeError("Pattern editor downloaded, but no executable was found.")
    return exe_path, release.get("tag_name", "")


def sync_pattern_mirror(base_dir: Path, source_hint: Path | None = None) -> Path:
    target_dir = base_dir / PATTERN_MIRROR_DIRNAME
    if source_hint is not None and source_hint.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_hint, target_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
        return target_dir

    if target_dir.exists():
        subprocess.run(["git", "-C", str(target_dir), "pull", "--ff-only"], check=True)
    else:
        subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/vectorcmdr/ACNH-Pattern-Dump-Index", str(target_dir)],
            check=True,
        )
    return target_dir


def launch_executable(exe_path: Path) -> None:
    if not exe_path.exists():
        raise FileNotFoundError(exe_path)
    subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))


@dataclass(slots=True)
class ExternalToolState:
    nhse_exe: Path | None
    pattern_editor_exe: Path | None
    pattern_mirror_dir: Path | None


class ExternalToolRepository:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.tools_dir = data_dir / TOOLS_DIRNAME
        self.pattern_mirror_dir = data_dir / PATTERN_MIRROR_DIRNAME
        self.tools_dir.mkdir(parents=True, exist_ok=True)

    def state(self) -> ExternalToolState:
        nhse_dir = self.tools_dir / NHSE_TOOL_DIRNAME
        pattern_dir = self.tools_dir / PATTERN_EDITOR_DIRNAME
        return ExternalToolState(
            nhse_exe=find_first_executable(nhse_dir, ["NHSE.exe", "*.exe"]) if nhse_dir.exists() else None,
            pattern_editor_exe=find_first_executable(pattern_dir, ["*.exe"]) if pattern_dir.exists() else None,
            pattern_mirror_dir=self.pattern_mirror_dir if self.pattern_mirror_dir.exists() else None,
        )
