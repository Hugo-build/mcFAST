import json
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient

from mcfast import api, studies
from mcfast.studies import StudyCoordinator, atomic_json, prepare_case


def setup(tmp_path, monkeypatch):
    root = tmp_path / 'workspaces'
    directory = root / 'example'
    project = directory / 'project'
    project.mkdir(parents=True)
    (project / 'Example.fst').write_text('3 NumBl - blades\n')
    manifest = {'workspace_id': 'example', 'entry': 'Example.fst'}
    atomic_json(directory / 'workspace.json', manifest)
    study = {'study_id': 'sweep', 'workspace_id': 'example', 'name': 'Sweep',
             'variables': [{'name': 'blades', 'file': 'Example.fst', 'key': 'NumBl'}],
             'samples': [{'blades': 2}, {'blades': 3}, {'blades': 4}]}
    atomic_json(directory / 'studies/sweep.json', study)
    monkeypatch.setattr(studies, 'find_openfast', lambda: '/fake/openfast')
    monkeypatch.setattr(studies, 'find_turbsim', lambda: None)
    monkeypatch.setattr(studies.LOCAL_CAPACITY, 'limit', 2)
    return root, manifest, study


def wait(coordinator, batch):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = coordinator.view('example', batch['batch_id'])
        if status['status'] in studies.TERMINAL:
            # Ensure all workers have finished persisting status.
            if ('example', batch['batch_id']) not in coordinator.running:
                return status
        time.sleep(.01)
    raise AssertionError('Batch did not finish')


def pipeline(seen, active, gate=None, fail_value=None):
    lock = threading.Lock()
    def execute(root, manifest, executable, turbsim, results, run_id, metadata, phase):
        value = int((root / 'Example.fst').read_text().split()[0])
        with lock:
            seen.append(value)
            active['current'] += 1
            active['peak'] = max(active['peak'], active['current'])
        if gate:
            gate.wait(5)
        else:
            time.sleep(.05)
        with lock:
            active['current'] -= 1
        if value == fail_value:
            raise RuntimeError('Intentional sample failure')
        output = results / run_id
        atomic_json(output / 'manifest.json', {**metadata, 'workspace_id': manifest['workspace_id'],
                    'run_id': run_id, 'status': 'completed', 'model': manifest['entry'], 'return_code': 0})
        return 0, output
    return execute


def test_parallel_isolation_and_snapshot(tmp_path, monkeypatch):
    root, manifest, study = setup(tmp_path, monkeypatch)
    seen, active, gate = [], {'current': 0, 'peak': 0}, threading.Event()
    monkeypatch.setattr(studies, 'execute_pipeline', pipeline(seen, active, gate))
    coordinator = StudyCoordinator(root)
    batch = coordinator.launch('example', study, {'local': 2}, manifest)
    deadline = time.monotonic() + 3
    while len(seen) < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    assert active['peak'] == 2
    (root / 'example/project/Example.fst').write_text('9 NumBl - edited later\n')
    gate.set()
    final = wait(coordinator, batch)
    assert final['status'] == 'completed'
    assert sorted(seen) == [2, 3, 4]
    assert (root / 'example/project/Example.fst').read_text().startswith('9 ')
    assert len(list((root / 'example/results').glob('*/manifest.json'))) == 3
    assert coordinator.view('example', batch['batch_id'], 2, 2)['cases'][0]['sample_index'] == 2


def test_failure_continues_and_retry_preserves_source_sample(tmp_path, monkeypatch):
    root, manifest, study = setup(tmp_path, monkeypatch)
    seen, active = [], {'current': 0, 'peak': 0}
    monkeypatch.setattr(studies, 'execute_pipeline', pipeline(seen, active, fail_value=3))
    coordinator = StudyCoordinator(root)
    old = wait(coordinator, coordinator.launch('example', study, {'local': 1}, manifest))
    assert old['counts'] == {'completed': 2, 'failed': 1}
    assert active['peak'] == 1
    (root / 'example/project/Example.fst').write_text('9 NumBl - later\n')
    monkeypatch.setattr(studies, 'execute_pipeline', pipeline(seen, active))
    new = wait(coordinator, coordinator.retry('example', old['batch_id'], {'local': 1}))
    assert new['status'] == 'completed'
    assert new['cases'][0]['sample_index'] == 1
    assert seen[-1] == 3
    assert len(list((root / 'example/results').glob('*/manifest.json'))) == 4


def test_stop_allows_active_to_finish_and_rejects_second_batch(tmp_path, monkeypatch):
    root, manifest, study = setup(tmp_path, monkeypatch)
    seen, active, gate = [], {'current': 0, 'peak': 0}, threading.Event()
    monkeypatch.setattr(studies, 'execute_pipeline', pipeline(seen, active, gate))
    coordinator = StudyCoordinator(root)
    batch = coordinator.launch('example', study, {'local': 1}, manifest)
    deadline = time.monotonic() + 3
    while not seen and time.monotonic() < deadline:
        time.sleep(.01)
    with pytest.raises(ValueError, match='active batch'):
        coordinator.launch('example', study, {'local': 1}, manifest)
    coordinator.stop('example', batch['batch_id'])
    gate.set()
    final = wait(coordinator, batch)
    assert final['counts'] == {'completed': 1, 'cancelled': 2}
    assert seen == [2]


def test_recovery_marks_local_interrupted(tmp_path, monkeypatch):
    root, manifest, study = setup(tmp_path, monkeypatch)
    coordinator = StudyCoordinator(root)
    monkeypatch.setattr(coordinator, 'start', lambda _: None)
    batch = coordinator.launch('example', study, {'local': 1}, manifest)
    restored = StudyCoordinator(root).view('example', batch['batch_id'])
    assert restored['status'] == 'failed'
    assert restored['counts'] == {'interrupted': 3}


def test_duplicate_bindings_and_capacity(tmp_path, monkeypatch):
    root, manifest, study = setup(tmp_path, monkeypatch)
    coordinator = StudyCoordinator(root)
    with pytest.raises(ValueError, match='CPU capacity'):
        coordinator.validate({'local': 3}, study)
    with pytest.raises(ValueError, match='Only local'):
        coordinator.validate({'ssh-one': 1}, study)
    study['variables'].append({**study['variables'][0], 'name': 'other'})
    with pytest.raises(ValueError, match='same file'):
        coordinator.validate({'local': 1}, study)


def test_rebase_native_libraries_and_external_references(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    library = tmp_path / 'lib.so'; library.write_bytes(b'library')
    (source / 'Example.fst').write_text(f'"{source}/Servo.dat" ServoFile - linked\n')
    (source / 'Servo.dat').write_text(f'"{library}" DLL_FileName - library\n')
    manifest = {'snapshot_source_root': str(source), 'entry': 'Example.fst'}
    study = {'variables': [], 'samples': [{}]}
    destination = prepare_case(source, study, manifest, 0, tmp_path / 'case')
    assert studies.parse_file(destination / 'Example.fst')['data']['ServoFile'] == 'Servo.dat'
    assert str(library) in (destination / 'Servo.dat').read_text()
    (source / 'Example.fst').write_text('"/external/file.dat" ServoFile - external\n')
    with pytest.raises(ValueError, match='External data'):
        prepare_case(source, study, manifest, 0, tmp_path / 'bad')


def test_api_batch_launch_dry_run_and_result_history(tmp_path, monkeypatch):
    root, manifest, study = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(api, 'WORKSPACE_ROOT', root)
    monkeypatch.setattr(studies, 'execute_pipeline', pipeline([], {'current': 0, 'peak': 0}))
    with TestClient(api.app) as client:
        base = '/api/workspaces/example/batches'
        payload = {'study_id': 'sweep', 'slots': {'local': 2}, 'dry_run': True}
        assert client.post(base, json=payload).json()['valid'] is True
        assert not (root / 'example/batches').exists()
        payload['dry_run'] = False
        response = client.post(base, json=payload)
        assert response.status_code == 202, response.text
        batch = response.json()
        final = wait(api._coordinator(), batch)
        assert final['status'] == 'completed'
        assert client.get(base + '/' + batch['batch_id'] + '?page=0').status_code == 422
        history = client.get('/api/workspaces/example/runs').json()['runs']
        assert len(history) == 3
        assert history[0]['study_name'] == 'Sweep'
        assert client.get(base).json()['batches'][0]['total'] == 3


def test_concurrent_turbsim_generates_per_sample_wind(tmp_path, monkeypatch):
    root, manifest, study = setup(tmp_path, monkeypatch)
    project = root / 'example/project'
    (project / 'Example.fst').write_text('"Inflow.dat" InflowFile - input\n')
    (project / 'Inflow.dat').write_text('3 WindType - binary\n"Wind/Case.bts" FileName_BTS - wind\n')
    (project / 'Wind').mkdir()
    (project / 'Wind/Case.in').write_text('True WrADFF - binary\n10 URef - wind\n')
    (project / 'Wind/Case.bts').write_bytes(b'baseline wind')
    manifest['wind'] = {'turbsim_input': 'Wind/Case.in'}
    study['variables'] = [{'name': 'wind', 'file': 'Wind/Case.in', 'key': 'URef'}]
    study['samples'] = [{'wind': 8}, {'wind': 12}]
    openfast = tmp_path / 'openfast'
    openfast.write_text('''#!/bin/sh
if [ "$1" = "-v" ]; then echo OpenFAST-v4.2.1; exit; fi
[ "$OMP_NUM_THREADS" = 1 ] || exit 9
cat Wind/Case.bts > "${1%.fst}.out"
''')
    openfast.chmod(0o755)
    turbsim = tmp_path / 'turbsim'
    turbsim.write_text('''#!/bin/sh
if [ "$1" = "-v" ]; then echo TurbSim-v4.2.1; exit; fi
[ "$OMP_NUM_THREADS" = 1 ] || exit 9
awk '/URef/ {print $1}' "$1" > "${1%.in}.bts"
''')
    turbsim.chmod(0o755)
    monkeypatch.setattr(studies, 'find_openfast', lambda: str(openfast))
    monkeypatch.setattr(studies, 'find_turbsim', lambda: str(turbsim))
    coordinator = StudyCoordinator(root)
    batch = wait(coordinator, coordinator.launch('example', study, {'local': 2}, manifest))
    assert batch['status'] == 'completed', batch
    outputs = [(root / 'example/results' / case['run_id'] / 'Example.out').read_text().strip() for case in batch['cases']]
    assert outputs == ['8', '12']
    assert (project / 'Wind/Case.bts').read_bytes() == b'baseline wind'

