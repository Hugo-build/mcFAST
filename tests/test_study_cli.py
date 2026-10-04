import json
import sys

import pytest

from mcfast import study_cli


def test_cli_dry_run_defaults_to_recommended_local_workers(monkeypatch, capsys):
    calls = []
    def request(server, path, body=None):
        calls.append((server, path, body))
        if body is None:
            return {'targets': [{'recommended_slots': 3}]}
        return {'valid': True, 'slots': body['slots']}
    monkeypatch.setattr(study_cli, 'request', request)
    monkeypatch.setattr(sys, 'argv', ['mcfast-study-run', '--workspace', 'a workspace', '--study', 's', '--dry-run', '--server', 'http://localhost:8123'])
    study_cli.main()
    assert calls[-1] == ('http://localhost:8123', '/api/workspaces/a%20workspace/batches', {'study_id': 's', 'slots': {'local': 3}, 'dry_run': True})
    assert json.loads(capsys.readouterr().out)['valid']


def test_cli_interrupt_stops_dispatch_on_server(monkeypatch):
    calls = []
    def request(server, path, body=None):
        calls.append((path, body))
        if path.endswith('/stop'):
            return {}
        if body is not None:
            return {'batch_id': 'batch-one'}
        raise KeyboardInterrupt
    monkeypatch.setattr(study_cli, 'request', request)
    monkeypatch.setattr(sys, 'argv', ['mcfast-study-run', '--workspace', 'w', '--study', 's', '--local-workers', '2'])
    with pytest.raises(SystemExit) as exc:
        study_cli.main()
    assert exc.value.code == 130
    assert calls[-1] == ('/api/workspaces/w/batches/batch-one/stop', {})
