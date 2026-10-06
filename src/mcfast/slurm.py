"""Offline frozen-study preparation and independent Slurm case workers."""
from __future__ import annotations

import argparse
from collections import Counter
from importlib.resources import files
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
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


def submit_study(workspace_root: Path, workspace: str, study_id: str, *,
                 nodes: int | None = None, workers_per_node: int | None = None,
                 account: str | None = None, partition: str | None = None,
                 wall_time: str | None = None, memory_per_cpu: str | None = None,
                 array: bool = False, dry_run: bool = False) -> dict:
    """Freeze the study, select this interpreter, and submit exactly one job."""
    if (nodes is not None and nodes < 1) or (workers_per_node is not None and workers_per_node < 1):
        raise ValueError('Nodes and workers per node must be positive integers')
    template_path = Path(__file__).resolve().parents[2] / 'scripts/mcfast-study.slurm'
    template = (template_path.read_text() if template_path.is_file()
                else files('mcfast').joinpath('templates/mcfast-study.slurm').read_text())
    sbatch = shutil.which('sbatch')
    if not dry_run and not sbatch:
        raise ValueError('sbatch is unavailable. Run submit on the cluster login node.')
    if array and nodes is None:
        nodes = int(next(line.split('=', 1)[1] for line in template.splitlines() if line.startswith('#SBATCH --nodes=')))
    snapshot = prepare_snapshot(workspace_root, workspace, study_id)
    exports = f'export MCFAST_PYTHON={shlex.quote(sys.executable)}\n'
    if array:
        exports += 'export MCFAST_SLURM_ARRAY=1\n'
    else:
        exports += 'unset MCFAST_SLURM_ARRAY\n'
    script = snapshot / 'submit.slurm'
    script.write_text(template.replace('set -euo pipefail\n', 'set -euo pipefail\n\n' + exports, 1))
    pattern = 'slurm-%A_%a' if array else 'slurm-%j'
    command = [sbatch or 'sbatch', '--parsable', '--export=ALL',
               f'--output={snapshot / (pattern + ".out")}', f'--error={snapshot / (pattern + ".err")}']
    options = {'nodes': 1 if array else nodes, 'ntasks-per-node': workers_per_node,
               'account': account, 'partition': partition, 'time': wall_time, 'mem-per-cpu': memory_per_cpu}
    command.extend(f'--{key}={value}' for key, value in options.items() if value is not None)
    if array:
        command.append(f'--array=0-{nodes - 1}%{nodes}')
    command.extend([str(script), str(snapshot)])
    submission = {'snapshot': str(snapshot), 'command': command, 'mode': 'array' if array else 'multi_node',
                  'status': 'prepared', 'created_at': now()}
    atomic_json(snapshot / 'submission.json', submission)
    if dry_run:
        return submission
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        submission.update(status='rejected', error=result.stderr.strip() or result.stdout.strip())
        atomic_json(snapshot / 'submission.json', submission)
        raise ValueError(f"Slurm rejected submission: {submission['error']}\nSnapshot: {snapshot}")
    job_id, _, cluster = result.stdout.strip().partition(';')
    if not job_id.isdigit():
        submission.update(status='submitted_unknown', output=result.stdout.strip())
        atomic_json(snapshot / 'submission.json', submission)
        raise ValueError(f'Slurm returned success but no recognizable job ID. Check squeue before resubmitting.\nSnapshot: {snapshot}')
    submission.update(status='submitted', job_id=job_id, cluster=cluster or None)
    try:
        atomic_json(snapshot / 'submission.json', submission)
    except OSError as exc:
        print(f'Job {job_id} submitted, but could not save its submission record: {exc}', file=sys.stderr)
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return submission


def worker_identity() -> tuple[int, int]:
    rank = int(os.environ.get('SLURM_PROCID', '0'))
    workers = int(os.environ.get('SLURM_NTASKS', '1'))
    if os.environ.get('MCFAST_SLURM_ARRAY') == '1':
        rank += int(os.environ['SLURM_ARRAY_TASK_ID']) * workers
        workers *= int(os.environ['SLURM_ARRAY_TASK_COUNT'])
    return rank, workers


def main() -> None:
    parser = argparse.ArgumentParser(description='Submit a saved study to Slurm in one command')
    commands = parser.add_subparsers(dest='command', required=True)
    submit = commands.add_parser('submit', help='Prepare and submit a saved study in one command')
    submit.add_argument('study_file', nargs='?', type=Path, help='Saved workspaces/WORKSPACE/studies/STUDY.json file')
    submit.add_argument('--workspace')
    submit.add_argument('--study')
    submit.add_argument('--workspace-root', type=Path, default=Path(os.environ.get('MCFAST_WORKSPACE_DIR', Path(__file__).resolve().parents[2] / 'workspaces')))
    submit.add_argument('--nodes', type=int, help='Number of nodes (defaults to the Slurm template)')
    submit.add_argument('--workers-per-node', type=int, help='Concurrent cases per node (defaults to the Slurm template)')
    submit.add_argument('--account')
    submit.add_argument('--partition')
    submit.add_argument('--time', help='Wall-time override')
    submit.add_argument('--mem-per-cpu', help='Memory per case override, e.g. 4G')
    submit.add_argument('--array', action='store_true', help='Submit one single-node array task per requested node')
    submit.add_argument('--dry-run', action='store_true', help='Prepare files and show the command without submitting')
    prepare = commands.add_parser('prepare', help='Freeze a saved study and print its snapshot path')
    prepare.add_argument('--workspace', required=True)
    prepare.add_argument('--study', required=True)
    prepare.add_argument('--workspace-root', type=Path, default=Path(os.environ.get('MCFAST_WORKSPACE_DIR', Path(__file__).resolve().parents[2] / 'workspaces')))
    worker = commands.add_parser('worker', help='Run the cases assigned to one Slurm CPU worker')
    worker.add_argument('--snapshot', required=True, type=Path)
    worker.add_argument('--rank', type=int)
    worker.add_argument('--workers', type=int)
    worker.add_argument('--openfast', default=os.environ.get('MCFAST_OPENFAST'))
    worker.add_argument('--turbsim', default=os.environ.get('MCFAST_TURBSIM'))
    status = commands.add_parser('status', help='Read per-case status from the shared filesystem')
    status.add_argument('--snapshot', required=True, type=Path)
    status.add_argument('--details', action='store_true', help='Include individual case records')
    args = parser.parse_args()
    try:
        if args.command == 'submit':
            if args.study_file:
                if args.workspace or args.study:
                    parser.error('Use a study file or --workspace and --study, not both')
                path = args.study_file.expanduser().resolve()
                if path.parent.name != 'studies' or path.suffix != '.json':
                    parser.error('Choose a saved WORKSPACE/studies/STUDY.json file')
                args.workspace_root = path.parents[2]
                args.workspace, args.study = path.parents[1].name, path.stem
            elif not args.workspace or not args.study:
                parser.error('Provide a saved study file or --workspace and --study')
            submission = submit_study(args.workspace_root, args.workspace, args.study,
                                      nodes=args.nodes, workers_per_node=args.workers_per_node,
                                      account=args.account, partition=args.partition,
                                      wall_time=args.time, memory_per_cpu=args.mem_per_cpu,
                                      array=args.array, dry_run=args.dry_run)
            if args.dry_run:
                print(shlex.join(submission['command']))
            else:
                print(f"Submitted Slurm job {submission['job_id']}")
            print(f"Snapshot: {submission['snapshot']}")
        elif args.command == 'prepare':
            print(prepare_snapshot(args.workspace_root, args.workspace, args.study))
        elif args.command == 'status':
            summary = snapshot_status(args.snapshot)
            if not args.details:
                summary.pop('cases')
            print(json.dumps(summary, indent=2))
        else:
            rank, workers = worker_identity()
            raise SystemExit(run_worker(args.snapshot, args.rank if args.rank is not None else rank,
                                        args.workers if args.workers is not None else workers,
                                        args.openfast, args.turbsim))
    except Exception as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
