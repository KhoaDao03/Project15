"""Stage only the public server and its installed dependencies in a fresh venv.

Run with the project's Python as root after creating the destination venv.
No package downloads, trading modules, runtime data, or credentials are copied.
"""

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import sysconfig
from pathlib import Path


def stage(destination):
    root = Path(__file__).resolve().parents[1]
    source_packages = Path(sysconfig.get_path("purelib")).resolve()
    destination = Path(destination).resolve()
    target_packages = destination / ".venv/lib/python3.12/site-packages"
    if not target_packages.is_dir() or destination == root:
        raise ValueError("Create a separate Python 3.12 virtual environment first")
    manifest = {}
    for line in (root / "deploy/public-web/requirements.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==")
        distribution = importlib.metadata.distribution(name)
        if distribution.version != version:
            raise ValueError(f"Installed version changed for {name}; review requirements before staging")
        for file in distribution.files or []:
            source = Path(distribution.locate_file(file)).resolve()
            if not source.is_relative_to(source_packages) or source.suffix == ".pyc":
                continue
            target = target_packages / source.relative_to(source_packages)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            manifest[str(target.relative_to(destination))] = hashlib.sha256(target.read_bytes()).hexdigest()
    for name in ("public_site.py", "public_static/index.html", "public_static/viewer.js",
                 "public_static/viewer.css", "public_static/logo.svg", "public_static/section-logo.svg",
                 "public_static/performance.html", "public_static/performance.js"):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "src/btc15" / name, target)
        manifest[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Staged {len(manifest)} verified files in {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    stage(parser.parse_args().destination)
