"""Offline frozen-study preparation and independent Slurm case workers."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import socket
import uuid

from .models import referenced_files, safe_path
from .parser import parse_file
from .pipeline import execute_pipeline
from .runner import find_openfast, find_turbsim
from .studies import IDENTIFIER, atomic_json, now, prepare_case
from .wind import discover_turbsim_inputs


def prepare_snapshot(workspace_root: Path, workspace: str, study_id: str) -> Path:
    # Reuse saved-case coercion without starting a server or its coordinator.
    from .api import _normalize_study, _validate_samples
    if not all(IDENTIFIER.fullmatch(value) for value in (workspace, study_id)):
        raise ValueError('Invalid workspace or study identifier')
    directory = safe_path(workspace_root.resolve(), f'{workspace}/workspace.json').parent
    project = (directory / 'project').resolve()
    if directory not in project.parents or not project.is_dir():
        raise ValueError('Workspace project directory is missing or outside the workspace')
    manifest = json.loads(safe_path(directory, 'workspace.json').read_text())
    study = _normalize_study(json.loads(safe_path(directory, f'studies/{study_id}.json').read_text()))
    if manifest['workspace_id'] != workspace or study['workspace_id'] != workspace or study['study_id'] != study_id:
        raise ValueError('Workspace/study identity does not match its directory')
    variables = study['variables']
    if not 1 <= len(variables) <= 200:
        raise ValueError('A study requires 1–200 variables')
    allowed = {item['path'] for item in referenced_files(project, manifest['entry'])['files']}
    allowed.update(item['path'] for item in discover_turbsim_inputs(project))
    names, bindings, resolved = set(), set(), []
    for variable in variables:
        name, binding = variable['name'], (variable['file'], variable['key'])
        if not name.strip() or name in names or binding in bindings:
            raise ValueError('Variable names and parameter bindings must be unique')
        if variable['file'] not in allowed:
            raise ValueError(f"Variable file is not linked: {variable['file']}")
        matches = [p for p in parse_file(safe_path(project, variable['file']))['parameters'] if p['key'] == variable['key']]
        if len(matches) != 1 or matches[0]['kind'] == 'keyword':
            raise ValueError(f"Invalid scalar parameter binding: {variable['file']}:{variable['key']}")
        names.add(name); bindings.add(binding)
        resolved.append({**variable, 'kind': matches[0]['kind'], 'original_value': matches[0]['value']})
    study.update(variables=resolved, samples=_validate_samples(resolved, study['samples']))
    if any(path.is_symlink() for path in project.rglob('*')):
        raise ValueError('Project contains an unsupported symlink')
    snapshot = directory / 'slurm' / ('slurm-' + uuid.uuid4().hex[:12])
    snapshot.mkdir(parents=True)
    try:
        shutil.copytree(project, snapshot / 'project')
        atomic_json(snapshot / 'workspace.json', {**manifest, 'snapshot_source_root': str(project.resolve())})
        atomic_json(snapshot / 'study.json', study)
        atomic_json(snapshot / 'run.json', {
            'run_id': snapshot.name, 'workspace_id': workspace, 'study_id': study_id,
            'created_at': now(), 'sample_count': len(study['samples']),
            'results_root': str(directory / 'results'),
        })
    except BaseException:
        shutil.rmtree(snapshot)
        raise
    return snapshot


def case_indices(count: int, rank: int, workers: int) -> range:
    if workers < 1 or not 0 <= rank < workers:
        raise ValueError('Worker rank must be between 0 and worker count minus one')
    return range(rank, count, workers)


def run_worker(snapshot: Path, rank: int, workers: int,
               openfast: str | None = None, turbsim: str | None = None) -> int:
    snapshot = snapshot.resolve()
    run = json.loads((snapshot / 'run.json').read_text())
    study = json.loads((snapshot / 'study.json').read_text())
    manifest = json.loads((snapshot / 'workspace.json').read_text())
    indices = case_indices(len(study['samples']), rank, workers)
    executable = openfast or find_openfast()
    turbsim = turbsim or find_turbsim()
    if indices and not executable:
        raise ValueError('OpenFAST is unavailable; load the cluster module or set MCFAST_OPENFAST')
    hostname = socket.gethostname()
    failed = 0
    for index in indices:
        run_id = f"{run['run_id']}-case-{index + 1}"
        case_dir = snapshot / 'cases' / run_id
        status_path = snapshot / 'status' / f'case-{index + 1}.json'
        results_root = Path(run['results_root'])
        metadata = {
            'slurm_run_id': run['run_id'], 'slurm_job_id': os.environ.get('SLURM_JOB_ID'),
            'study_id': study['study_id'], 'study_name': study['name'],
            'sample_index': index, 'sample_values': study['samples'][index],
            'target': hostname, 'worker_rank': rank,
        }
        # mkdir is an atomic claim on the shared filesystem. Never overwrite an
        # earlier attempt, even after preemption or an accidental resubmission.
        case_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            case_dir.mkdir()
        except FileExistsError:
            print(f'{hostname} worker {rank}: case {index + 1} already claimed; use a new snapshot to rerun', flush=True)
            failed += 1
            continue
        record = {**metadata, 'run_id': run_id, 'status': 'running', 'phase': 'preparation', 'started_at': now()}
        atomic_json(status_path, record)
        print(f'{hostname} worker {rank}: starting case {index + 1}', flush=True)
        def phase(value):
            record['phase'] = value
            atomic_json(status_path, record)
        try:
            root = prepare_case(snapshot / 'project', study, manifest, index, case_dir / 'project')
            code, output = execute_pipeline(root, manifest, executable, turbsim, results_root, run_id, metadata, phase)
            state = 'completed' if code == 0 else 'failed'
            payload = json.loads((output / 'manifest.json').read_text())
            payload.update(model=manifest['entry'], phase='complete' if code == 0 else 'failed')
            atomic_json(output / 'manifest.json', payload)
            record.update(status=state, phase=payload['phase'], return_code=code)
            failed += code != 0
        except Exception as exc:
            failed += 1
            record.update(status='failed', phase='failed', error=str(exc), return_code=None)
            output = results_root / run_id
            output.mkdir(parents=True, exist_ok=True)
            with (output / 'console.log').open('a', encoding='utf-8') as log:
                log.write(f'\nmcFAST case failed: {exc}\n')
            atomic_json(output / 'manifest.json', {
                **record, 'workspace_id': manifest['workspace_id'], 'model': manifest['entry'],
                'workspace_entry': manifest['entry'], 'finished_at': now(), 'outputs': [],
            })
        record['finished_at'] = now()
        atomic_json(status_path, record)
        print(f"{hostname} worker {rank}: case {index + 1} {record['status']}", flush=True)
    return 1 if failed else 0


def snapshot_status(snapshot: Path) -> dict:
    snapshot = snapshot.resolve()
    run = json.loads((snapshot / 'run.json').read_text())
    records = [json.loads(path.read_text()) for path in (snapshot / 'status').glob('case-*.json')]
    counts = Counter(record['status'] for record in records)
    counts['not_started'] = run['sample_count'] - len(records)
    return {**run, 'counts': dict(counts), 'cases': sorted(records, key=lambda record: record['sample_index'])}


def main() -> None:
    parser = argparse.ArgumentParser(description='Prepare and run saved studies on Slurm without a web server')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='Freeze a saved study and print its snapshot path')
    prepare.add_argument('--workspace', required=True)
    prepare.add_argument('--study', required=True)
    prepare.add_argument('--workspace-root', type=Path, default=Path(os.environ.get('MCFAST_WORKSPACE_DIR', Path(__file__).resolve().parents[2] / 'workspaces')))
    worker = commands.add_parser('worker', help='Run the cases assigned to one Slurm CPU worker')
    worker.add_argument('--snapshot', required=True, type=Path)
    worker.add_argument('--rank', type=int, default=int(os.environ.get('SLURM_PROCID', '0')))
    worker.add_argument('--workers', type=int, default=int(os.environ.get('SLURM_NTASKS', '1')))
    worker.add_argument('--openfast', default=os.environ.get('MCFAST_OPENFAST'))
    worker.add_argument('--turbsim', default=os.environ.get('MCFAST_TURBSIM'))
    status = commands.add_parser('status', help='Read per-case status from the shared filesystem')
    status.add_argument('--snapshot', required=True, type=Path)
    status.add_argument('--details', action='store_true', help='Include individual case records')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            print(prepare_snapshot(args.workspace_root, args.workspace, args.study))
        elif args.command == 'status':
            summary = snapshot_status(args.snapshot)
            if not args.details:
                summary.pop('cases')
            print(json.dumps(summary, indent=2))
        else:
            raise SystemExit(run_worker(args.snapshot, args.rank, args.workers, args.openfast, args.turbsim))
    except Exception as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
