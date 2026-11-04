"""Install only the pinned OCR models and license; run at build/setup time."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request

from parsing_platform.ocr_language_data import (
    MODEL_BASE_URL, MODEL_FILES, file_matches_manifest, verified_tessdata_dir,
)


def prepare(directory: Path) -> None:
    if directory.is_symlink():
        raise ValueError("OCR language directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in MODEL_FILES.items():
        target = directory / name
        if target.is_symlink():
            raise ValueError("OCR language files must not be symlinks")
        if file_matches_manifest(target, expected):
            continue
        with urllib.request.urlopen(f"{MODEL_BASE_URL}/{name}", timeout=45) as response:
            content = response.read(expected["bytes"] + 1)
        if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
            raise ValueError(f"Pinned OCR asset failed integrity verification: {name}")
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".download-", delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        try:
            temporary.chmod(0o644)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    verified_tessdata_dir(directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.verify_only:
        verified_tessdata_dir(arguments.directory)
    else:
        prepare(arguments.directory)
    print(json.dumps({"verified": True, "files": sorted(MODEL_FILES)}))
