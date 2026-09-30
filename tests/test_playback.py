import json
from pathlib import Path
import struct

from fastapi.testclient import TestClient
import numpy as np
import pytest

from mcfast import api
from mcfast.playback import read_playback


def write_output(path, times=(0, 1, 2), azimuth=(350, 10, 30), binary=False):
    names = ["Time", "Azimuth", "RotSpeed", "PtfmSurge", "RotTorq"]
    units = ["s", "deg", "rpm", "m", "kN-m"]
    rows = np.array([[t, a, 7.55, 1, 2] for t, a in zip(times, azimuth)])
    if binary:
        # OpenFAST uncompressed binary format (ID 3).
        with path.open('wb') as stream:
            stream.write(struct.pack('<hii', 3, len(names)-1, len(times)))
            stream.write(struct.pack('<dd', times[0], times[1]-times[0]))
            stream.write(struct.pack('<i', 0))
            for values in (names, units):
                for value in values:
                    text = f'({value})' if values is units else value
                    stream.write(text.encode().ljust(10, b' '))
            stream.write(rows[:, 1:].astype('<f8').tobytes())
    else:
        path.write_text('\n'.join(['header'] * 6 + [' '.join(names), ' '.join(f'({u})' for u in units)]) + '\n' +
                        '\n'.join(' '.join(str(v) for v in row) for row in rows) + '\n')


@pytest.mark.parametrize('binary', [False, True])
def test_reader_output_and_units(tmp_path, binary):
    path = tmp_path / ('Example.outb' if binary else 'Example.out')
    write_output(path, binary=binary)
    result = read_playback(tmp_path, 'case/Example.fst')
    assert result['timestamps'] == [0, 1, 2]
    assert result['channels']['Azimuth']['values'] == [350, 10, 30]
    assert result['channels']['RotTorq']['unit'] == 'kN-m'
    assert 'PtfmPitch' in result['missing_channels']


def test_prefers_binary_and_excludes_module_outputs(tmp_path):
    write_output(tmp_path / 'Example.MD.out')
    with pytest.raises(ValueError, match='missing'):
        read_playback(tmp_path, 'Example.fst')
    write_output(tmp_path / 'Example.out')
    write_output(tmp_path / 'Example.outb', binary=True)
    assert read_playback(tmp_path, 'Example.fst')['source'] == 'Example.outb'


@pytest.mark.parametrize('times,azimuth', [((0,0,2),(0,1,2)), ((0,2,1),(0,1,2)), ((0,1,2),(0,float('nan'),2))])
def test_rejects_invalid_samples(tmp_path, times, azimuth):
    write_output(tmp_path / 'Example.out', times, azimuth)
    with pytest.raises(ValueError):
        read_playback(tmp_path, 'Example.fst')


def test_missing_azimuth_units_and_corrupt_file(tmp_path):
    path = tmp_path / 'Example.out'
    write_output(path)
    path.write_text(path.read_text().replace('Azimuth', 'Other'))
    with pytest.raises(ValueError, match='Azimuth'):
        read_playback(tmp_path, 'Example.fst')
    write_output(path)
    path.write_text(path.read_text().replace('(deg)', '(rad)'))
    with pytest.raises(ValueError, match='Azimuth'):
        read_playback(tmp_path, 'Example.fst')
    path.write_text('broken')
    with pytest.raises(ValueError, match='Cannot read'):
        read_playback(tmp_path, 'Example.fst')


def test_playback_endpoint_isolation_and_geometry(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'WORKSPACE_ROOT', tmp_path)
    for workspace in ('one', 'two'):
        directory = tmp_path / workspace
        directory.mkdir()
        (directory / 'workspace.json').write_text(json.dumps({'workspace_id': workspace, 'entry': 'Example.fst'}))
    run = tmp_path / 'one/results/run-1'
    run.mkdir(parents=True)
    manifest = {'workspace_id': 'one', 'run_id': 'run-1', 'model': 'Example.fst',
                'status': 'completed', 'geometry': {'hubHeight': 100, 'platformReferenceZ': 5}}
    (run / 'manifest.json').write_text(json.dumps(manifest))
    write_output(run / 'Example.out')
    with TestClient(api.app) as client:
        url = '/api/workspaces/one/runs/run-1/playback'
        payload = client.get(url).json()
        assert payload['available'] is True
        assert payload['geometry_source'] == 'saved'
        assert payload['geometry']['platformReferenceZ'] == 5
        assert client.get('/api/workspaces/two/runs/run-1/playback').status_code == 404
        manifest['status'] = 'running'
        (run / 'manifest.json').write_text(json.dumps(manifest))
        assert client.get(url).json()['available'] is False
        manifest['status'] = 'completed'
        (run / 'manifest.json').write_text(json.dumps(manifest))
        (run / 'Example.out').unlink()
        assert client.get(url).json()['available'] is False
        assert client.get('/api/workspaces/one/runs/run-1').status_code == 200


def test_invalid_optional_channel_is_unavailable(tmp_path):
    path = tmp_path / 'Example.out'
    write_output(path)
    path.write_text(path.read_text().replace('7.55', 'nan'))
    result = read_playback(tmp_path, 'Example.fst')
    assert 'RotSpeed' in result['missing_channels']
    assert 'RotSpeed' not in result['channels']


def test_rejects_artifact_symlink_outside_run(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    write_output(tmp_path / 'Example.out')
    (run / 'Example.out').symlink_to(tmp_path / 'Example.out')
    with pytest.raises(ValueError, match='outside'):
        read_playback(run, 'Example.fst')


def test_old_run_uses_current_geometry(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'WORKSPACE_ROOT', tmp_path)
    workspace = tmp_path / 'older'
    (workspace / 'project').mkdir(parents=True)
    (workspace / 'workspace.json').write_text(json.dumps({'workspace_id': 'older', 'entry': 'Example.fst'}))
    (workspace / 'project/Example.fst').write_text('120 TowerHt - tower height\n5 PtfmRefzt - platform reference\n')
    run = workspace / 'results/run-1'
    run.mkdir(parents=True)
    (run / 'manifest.json').write_text(json.dumps({'workspace_id': 'older', 'run_id': 'run-1',
                                                'model': 'Example.fst', 'status': 'completed'}))
    write_output(run / 'Example.out')
    with TestClient(api.app) as client:
        result = client.get('/api/workspaces/older/runs/run-1/playback').json()
        assert result['geometry_source'] == 'current'
        assert result['geometry']['hubHeight'] == 120
        assert result['geometry']['platformReferenceZ'] == 5
        assert client.get('/api/workspaces/older/runs/not-found/playback').status_code == 404
