"""Generate reproducible TurbSim input sweeps for the IEA 15 MW turbine.

The supplied IEA input is used only as a format-compatible template. Generated
cases are independent files; the source template is never modified.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WIND_DIR = (
    PROJECT_ROOT / "models" / "IEA-15-240-RWT" / "IEA-15-240-RWT" / "Wind"
)
DEFAULT_TEMPLATE = DEFAULT_WIND_DIR / "IEA15MW_IEC_ETM_U50.0_Seed60362647.in"

INPUT_LINE = re.compile(
    r'^(?P<indent>\s*)(?P<value>"[^"]*"|\S+)(?P<gap>\s+)'
    r'(?P<key>\S+)(?P<rest>\s+.*)$'
)


@dataclass(frozen=True)
class TurbSimSettings:
    analysis_time: float = 60.0
    time_step: float = 0.05
    wind_type: str = "NTM"
    turbine_class: int = 1
    turbulence_category: str = "B"
    iec_edition: int = 3
    prefix: str = "IEA15MW_UMaineSemi"


def speed_label(speed: float) -> str:
    """Use a stable one-decimal speed label compatible with existing wind files."""
    return f"{speed:.1f}"


def case_name(settings: TurbSimSettings, speed: float, seed: int) -> str:
    return f"{settings.prefix}_{settings.wind_type}_U{speed_label(speed)}_Seed{seed}"


def replace_values(template: str, updates: dict[str, str]) -> str:
    """Replace first-column values by TurbSim keyword without reformatting the file."""
    remaining = set(updates)
    output: list[str] = []
    for line in template.splitlines(keepends=True):
        ending = "\n" if line.endswith("\n") else ""
        content = line.removesuffix("\n")
        match = INPUT_LINE.match(content)
        if match and match.group("key") in updates:
            key = match.group("key")
            output.append(
                f'{match.group("indent")}{updates[key]}{match.group("gap")}'
                f'{key}{match.group("rest")}{ending}'
            )
            remaining.discard(key)
        else:
            output.append(line)
    if remaining:
        raise ValueError(f"Template is missing required TurbSim keys: {', '.join(sorted(remaining))}")
    return "".join(output)


def iec_wind_type(settings: TurbSimSettings) -> str:
    if settings.wind_type == "NTM":
        return '"NTM"'
    return f'"{settings.turbine_class}{settings.wind_type}"'


def render_case(template: str, settings: TurbSimSettings, speed: float, seed: int) -> str:
    if speed <= 0:
        raise ValueError("Wind speeds must be greater than zero")
    if not -(2**31) <= seed <= 2**31 - 1:
        raise ValueError(f"TurbSim seed is outside the signed 32-bit range: {seed}")
    return replace_values(template, {
        "RandSeed1": str(seed),
        "WrADFF": "True",
        "WrBLFF": "False",
        "NumGrid_Z": "21",
        "NumGrid_Y": "21",
        "TimeStep": f"{settings.time_step:g}",
        "AnalysisTime": f"{settings.analysis_time:.1f}",
        "UsableTime": '"ALL"',
        "HubHt": "150.0",
        "GridHeight": "252.0",
        "GridWidth": "252.0",
        "TurbModel": '"IECKAI"',
        "IECstandard": f'"{settings.turbine_class}-ED{settings.iec_edition}"',
        "IECturbc": settings.turbulence_category,
        "IEC_WindType": iec_wind_type(settings),
        "WindProfileType": '"PL"',
        "RefHt": "150.0",
        "URef": speed_label(speed),
    })


def generate_cases(
    template_path: Path,
    output_dir: Path,
    wind_speeds: Iterable[float],
    seeds: Iterable[int],
    settings: TurbSimSettings,
    overwrite: bool = False,
) -> list[Path]:
    template_path = template_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not template_path.is_file():
        raise FileNotFoundError(f"TurbSim template not found: {template_path}")
    if settings.analysis_time <= 0 or settings.time_step <= 0:
        raise ValueError("Analysis time and time step must be greater than zero")

    combinations = [(float(speed), int(seed)) for speed in wind_speeds for seed in seeds]
    if not combinations:
        raise ValueError("At least one wind speed and one seed are required")
    destinations = [
        output_dir / f"{case_name(settings, speed, seed)}.in"
        for speed, seed in combinations
    ]
    if len(set(destinations)) != len(destinations):
        raise ValueError("The requested wind-speed/seed combinations contain duplicate case names")
    existing = [path for path in destinations if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Refusing to overwrite existing TurbSim input(s): "
            + ", ".join(path.name for path in existing)
        )

    template = template_path.read_text(encoding="utf-8")
    rendered = [render_case(template, settings, speed, seed) for speed, seed in combinations]
    output_dir.mkdir(parents=True, exist_ok=True)
    for destination, content in zip(destinations, rendered, strict=True):
        destination.write_text(content, encoding="utf-8")
    return destinations


def find_turbsim() -> str | None:
    project_executable = PROJECT_ROOT / ".openfast" / "conda-4.2.1" / "bin" / "turbsim"
    if project_executable.is_file() and project_executable.stat().st_mode & 0o111:
        return str(project_executable)
    return shutil.which("turbsim")


def run_cases(paths: Iterable[Path], executable: str) -> None:
    for path in paths:
        print(f"Running TurbSim: {path.name}", flush=True)
        completed = subprocess.run([executable, path.name], cwd=path.parent, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"TurbSim failed for {path.name} with exit code {completed.returncode}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an IEA 15 MW TurbSim parameter sweep for UMaineSemi"
    )
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_WIND_DIR)
    parser.add_argument(
        "--wind-speeds", type=float, nargs="+", default=[10.0], metavar="MPS",
        help="Hub-height mean wind speeds in m/s (default: 10)",
    )
    parser.add_argument(
        "--seeds", type=int, nargs="+", default=[101, 202, 303], metavar="SEED",
        help="TurbSim RandSeed1 values (default: 101 202 303)",
    )
    parser.add_argument("--analysis-time", type=float, default=60.0, metavar="SECONDS")
    parser.add_argument("--time-step", type=float, default=0.05, metavar="SECONDS")
    parser.add_argument(
        "--wind-type", choices=("NTM", "ETM", "EWM1", "EWM50"), default="NTM"
    )
    parser.add_argument("--turbine-class", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--turbulence-category", choices=("A", "B", "C"), default="B")
    parser.add_argument("--iec-edition", type=int, choices=(2, 3), default=3)
    parser.add_argument("--prefix", default="IEA15MW_UMaineSemi")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--run", action="store_true",
        help="Run each generated input with the project TurbSim executable and create .bts files",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    settings = TurbSimSettings(
        analysis_time=args.analysis_time,
        time_step=args.time_step,
        wind_type=args.wind_type,
        turbine_class=args.turbine_class,
        turbulence_category=args.turbulence_category,
        iec_edition=args.iec_edition,
        prefix=args.prefix,
    )
    paths = generate_cases(
        args.template,
        args.output_dir,
        args.wind_speeds,
        args.seeds,
        settings,
        args.overwrite,
    )
    print(f"Generated {len(paths)} TurbSim input file(s):")
    for path in paths:
        print(f"  {path}")
    if args.run:
        executable = find_turbsim()
        if not executable:
            raise SystemExit("TurbSim executable not found in .openfast/conda-4.2.1 or PATH")
        run_cases(paths, executable)
        print(f"Generated {len(paths)} binary wind field(s) beside the input files.")


if __name__ == "__main__":
    main()

    


