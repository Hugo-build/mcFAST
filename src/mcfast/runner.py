from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def find_openfast() -> str | None:
    """Prefer an OpenFAST launcher installed in the active Python environment."""
    executable_name = "openfast.exe" if os.name == "nt" else "openfast"
    environment_executable = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin") / executable_name
    if environment_executable.is_file() and os.access(environment_executable, os.X_OK):
        return str(environment_executable)
    return shutil.which("openfast")


def find_turbsim() -> str | None:
    """Prefer a TurbSim executable installed beside the active OpenFAST binary."""
    executable_name = "turbsim.exe" if os.name == "nt" else "turbsim"
    environment_executable = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin") / executable_name
    if environment_executable.is_file() and os.access(environment_executable, os.X_OK):
        return str(environment_executable)
    for bundled in sorted((PROJECT_ROOT / ".openfast").glob(f"*/bin/{executable_name}"), reverse=True):
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return str(bundled)
    return shutil.which("turbsim")


def openfast_version(executable: str) -> str | None:
    completed = subprocess.run(
        [executable, "-v"],
        check=False,
        capture_output=True,
        text=True,
    )
    for line in completed.stdout.splitlines():
        if "OpenFAST-v" in line:
            return line.strip()
    return None


def turbsim_version(executable: str) -> str | None:
    completed = subprocess.run(
        [executable, "-v"],
        check=False,
        capture_output=True,
        text=True,
    )
    for line in completed.stdout.splitlines():
        if "TurbSim-v" in line:
            return line.strip()
    return None


def run_turbsim(input_file: Path, executable: str, emit: Callable[[str], None] | None = None) -> int:
    """Run one TurbSim input and optionally stream merged console output."""
    process = subprocess.Popen(
        [executable, input_file.name],
        cwd=input_file.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is None:  # pragma: no cover - guaranteed by PIPE
        raise RuntimeError("TurbSim console pipe was not created")
    for line in process.stdout:
        if emit:
            emit(line)
    return process.wait()


def run_openfast(
    model: Path,
    executable: str,
    results_root: Path,
    run_id: str | None = None,
    echo_console: bool = True,
    manifest_metadata: dict[str, object] | None = None,
    reuse_run_dir: bool = False,
    append_console: bool = False,
) -> tuple[int, Path]:
    """Run a deck while streaming and preserving its console and outputs."""
    started = datetime.now(timezone.utc)
    run_id = run_id or f"{started:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    run_dir = results_root.expanduser().resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=reuse_run_dir)

    temporary_stem = f".mcfast-{run_id}"
    temporary_input = model.parent / f"{temporary_stem}.fst"
    shutil.copyfile(model, temporary_input)
    command = [executable, temporary_input.name]
    log_path = run_dir / "console.log"
    outputs: list[dict[str, int | str]] = []
    return_code: int | None = None
    error: str | None = None

    try:
        with log_path.open("a" if append_console else "w", encoding="utf-8") as log:
            def emit(message: str) -> None:
                log.write(message)
                log.flush()
                if echo_console:
                    print(message, end="", flush=True)

            emit(f"Working directory: {model.parent}\n")
            emit(f"Command: {' '.join(command)}\n")
            emit(f"Results: {run_dir}\n\n")
            process = subprocess.Popen(
                command,
                cwd=model.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if process.stdout is None:  # pragma: no cover - guaranteed by PIPE
                raise RuntimeError("OpenFAST console pipe was not created")
            for line in process.stdout:
                emit(line)
            return_code = process.wait()
    except BaseException as exc:
        error = str(exc)
        raise
    finally:
        temporary_input.unlink(missing_ok=True)
        for generated in sorted(model.parent.glob(f"{temporary_stem}*")):
            suffix = generated.name.removeprefix(temporary_stem)
            destination = run_dir / f"{model.stem}{suffix}"
            shutil.move(str(generated), destination)
            outputs.append({"path": destination.name, "bytes": destination.stat().st_size})

        finished = datetime.now(timezone.utc)
        manifest = {
            "run_id": run_id,
            "status": "completed" if return_code == 0 else "failed",
            "return_code": return_code,
            "error": error,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_seconds": (finished - started).total_seconds(),
            "model": str(model),
            "executable": executable,
            "openfast_version": openfast_version(executable),
            "command": command,
            "outputs": outputs,
        }
        if manifest_metadata:
            manifest.update(manifest_metadata)
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    assert return_code is not None
    return return_code, run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an OpenFAST input deck with the installed native executable")
    parser.add_argument("input", type=Path, help="Path to the primary .fst input file")
    parser.add_argument(
        "--executable",
        help="OpenFAST executable (defaults to the active Python environment, then PATH)",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("results/openfast"),
        help="Directory for timestamped run logs and generated files",
    )
    parser.add_argument("--run-id", help="Optional explicit result directory name")
    parser.add_argument("--dry-run", action="store_true", help="Validate paths and print the command only")
    args = parser.parse_args()
    model = args.input.expanduser().resolve()
    if not model.is_file():
        parser.error(f"input deck does not exist: {model}")
    executable = args.executable or find_openfast()
    if not executable:
        parser.error("OpenFAST executable not found. Install it with Homebrew/Conda, or pass --executable.")
    if args.dry_run:
        command = [str(executable), model.name]
        print(f"Working directory: {model.parent}")
        print("Command:", " ".join(command))
        print(f"Results root: {args.results_dir.expanduser().resolve()}")
        return
    try:
        return_code, run_dir = run_openfast(model, str(executable), args.results_dir, args.run_id)
    except FileExistsError:
        parser.error(f"result run already exists: {args.results_dir / str(args.run_id)}")
    print(f"Saved run to {run_dir}")
    raise SystemExit(return_code)
