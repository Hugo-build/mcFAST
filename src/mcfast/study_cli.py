"""Thin command-line client for the application's persisted study coordinator."""
import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def request(server: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(server.rstrip('/') + path, data=data,
                         headers={'Content-Type': 'application/json'}), timeout=120) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description='Run saved study cases through a running mcFAST server')
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--study', required=True)
    parser.add_argument('--local-workers', type=int, default=0, help='Local slots; defaults to recommended capacity')
    parser.add_argument('--server', default='http://127.0.0.1:8000')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        slots = {}
        if args.local_workers < 0:
            parser.error('--local-workers must be non-negative')
        if args.local_workers:
            slots['local'] = args.local_workers
        if not slots:
            local = request(args.server, '/api/simulation/targets')['targets'][0]
            slots['local'] = local['recommended_slots']
        base = '/api/workspaces/' + quote(args.workspace, safe='') + '/batches'
        batch = request(args.server, base, {'study_id': args.study, 'slots': slots, 'dry_run': args.dry_run})
        if args.dry_run:
            print(json.dumps(batch, indent=2))
            return
        path = base + '/' + batch['batch_id']
        print('Batch:', batch['batch_id'], flush=True)
        previous = None
        try:
            while True:
                batch = request(args.server, path)
                progress = json.dumps({'status': batch['status'], 'counts': batch['counts']}, sort_keys=True)
                if progress != previous:
                    print(progress, flush=True)
                    previous = progress
                if batch['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
                    raise SystemExit(0 if batch['status'] == 'completed' else 1)
                time.sleep(2)
        except KeyboardInterrupt:
            request(args.server, path + '/stop', {})
            print('Dispatch stopped; active cases will finish.')
            raise SystemExit(130)
    except HTTPError as exc:
        parser.exit(1, exc.read().decode(errors='replace') + '\n')
    except (URLError, ValueError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
