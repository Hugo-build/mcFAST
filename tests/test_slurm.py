import json
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from mcfast import slurm
from mcfast.studies import atomic_json


def setup(tmp_path, count=7):
    root = tmp_path / 'workspaces'
    directory = root / 'example'
    project = directory / 'project'
    project.mkdir(parents=True)
    (project / 'Example.fst').write_text('3 NumBl - blades\n')
    atomic_json(directory / 'workspace.json', {'workspace_id': 'example', 'entry': 'Example.fst'})
    atomic_json(directory / 'studies/sweep.json', {
        'study_id': 'sweep', 'workspace_id': 'example', 'name': 'Sweep',
        'variables': [{'name': 'blades', 'file': 'Example.fst', 'key': 'NumBl'}],
        'samples': [{'blades': i + 2} for i in range(count)],
    })
    return root, slurm.prepare_snapshot(root, 'example', 'sweep')


def test_all_cases_have_exactly_one_owner():
    groups = [list(slurm.case_indices(103, rank, 80)) for rank in range(80)]
    assert sorted(index for group in groups for index in group) == list(range(103))
    assert max(map(len, groups)) == 2
    assert list(slurm.case_indices(2, 9, 10)) == []
    for rank, workers in [(-1, 3), (3, 3), (0, 0)]:
        with pytest.raises(ValueError):
            slurm.case_indices(10, rank, workers)


def test_workers_snapshot_isolation_failure_continuation_and_status(tmp_path, monkeypatch):
    root, snapshot = setup(tmp_path)
    (root / 'example/project/Example.fst').write_text('99 NumBl - edited after snapshot\n')
    gate = threading.Barrier(3)
    lock = threading.Lock()
    seen, active = [], {'current': 0, 'peak': 0}
    def execute(project, manifest, executable, turbsim, results, run_id, metadata, phase):
        value = int((project / 'Example.fst').read_text().split()[0])
        with lock:
            seen.append(value); active['current'] += 1
            active['peak'] = max(active['peak'], active['current'])
        if metadata['sample_index'] < 3:
            gate.wait(timeout=5)
        phase('openfast')
        time.sleep(.01)
        with lock:
            active['current'] -= 1
        if value == 3:
            raise RuntimeError('intentional failure')
        output = results / run_id
        atomic_json(output / 'manifest.json', {**metadata, 'run_id': run_id, 'status': 'completed', 'return_code': 0})
        return 0, output
    monkeypatch.setattr(slurm, 'execute_pipeline', execute)
    with ThreadPoolExecutor(max_workers=3) as pool:
        codes = list(pool.map(lambda rank: slurm.run_worker(snapshot, rank, 3, '/fake/openfast'), range(3)))
    assert codes == [0, 1, 0]
    assert sorted(seen) == list(range(2, 9))
    assert active['peak'] == 3
    summary = slurm.snapshot_status(snapshot)
    assert summary['counts'] == {'completed': 6, 'failed': 1, 'not_started': 0}
    assert len(list((root / 'example/results').glob('*/manifest.json'))) == 7
    assert (root / 'example/project/Example.fst').read_text().startswith('99 ')
    assert len({case['run_id'] for case in summary['cases']}) == 7
    assert all(case['target'] for case in summary['cases'])
    previous = list(seen)
    assert slurm.run_worker(snapshot, 0, 3, '/fake/openfast') == 1
    assert seen == previous
    assert slurm.snapshot_status(snapshot) == summary


def test_nonzero_solver_exit_is_recorded(tmp_path, monkeypatch):
    root, snapshot = setup(tmp_path, 1)
    def execute(project, manifest, executable, turbsim, results, run_id, metadata, phase):
        output = results / run_id
        atomic_json(output / 'manifest.json', {'status': 'failed', 'return_code': 2})
        return 2, output
    monkeypatch.setattr(slurm, 'execute_pipeline', execute)
    assert slurm.run_worker(snapshot, 0, 1, '/fake/openfast') == 1
    record = slurm.snapshot_status(snapshot)['cases'][0]
    assert record['return_code'] == 2 and record['status'] == 'failed'


def test_empty_study_and_invalid_binding_are_rejected(tmp_path):
    root, _ = setup(tmp_path)
    path = root / 'example/studies/sweep.json'
    study = json.loads(path.read_text())
    study['variables'][0]['key'] = 'missing'
    atomic_json(path, study)
    with pytest.raises(ValueError, match='binding'):
        slurm.prepare_snapshot(root, 'example', 'sweep')
    study['variables'][0]['key'] = 'NumBl'; study['samples'] = []
    atomic_json(path, study)
    with pytest.raises(Exception, match='complete case'):
        slurm.prepare_snapshot(root, 'example', 'sweep')


def test_bad_rank_and_missing_solver_do_not_claim_cases(tmp_path, monkeypatch):
    _, snapshot = setup(tmp_path, 1)
    monkeypatch.setattr(slurm, 'find_openfast', lambda: None)
    with pytest.raises(ValueError, match='rank'):
        slurm.run_worker(snapshot, 2, 2)
    with pytest.raises(ValueError, match='OpenFAST'):
        slurm.run_worker(snapshot, 0, 1)
    assert slurm.run_worker(snapshot, 1, 2) == 0
    assert slurm.snapshot_status(snapshot)['counts'] == {'not_started': 1}


def test_script_launches_one_cpu_per_worker_and_waits_for_all_workers():
    script = (Path(__file__).resolve().parents[1] / 'scripts/mcfast-study.slurm').read_text()
    assert '--nodes=10' in script and '--ntasks-per-node=8' in script
    assert '--kill-on-bad-exit=0 --wait=0' in script
    assert '-m mcfast.slurm worker' in script


def test_slurm_script_with_independent_worker_processes(tmp_path):
    """Exercise the shell/CLI/pipeline together with a local srun substitute."""
    import os
    import subprocess
    import sys

    root, snapshot = setup(tmp_path, 7)
    binaries = tmp_path / 'bin'; binaries.mkdir()
    native = binaries / 'fake-openfast'
    native.write_text(f'#!{sys.executable}\n' + '''
import pathlib, sys, time
if sys.argv[1] == '-v':
    print('OpenFAST-v4.2.1 test executable')
    sys.exit(0)
source = pathlib.Path(sys.argv[1])
value = int(source.read_text().split()[0])
time.sleep(.05)
source.with_suffix('.out').write_text('fake solver result')
print(f'case value {value}')
sys.exit(1 if value == 3 else 0)
''')
    native.chmod(0o755)
    launcher = binaries / 'srun'
    launcher.write_text(f'#!{sys.executable}\n' + '''
import os, subprocess, sys
args = sys.argv[1:]
count = int(next(value.split('=')[1] for value in args if value.startswith('--ntasks=')))
while args[0].startswith('--'):
    args.pop(0)
children = [subprocess.Popen(args, env={**os.environ, 'SLURM_PROCID': str(rank), 'SLURM_NTASKS': str(count)}) for rank in range(count)]
sys.exit(max(child.wait() for child in children))
''')
    launcher.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / 'scripts/mcfast-study.slurm'
    result = subprocess.run(['bash', str(script), str(snapshot)], env={
        **os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
        'MCFAST_PYTHON': sys.executable, 'MCFAST_OPENFAST': str(native),
        'SLURM_JOB_NUM_NODES': '2', 'SLURM_NTASKS_PER_NODE': '2', 'SLURM_JOB_ID': 'test123',
    }, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'Starting 4 case workers across 2 nodes (2 per node)' in result.stdout
    summary = slurm.snapshot_status(snapshot)
    assert summary['counts'] == {'completed': 6, 'failed': 1, 'not_started': 0}
    assert {case['worker_rank'] for case in summary['cases']} == {0, 1, 2, 3}
    assert all(case['slurm_job_id'] == 'test123' for case in summary['cases'])
    assert len(list((root / 'example/results').glob('*/Example.out'))) == 7
