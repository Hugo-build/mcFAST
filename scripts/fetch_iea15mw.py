"""Download the official IEA 15 MW OpenFAST decks into ``models/``.

Only the OpenFAST subtree is extracted from the tagged upstream archive.  The
tag is pinned so tests and UI behavior do not change when upstream master does.
"""

from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import shutil
import sys
import tarfile
from urllib.request import urlopen


VERSION = "v1.1.17"
URL = f"https://github.com/IEAWindSystems/IEA-15-240-RWT/archive/refs/tags/{VERSION}.tar.gz"


def apply_openfast_42_compatibility(target: Path, project_root: Path) -> None:
    """Apply the two platform/schema fixes needed by the v1.1.17 archive."""
    case_dir = target / "IEA-15-240-RWT-UMaineSemi"
    elastodyn = case_dir / "IEA-15-240-RWT-UMaineSemi_ElastoDyn.dat"
    servo = case_dir / "IEA-15-240-RWT-UMaineSemi_ServoDyn.dat"

    elastodyn_text = elastodyn.read_text()
    if "HubIner_Teeter" not in elastodyn_text:
        anchor = "     969952   HubIner     - Hub inertia about rotor axis [3 blades] or teeter axis [2 blades] (kg m^2)\n"
        if anchor not in elastodyn_text:
            raise RuntimeError(f"Could not locate HubIner in {elastodyn}")
        elastodyn.write_text(
            elastodyn_text.replace(
                anchor,
                anchor + "          0   HubIner_Teeter - Hub inertia about teeter axis (2-blades) (kg m^2)\n",
                1,
            )
        )

    library_name = {"darwin": "libdiscon.dylib", "linux": "libdiscon.so"}.get(sys.platform)
    if library_name:
        library = project_root / ".openfast" / "conda-4.2.1" / "lib" / library_name
        relative_library = Path(os.path.relpath(library, case_dir)).as_posix()
        servo_lines = servo.read_text().splitlines(keepends=True)
        for index, line in enumerate(servo_lines):
            if "DLL_FileName" in line:
                servo_lines[index] = (
                    f'"{relative_library}"  DLL_FileName - Name/location of the dynamic library '
                    "{.dll [Windows], .so [Linux], or .dylib [macOS]} in the Bladed-DLL format (-) "
                    "[used only with Bladed Interface]\n"
                )
                break
        else:
            raise RuntimeError(f"Could not locate DLL_FileName in {servo}")
        servo.write_text("".join(servo_lines))


def fetch(destination: Path, force: bool = False) -> Path:
    destination = destination.resolve()
    target = destination / "IEA-15-240-RWT"
    if target.exists() and not force:
        apply_openfast_42_compatibility(target, destination.parent)
        print(f"Model already present at {target}")
        return target
    if target.exists():
        shutil.rmtree(target)
    destination.mkdir(parents=True, exist_ok=True)
    print(f"Downloading official IEA 15 MW model {VERSION}…")
    with urlopen(URL, timeout=90) as response:
        archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
    prefix = f"IEA-15-240-RWT-{VERSION.removeprefix('v')}/OpenFAST/"
    members = [member for member in archive.getmembers() if member.name.startswith(prefix) and member.isfile()]
    for member in members:
        relative = Path(member.name).relative_to(prefix)
        output = (target / relative).resolve()
        if target.resolve() not in output.parents:
            raise RuntimeError(f"Unsafe archive member: {member.name}")
        output.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source:
            with output.open("wb") as destination_file:
                shutil.copyfileobj(source, destination_file)
    print(f"Extracted {len(members)} OpenFAST files to {target}")
    apply_openfast_42_compatibility(target, destination.parent)
    print("Applied OpenFAST 4.2 and platform controller compatibility fixes")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=Path(__file__).resolve().parents[1] / "models")
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()
    fetch(arguments.destination, arguments.force)
