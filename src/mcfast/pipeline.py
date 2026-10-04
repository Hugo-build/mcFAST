"""Shared isolated and interactive TurbSim/OpenFAST execution."""
from pathlib import Path
from typing import Callable, Any

from .models import model_geometry, safe_path
from .wind import wind_status, project_path
from .runner import run_openfast, run_turbsim, turbsim_version


def execute_pipeline(root: Path, manifest: dict, executable: str, turbsim: str | None,
                     results: Path, run_id: str, metadata: dict | None = None,
                     phase: Callable[[str], None] = lambda _: None,
                     openfast_runner: Callable = run_openfast,
                     turbsim_runner: Callable = run_turbsim) -> tuple[int, Path]:
    directory = results / run_id
    directory.mkdir(parents=True, exist_ok=True)
    phase('preflight')
    wind = wind_status(root, manifest['entry'], manifest, turbsim_executable=turbsim)
    if wind['active'] and not wind['valid']:
        raise RuntimeError(wind['message'])
    generated = False
    with (directory / 'console.log').open('a', encoding='utf-8') as log:
        def emit(message: str) -> None:
            log.write(message)
            log.flush()
        if wind['mode'] == 'managed':
            if wind['needs_generation']:
                if not turbsim:
                    raise RuntimeError('TurbSim executable not found')
                source = project_path(root, wind['selected_turbsim_input'])
                emit(f'TurbSim input: {source}\nCommand: {turbsim} {source.name}\n')
                phase('turbsim')
                code = turbsim_runner(source, turbsim, emit)
                if code != 0:
                    raise RuntimeError(f'TurbSim failed with exit code {code}')
                if not source.with_suffix('.bts').is_file() or source.with_suffix('.bts').stat().st_size == 0:
                    raise RuntimeError('TurbSim did not create the expected non-empty output')
                generated = True
            else:
                emit(f"Reusing current TurbSim wind field: {wind['resolved_bts']}\n")
        elif wind['mode'] == 'external':
            emit(f"Using external TurbSim wind field without generation: {wind['resolved_bts']}\n")
    wind_metadata: dict[str, Any] = {
        'mode': wind['mode'], 'inflow_file': wind['inflow_file'],
        'file_name_bts': wind['file_name_bts'], 'resolved_bts': wind['resolved_bts'],
        'turbsim_input': wind['selected_turbsim_input'], 'managed_bts': wind['managed_bts'],
        'generated': generated, 'generation_reason': 'missing_or_stale' if generated else 'not_required',
    }
    if generated:
        wind_metadata.update(turbsim_executable=turbsim, turbsim_version=turbsim_version(turbsim), turbsim_return_code=0)
    phase('openfast')
    return openfast_runner(safe_path(root, manifest['entry']), executable, results, run_id,
                          echo_console=False, manifest_metadata={
                              **(metadata or {}), 'workspace_id': manifest['workspace_id'],
                              'workspace_entry': manifest['entry'], 'wind': wind_metadata,
                              'geometry': model_geometry(root, manifest['entry']),
                          }, reuse_run_dir=True, append_console=True)
