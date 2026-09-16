"""Launch exactly one Lambda GPU instance through the API, on the controller Mac.

Persists inventory and the launch request before sending it, reconciles an
ambiguous launch against provider inventory by the unique session name instead
of retrying blindly, waits for capacity with bounded backoff, and never creates
or attaches a filesystem. The API key stays in the environment; nothing here
prints it. Termination belongs to scripts.gpu_inference_watchdog.
"""
import argparse, json, os, sys, time, uuid
from pathlib import Path
import httpx

API = 'https://cloud.lambda.ai/api/v1/'


def main(a):
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    key = os.environ['LAMBDA_API_KEY']

    def save(name, data):
        (out / name).write_text(json.dumps(data, indent=2, default=str))

    with httpx.Client(base_url=API, auth=(key, ''), timeout=60, headers={'User-Agent': 'forkloop-live-paired-provision'}) as c:
        before = c.get('instances').json()['data']
        save('instances-before.json', before)
        types = c.get('instance-types').json()['data']
        save('instance-types.json', types)
        keys = c.get('ssh-keys').json()['data']
        save('ssh-keys.json', [{k: v for k, v in r.items() if k != 'private_key'} for r in keys])
        save('filesystems-before.json', c.get('file-systems').json()['data'])
        entry = types[a.instance_type]
        cents = entry['instance_type']['price_cents_per_hour']
        specs = entry['instance_type']['specs']
        if cents > a.max_cents:
            raise SystemExit(f'quoted {cents} cents/h exceeds ceiling {a.max_cents}')
        if specs['gpus'] != 1:
            raise SystemExit('single-GPU instance required')
        if a.ssh_key not in {k['name'] for k in keys}:
            raise SystemExit('registered SSH key not found')
        # Termination route must be reachable and authorized before anything is billable.
        probe = c.post('instance-operations/terminate', json={'instance_ids': []})
        save('terminate-endpoint-probe.json', {'status_code': probe.status_code, 'body': probe.text[:500]})
        if probe.status_code in (401, 403) or probe.status_code == 404 and 'instance' not in probe.text.lower():
            raise SystemExit(f'termination endpoint unavailable: HTTP {probe.status_code}')
        request = {'region_name': a.region, 'instance_type_name': a.instance_type, 'ssh_key_names': [a.ssh_key],
                   'name': a.name, 'quantity': 1}
        save('launch-request.json', {'request': request, 'client_request_id': uuid.uuid4().hex, 'price_cents_per_hour': cents,
                                     'specs': specs, 'first_attempt_utc': time.time(), 'capacity_wait_seconds': a.wait_minutes * 60})
        started = time.time()
        attempt = 0
        instance_id = None
        while instance_id is None:
            attempt += 1
            regions = [r['name'] for r in c.get('instance-types').json()['data'][a.instance_type]['regions_with_capacity_available']]
            row = {'utc': time.time(), 'attempt': attempt, 'regions_with_capacity': regions}
            if a.region in regions:
                try:
                    r = c.post('instance-operations/launch', json=request)
                    row.update(status_code=r.status_code, body=r.json())
                    if r.status_code == 200:
                        ids = r.json()['data']['instance_ids']
                        if len(ids) != 1:
                            raise SystemExit(f'launch returned {len(ids)} instances; reconcile manually')
                        instance_id = ids[0]
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    row.update(ambiguous=f'{type(e).__name__}: {e}')
                if instance_id is None:
                    # Ambiguous or failed: reconcile by unique name before any further launch.
                    time.sleep(5)
                    mine = [i for i in c.get('instances').json()['data'] if i.get('name') == a.name]
                    row['reconciled'] = [i['id'] for i in mine]
                    if len(mine) > 1:
                        raise SystemExit(f'duplicate instances named {a.name}: {[i["id"] for i in mine]}')
                    if mine:
                        instance_id = mine[0]['id']
            with (out / 'launch-attempts.jsonl').open('a') as f:
                f.write(json.dumps(row, default=str) + '\n')
            print(json.dumps({k: row.get(k) for k in ['attempt', 'status_code', 'regions_with_capacity', 'ambiguous', 'reconciled']}), flush=True)
            if instance_id is None:
                body = row.get('body') or {}
                code = (body.get('error') or {}).get('code', '') if isinstance(body, dict) else ''
                capacity = a.region not in regions or 'capacity' in code
                if not capacity:
                    raise SystemExit(f'launch refused: {code or row.get("status_code")}')
                if time.time() - started > a.wait_minutes * 60:
                    raise SystemExit('capacity unavailable within the bounded wait; no instance launched')
                time.sleep(min(90, 30 * attempt))
        save('instance-id.json', {'instance_id': instance_id, 'name': a.name, 'launch_request_utc': started, 'launched_utc': time.time(),
                                  'region': a.region, 'instance_type': a.instance_type, 'price_cents_per_hour': cents})
        print(json.dumps({'instance_id': instance_id, 'name': a.name}), flush=True)
        while True:
            d = c.get('instances/' + instance_id).json()['data']
            save('instance-status.json', d)
            if d['status'] == 'active' and d.get('ip'):
                break
            if d['status'] in ('terminated', 'terminating', 'unhealthy'):
                raise SystemExit(f'instance entered {d["status"]} before becoming active')
            if time.time() - started > 1200:
                raise SystemExit('instance did not become active within 20 minutes; watchdog must terminate it')
            time.sleep(15)
        save('instance-active.json', d)
        print(json.dumps({'instance_id': d['id'], 'ip': d['ip'], 'status': d['status'], 'name': d.get('name'), 'region': d['region']['name'],
                          'type': d['instance_type']['name'], 'file_system_names': d.get('file_system_names')}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--region', default='us-west-3')
    p.add_argument('--instance-type', default='gpu_1x_h100_pcie')
    p.add_argument('--max-cents', type=int, default=329)
    p.add_argument('--ssh-key', required=True)
    p.add_argument('--wait-minutes', type=float, default=20)
    main(p.parse_args())
