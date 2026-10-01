import json

import pytest
from fastapi.testclient import TestClient

from mcfast import api
from mcfast.results import read_results, result_metadata, result_series
from test_playback import write_output


@pytest.mark.parametrize('binary', [False, True])
def test_general_output(tmp_path, binary):
    write_output(tmp_path / ('Example.outb' if binary else 'Example.out'), binary=binary)
    result = read_results(tmp_path, 'Example.fst')
    assert result_metadata(result)['sample_count'] == 3
    assert result_series(result, ['RotTorq'], 1, 2)['channels']['RotTorq'] == {'unit': 'kN-m', 'values': [2, 2]}
    assert result_series(result, ['RotTorq'], 1, 2)['timestamps'] == [1, 2]
    for channels, start, end in [(['Unknown'], 0, 2), ([], 0, 2), (['RotTorq'], 2, 1), (['RotTorq'], -1, 2), (['RotTorq'], 0, float('nan'))]:
        with pytest.raises(ValueError):
            result_series(result, channels, start, end)


def test_arbitrary_channels_and_gaps(tmp_path):
    path = tmp_path / 'Example.out'
    write_output(path)
    path.write_text(path.read_text().replace('Azimuth', 'Custom').replace('(deg)', '(furlong)').replace('350.0', 'nan'))
    result = read_results(tmp_path, 'Example.fst')
    assert result_series(result, ['Custom'])['channels']['Custom'] == {'unit': 'furlong', 'values': [None, 10, 30]}


def test_invalid_and_preferred_source(tmp_path):
    with pytest.raises(ValueError, match='missing'):
        read_results(tmp_path, 'Example.fst')
    write_output(tmp_path / 'Example.out')
    write_output(tmp_path / 'Example.outb', binary=True)
    assert read_results(tmp_path, 'Example.fst')[0] == 'Example.outb'
    (tmp_path / 'Example.outb').unlink()
    write_output(tmp_path / 'Example.out', times=(0, 0, 2))
    with pytest.raises(ValueError, match='increasing'):
        read_results(tmp_path, 'Example.fst')
    (tmp_path / 'Example.out').write_text('corrupt')
    with pytest.raises(ValueError, match='Cannot read'):
        read_results(tmp_path, 'Example.fst')


def test_endpoints_and_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'WORKSPACE_ROOT', tmp_path)
    for name in ['one', 'two']:
        workspace = tmp_path / name
        workspace.mkdir()
        (workspace / 'workspace.json').write_text(json.dumps({'workspace_id': name, 'entry': 'Example.fst'}))
    run = tmp_path / 'one/results/run-1'
    run.mkdir(parents=True)
    manifest = {'workspace_id': 'one', 'run_id': 'run-1', 'model': 'Example.fst', 'status': 'completed'}
    (run / 'manifest.json').write_text(json.dumps(manifest))
    write_output(run / 'Example.out')
    with TestClient(api.app) as client:
        url = '/api/workspaces/one/runs/run-1/results'
        assert client.get(url).json()['available']
        assert client.delete(url + '/cache').json() == {'released': True}
        assert client.delete(url.replace('/one/', '/two/') + '/cache').status_code == 404
        assert client.get(url + '/series?channel=RotTorq&start=1&end=2').json()['timestamps'] == [1, 2]
        assert client.get(url + '/series?channel=Unknown').status_code == 422
        assert client.get(url.replace('/one/', '/two/')).status_code == 404
        manifest['status'] = 'running'
        (run / 'manifest.json').write_text(json.dumps(manifest))
        assert not client.get(url).json()['available']
        manifest['status'] = 'completed'
        (run / 'manifest.json').write_text(json.dumps(manifest))
        (run / 'Example.out').unlink()
        write_output(tmp_path / 'Example.out')
        (run / 'Example.out').symlink_to(tmp_path / 'Example.out')
        assert 'outside' in client.get(url).json()['reason']


def test_duplicate_channel_names_are_selectable(tmp_path):
    path = tmp_path / 'Example.out'
    write_output(path)
    path.write_text(path.read_text().replace('Azimuth', 'Invalid').replace('RotSpeed', 'Invalid'))
    result = read_results(tmp_path, 'Example.fst')
    channels = result_metadata(result)['channels']
    assert channels[0]['name'] == 'Invalid [column 2]'
    assert channels[1]['name'] == 'Invalid [column 3]'
    assert result_series(result, ['Invalid [column 2]'])['channels']['Invalid [column 2]']['values'] == [350, 10, 30]


def test_shared_lazy_store_and_release(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from mcfast import output_store
    from mcfast.playback import read_playback
    write_output(tmp_path / 'Example.outb', binary=True)
    original = output_store.OutputStore
    loads = []

    def counted(source):
        loads.append(source)
        return original(source)

    monkeypatch.setattr(output_store, 'OutputStore', counted)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: read_results(tmp_path, 'Example.fst'), range(4)))
    store = results[0][2]['Azimuth'].store
    assert len(loads) == 1
    assert not store.decoded  # Metadata never decompresses channels.
    assert all(result[2]['Azimuth'].store is store for result in results)
    a = results[0][2]['RotSpeed']['values']
    assert results[1][2]['RotSpeed']['values'] is a
    read_playback(tmp_path, 'Example.fst')
    assert len(loads) == 1
    output_store.release_store(tmp_path)
    assert results[0][2]['RotSpeed']['values'] is a  # In-flight references remain safe.
    assert read_results(tmp_path, 'Example.fst')[2]['Azimuth'].store is not store
    assert len(loads) == 2


@pytest.mark.parametrize('file_id', [1, 2, 4])
def test_compressed_binary_formats(tmp_path, file_id):
    import struct
    import numpy as np
    width = 16 if file_id == 4 else 10
    path = tmp_path / 'Example.outb'
    with path.open('wb') as f:
        f.write(struct.pack('<h', file_id))
        if file_id == 4:
            f.write(struct.pack('<h', width))
        f.write(struct.pack('<ii', 2, 3))
        f.write(struct.pack('<dd', 10, 5) if file_id == 1 else struct.pack('<dd', 0, 1))
        f.write(np.array([2, 4], dtype='<f4').tobytes())
        f.write(np.array([10, -8], dtype='<f4').tobytes())
        f.write(struct.pack('<i', 0))
        for headers in [('Time', 'Custom', 'Other'), ('(s)', '(m)', '(kW)')]:
            for header in headers:
                f.write(header.encode().ljust(width, b' '))
        if file_id == 1:
            f.write(np.array([5, 15, 25], dtype='<i4').tobytes())
        f.write(np.array([[12, 0], [14, 4], [16, 8]], dtype='<i2').tobytes())
    result = read_results(tmp_path, 'Example.fst')
    assert result_series(result, ['Custom', 'Other'])['channels']['Custom']['values'] == [1, 2, 3]
    assert result_series(result, ['Other'], 1, 2)['channels']['Other']['values'] == [3, 4]
    assert result_series(result, ['Custom'])['timestamps'] == [0, 1, 2]
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match='Truncated|length'):
        read_results(tmp_path, 'Example.fst')


def test_cache_budget_and_file_replacement(tmp_path, monkeypatch):
    from mcfast import output_store
    write_output(tmp_path / 'Example.outb', binary=True)
    monkeypatch.setattr(output_store, '_CHANNEL_BUDGET', 24)
    result = read_results(tmp_path, 'Example.fst')
    store = result[2]['Azimuth'].store
    old = result[2]['Azimuth']['values']
    result[2]['RotSpeed']['values']
    assert store.decoded_bytes <= 24
    assert list(store.decoded) == [1]
    assert old.tolist() == [350, 10, 30]
    write_output(tmp_path / 'Example.outb', azimuth=(1, 2, 3), binary=True)
    updated = read_results(tmp_path, 'Example.fst')
    assert updated[2]['Azimuth'].store is not store
    assert updated[2]['Azimuth']['values'].tolist() == [1, 2, 3]
