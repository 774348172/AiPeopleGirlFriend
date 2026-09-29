"""Create and verify a ZIP64 archive for the Windows portable build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path


def long_path(path: Path) -> str:
    value = str(path.resolve())
    return "\\\\?\\" + value if os.name == "nt" and not value.startswith("\\\\?\\") else value


def build(source: Path, output: Path) -> dict[str, object]:
    source = source.resolve()
    output = output.resolve()
    if not (source / "CatGirlfriend.exe").is_file() or not (source / "Backend" / "release_manifest.json").is_file():
        raise ValueError("incomplete portable build")
    if output.is_relative_to(source):
        raise ValueError("archive must be outside the source directory")

    temporary = output.with_name(output.name + ".tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    count = 0
    try:
        with zipfile.ZipFile(long_path(temporary), "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for current, directories, files in os.walk(long_path(source)):
                relative_dir = Path(current).relative_to(long_path(source)).parts
                if not relative_dir:
                    directories[:] = [name for name in directories
                                      if name != "CatGirlfriend_BurstDebugInformation_DoNotShip"]
                elif relative_dir == ("Backend",):
                    directories[:] = [name for name in directories if name not in {"data", "logs"}]
                directories.sort()
                files.sort()
                for name in files:
                    path = Path(current) / name
                    relative = path.relative_to(long_path(source)).as_posix()
                    archive.write(path, arcname=source.name + "/" + relative)
                    count += 1
        with zipfile.ZipFile(long_path(temporary), "r", allowZip64=True) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise ValueError("ZIP integrity check failed: " + bad)
            names = set(archive.namelist())
            for required in ("CatGirlfriend.exe", "Backend/runtime/python.exe", "Backend/assets/gemma4_nf4_runtime_manifest.json"):
                if source.name + "/" + required not in names:
                    raise ValueError("ZIP is missing " + required)
        os.replace(long_path(temporary), long_path(output))
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise

    digest = hashlib.sha256()
    with open(long_path(output), "rb") as stream:
        for chunk in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return {"archive": str(output), "bytes": output.stat().st_size, "files": count, "sha256": digest.hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
