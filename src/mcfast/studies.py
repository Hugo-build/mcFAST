"""Persisted local study coordinator shared by the API and CLI."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid

from .models import safe_path, referenced_files
from .parser import parse_file, update_file
from .pipeline import execute_pipeline
from .runner import find_openfast, find_turbsim

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]+')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def usable_cpus() -> int:
    return len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)


class Capacity:
    def __init__(self):
        self.limit = usable_cpus()
        self.busy = 0
        self.condition = threading.Condition()

    @contextmanager
    def slot(self):
        with self.condition:
            self.condition.wait_for(lambda: self.busy < self.limit)
            self.busy += 1
        try:
            yield
        finally:
            with self.condition:
                self.busy -= 1
                self.condition.notify_all()


LOCAL_CAPACITY = Capacity()


def prepare_case(source: Path, study: dict, manifest: dict, index: int,
                 destination: Path) -> Path:
    """Copy lazily, then apply samples and rebase all discoverable references."""
    shutil.copytree(source, destination)
    updates: dict[str, dict] = {}
    for variable in study['variables']:
        updates.setdefault(variable['file'], {})[variable['key']] = study['samples'][index][variable['name']]
    for file, values in updates.items():
        update_file(safe_path(destination, file), values)
    originals = Path(manifest.get('snapshot_source_root', str(source)))
    text_files = []
    for path in destination.rglob('*'):
        if path.is_symlink():
            raise ValueError('Case contains a symlink')
        if path.is_file() and path.suffix.lower() in {'.fst', '.dat', '.txt', '.in', '.inp', '.yaml', '.yml'}:
            text_files.append(path)
    # Rebase before walking the graph: absolute links would otherwise hide
    # their linked modules from the graph of the copied project.
    for path in text_files:
        rewrites = {}
        for parameter in parse_file(path)['parameters']:
            value = parameter['value']
            if not isinstance(value, str):
                continue
            prefix = '@' if parameter['key'] == 'NumCoords' and value.startswith('@') else ''
            reference = Path(value[1:] if prefix else value)
            if reference.is_absolute() and (reference == originals or originals in reference.parents):
                new_path = destination / reference.relative_to(originals)
                rewrites[parameter['key']] = prefix + os.path.relpath(new_path, path.parent)
        if rewrites:
            update_file(path, rewrites)
    relevant = {path.relative_to(destination).as_posix() for path in text_files}
    if manifest.get('entry'):
        relevant = {node['path'] for node in referenced_files(destination, manifest['entry'])['files']}
        relevant.update(variable['file'] for variable in study['variables'])
        if manifest.get('wind', {}).get('turbsim_input'):
            relevant.add(manifest['wind']['turbsim_input'])
    selected_wind = manifest.get('wind', {}).get('turbsim_input')
    if selected_wind and selected_wind in updates:
        safe_path(destination, selected_wind).with_suffix('.bts').unlink(missing_ok=True)
    for path in text_files:
        if path.relative_to(destination).as_posix() not in relevant:
            continue
        rewrites = {}
        for parameter in parse_file(path)['parameters']:
            value = parameter['value']
            if not isinstance(value, str) or not value or value.lower() in {'unused', 'none', 'default'}:
                continue
            prefix = '@' if parameter['key'] == 'NumCoords' and value.startswith('@') else ''
            referenced = Path(value[1:] if prefix else value)
            if referenced.suffix.lower() in {'.dll', '.so', '.dylib'}:
                key = path.relative_to(destination).as_posix() + ':' + parameter['key']
                native = referenced if referenced.is_absolute() else path.parent / referenced
                if not native.is_file():
                    raise ValueError(f'Missing native library for {key}: {native}')
                continue
            candidate = (referenced if referenced.is_absolute() else path.parent / referenced).resolve()
            is_reference = parameter.get('reference') or referenced.suffix.lower() == '.bts' or parameter['key'] in {'PotFile', 'HydroFile'}
            if is_reference and candidate != destination.resolve() and destination.resolve() not in candidate.parents:
                raise ValueError(f'External data reference in {path.name}:{parameter["key"]}: {value}')
        if rewrites:
            update_file(path, rewrites)
    return destination


class StudyCoordinator:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()
        self.batches: dict[tuple[str, str], dict] = {}
        self.running: set[tuple[str, str]] = set()
        self.recover()

    def directory(self, workspace: str, batch: str) -> Path:
        if not IDENTIFIER.fullmatch(workspace) or not IDENTIFIER.fullmatch(batch):
            raise ValueError('Invalid workspace or batch identifier')
        return self.root / workspace / 'batches' / batch

    def save(self, batch: dict) -> None:
        atomic_json(self.directory(batch['workspace_id'], batch['batch_id']) / 'batch.json', batch)

    def recover(self) -> None:
        for path in self.root.glob('*/batches/*/batch.json'):
            try:
                batch = json.loads(path.read_text())
                for case in batch['cases']:
                    case.pop('_claimed', None)
                    if case['status'] not in TERMINAL:
                        self.fail_case(batch, case, 'Coordinator restarted; retry explicitly', status='interrupted')
                self.batches[(batch['workspace_id'], batch['batch_id'])] = batch
                self.finish_status(batch)
                self.save(batch)
            except (OSError, ValueError, KeyError):
                continue

    def target_list(self) -> list[dict]:
        return [{'target_id': 'local', 'name': 'This computer', 'mode': 'local', 'ready': bool(find_openfast()),
                 'cpu_capacity': LOCAL_CAPACITY.limit, 'busy': LOCAL_CAPACITY.busy,
                 'recommended_slots': max(1, LOCAL_CAPACITY.limit - 1)}]

    def validate(self, slots: dict[str, int], study: dict) -> None:
        if not slots or any(type(n) is not int or n < 1 for n in slots.values()):
            raise ValueError('Select at least one worker; slot counts must be positive integers')
        bindings = [(v['file'], v['key']) for v in study['variables']]
        if len(bindings) != len(set(bindings)):
            raise ValueError('Two variables cannot bind the same file and parameter')
        if set(slots) != {'local'}:
            raise ValueError('Only local simulation workers are supported')
        if slots['local'] > LOCAL_CAPACITY.limit or not find_openfast():
            raise ValueError('Local workers exceed CPU capacity or OpenFAST is unavailable')

    def launch(self, workspace: str, study: dict, slots: dict, manifest: dict,
               snapshot: Path | None = None, retry_of: str | None = None) -> dict:
        with self.lock:
            self.validate(slots, study)
            if any(w == workspace and b['status'] not in TERMINAL for (w, _), b in self.batches.items()):
                raise ValueError('This workspace already has an active batch')
            identifier = 'batch-' + uuid.uuid4().hex[:12]
            directory = self.directory(workspace, identifier)
            directory.mkdir(parents=True)
            try:
                source = snapshot or self.root / workspace / 'project'
                if any(path.is_symlink() for path in source.rglob('*')):
                    raise ValueError('Project contains an unsupported symlink')
                shutil.copytree(source, directory / 'project')
                frozen_manifest = {**manifest, 'snapshot_source_root': manifest.get('snapshot_source_root', str(source.resolve()))}
                atomic_json(directory / 'workspace.json', frozen_manifest)
                atomic_json(directory / 'study.json', study)
                batch = {'batch_id': identifier, 'workspace_id': workspace, 'study_id': study['study_id'],
                         'study_name': study['name'], 'started_at': now(), 'status': 'running', 'stop_requested': False,
                         'slots': slots,
                         'retry_of': retry_of, 'cases': [
                             {'sample_index': study.get('source_indices', list(range(len(study['samples']))))[i],
                              'study_index': i, 'run_id': identifier + '-case-' + str(i + 1), 'status': 'queued',
                              'phase': 'queued', 'target': None}
                             for i in range(len(study['samples']))]}
                self.batches[(workspace, identifier)] = batch
                self.save(batch)
                self.start(batch)
                return self.view(workspace, identifier)
            except Exception:
                shutil.rmtree(directory)
                raise

    def start(self, batch: dict) -> None:
        key = (batch['workspace_id'], batch['batch_id'])
        if key in self.running:
            return
        self.running.add(key)
        def coordinate():
            threads = []
            for target, slots in batch['slots'].items():
                for _ in range(min(slots, len(batch['cases']))):
                    thread = threading.Thread(target=self.worker, args=(batch, target), daemon=True)
                    thread.start()
                    threads.append(thread)
            for thread in threads:
                thread.join()
            with self.lock:
                self.finish_status(batch)
                self.save(batch)
                self.running.discard(key)
        threading.Thread(target=coordinate, name='study-' + batch['batch_id'], daemon=True).start()

    def finish_status(self, batch: dict) -> None:
        statuses = {case['status'] for case in batch['cases']}
        if statuses <= TERMINAL:
            batch['status'] = 'failed' if statuses & {'failed', 'interrupted'} else 'cancelled' if 'cancelled' in statuses else 'completed'
            batch.setdefault('finished_at', now())

    def change(self, batch: dict, case: dict, **values) -> None:
        with self.lock:
            case.update(values)
            self.save(batch)

    def worker(self, batch: dict, target: str) -> None:
        while True:
            with self.lock:
                if batch['stop_requested']:
                    return
                case = next((c for c in batch['cases'] if c['status'] == 'queued'), None)
                if case is None:
                    return
                case.update(target=target, status='running', phase='preparation')
                self.save(batch)
            try:
                with LOCAL_CAPACITY.slot():
                    if batch['stop_requested']:
                        self.change(batch, case, status='cancelled', phase='cancelled')
                        continue
                    self.local_case(batch, case)
            except Exception as exc:
                self.fail_case(batch, case, str(exc))

    def metadata(self, batch: dict, case: dict) -> dict:
        return {'batch_id': batch['batch_id'], 'study_id': batch['study_id'], 'study_name': batch['study_name'],
                'sample_index': case['sample_index'], 'target': case['target'],
                'sample_values': json.loads((self.directory(batch['workspace_id'], batch['batch_id']) / 'study.json').read_text())['samples'][case['study_index']]}

    def local_case(self, batch: dict, case: dict) -> None:
        directory = self.directory(batch['workspace_id'], batch['batch_id'])
        study = json.loads((directory / 'study.json').read_text())
        manifest = json.loads((directory / 'workspace.json').read_text())
        case_root = directory / 'cases' / case['run_id'] / 'project'
        root = prepare_case(directory / 'project', study, manifest, case['study_index'], case_root)
        code, output = execute_pipeline(root, manifest, find_openfast(), find_turbsim(),
                                         self.root / batch['workspace_id'] / 'results', case['run_id'],
                                         self.metadata(batch, case), lambda value: self.change(batch, case, phase=value))
        payload = json.loads((output / 'manifest.json').read_text())
        payload.update(model=manifest['entry'], phase='complete' if code == 0 else 'failed')
        atomic_json(output / 'manifest.json', payload)
        self.change(batch, case, status='completed' if code == 0 else 'failed', phase=payload['phase'], return_code=code)

    def fail_case(self, batch: dict, case: dict, error: str, status: str = 'failed') -> None:
        output = self.root / batch['workspace_id'] / 'results' / case['run_id']
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'console.log').open('a', encoding='utf-8') as log:
            log.write('\nmcFAST case failed: ' + error + '\n')
        manifest = json.loads((self.directory(batch['workspace_id'], batch['batch_id']) / 'workspace.json').read_text())
        atomic_json(output / 'manifest.json', {**self.metadata(batch, case), 'workspace_id': batch['workspace_id'],
                    'run_id': case['run_id'], 'model': manifest['entry'], 'workspace_entry': manifest['entry'],
                    'status': status, 'phase': status, 'error': error, 'return_code': None,
                    'started_at': batch['started_at'], 'finished_at': now(), 'outputs': []})
        self.change(batch, case, status=status, phase=status, error=error)

    def view(self, workspace: str, identifier: str, page: int = 1, page_size: int = 50) -> dict:
        with self.lock:
            batch = self.batches[(workspace, identifier)]
            self.finish_status(batch)
            counts: dict[str, int] = {}
            target_counts: dict[str, dict] = {}
            for case in batch['cases']:
                counts[case['status']] = counts.get(case['status'], 0) + 1
                if case['target']:
                    target_count = target_counts.setdefault(case['target'], {'running': 0})
                    if case['status'] not in TERMINAL:
                        target_count['running'] += 1
            start = (page - 1) * page_size
            summary = {key: value for key, value in batch.items() if key != 'cases'}
            return {**summary, 'counts': counts, 'target_counts': target_counts, 'total': len(batch['cases']), 'page': page, 'page_size': page_size,
                    'cases': [{key: value for key, value in case.items() if key != '_claimed'} for case in batch['cases'][start:start + page_size]]}

    def stop(self, workspace: str, identifier: str) -> dict:
        with self.lock:
            batch = self.batches[(workspace, identifier)]
            batch['stop_requested'] = True
            for case in batch['cases']:
                if case['status'] == 'queued':
                    case.update(status='cancelled', phase='cancelled')
            self.save(batch)
            return self.view(workspace, identifier)

    def retry(self, workspace: str, identifier: str, slots: dict) -> dict:
        with self.lock:
            old = self.batches[(workspace, identifier)]
            if old['status'] not in TERMINAL:
                raise ValueError('Wait for the batch to finish before retrying')
            directory = self.directory(workspace, identifier)
            study = json.loads((directory / 'study.json').read_text())
            failed = [case for case in old['cases'] if case['status'] in {'failed', 'interrupted'}]
            if not failed:
                raise ValueError('No failed or interrupted cases to retry')
            study['samples'] = [study['samples'][case['study_index']] for case in failed]
            study['source_indices'] = [case['sample_index'] for case in failed]
            manifest = json.loads((directory / 'workspace.json').read_text())
            return self.launch(workspace, study, slots, manifest, directory / 'project', identifier)
