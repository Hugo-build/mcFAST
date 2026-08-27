"""Download the official IEA 22 MW OpenFAST decks into ``models/``.

Only the OpenFAST subtree is extracted from the pinned upstream release.  It
contains the monopile and semisubmersible decks along with their shared turbine
and ROSCO input data.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import shutil
import tarfile
from urllib.request import urlopen


VERSION = "v1.1.0"
URL = f"https://github.com/IEAWindSystems/IEA-22-280-RWT/archive/refs/tags/{VERSION}.tar.gz"


def fetch(destination: Path, force: bool = False) -> Path:
    """Fetch and extract the pinned IEA 22 MW OpenFAST model release."""
    destination = destination.resolve()
    target = destination / "IEA-22-280-RWT"
    if target.exists() and not force:
        print(f"Model already present at {target}")
        return target
    if target.exists():
        shutil.rmtree(target)

    destination.mkdir(parents=True, exist_ok=True)
    print(f"Downloading official IEA 22 MW model {VERSION}…")
    with urlopen(URL, timeout=90) as response:
        archive_bytes = response.read()

    prefix = f"IEA-22-280-RWT-{VERSION.removeprefix('v')}/OpenFAST/"
    extracted = 0
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        members = [
            member
            for member in archive.getmembers()
            if member.name.startswith(prefix) and member.isfile()
        ]
        if not members:
            raise RuntimeError(f"Archive does not contain the expected {prefix} subtree")

        for member in members:
            relative = Path(member.name).relative_to(prefix)
            output = (target / relative).resolve()
            if target.resolve() not in output.parents:
                raise RuntimeError(f"Unsafe archive member: {member.name}")
            output.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"Could not read archive member: {member.name}")
            with source, output.open("wb") as destination_file:
                shutil.copyfileobj(source, destination_file)
            extracted += 1

    print(f"Extracted {extracted} OpenFAST files to {target}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch the official IEA 22 MW OpenFAST decks"
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "models",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing model directory",
    )
    arguments = parser.parse_args()
    fetch(arguments.destination, arguments.force)
