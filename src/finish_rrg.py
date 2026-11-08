"""Continue the authorized one-off RRG reproduction after raw inputs arrive."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/rrg'
STATUS = DATA / 'reproduction_status.json'


def update(phase, **values):
    temporary = STATUS.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'phase': phase,
        'updated_at_utc': datetime.now(timezone.utc).isoformat(), **values},
        ensure_ascii=False, indent=2))
    temporary.replace(STATUS)


def main():
    plan = json.loads((DATA / 'download_plan.json').read_text())
    required = [t for t in plan['tasks'] if t['api'] != 'fund_portfolio'
        or t['params']['ts_code'].endswith(('.SH', '.SZ'))]
    while True:
        pending = [t['file'] for t in required if not (DATA/'raw'/t['file']).exists()
            or not (DATA/'raw'/t['file']).with_suffix('.json').exists()]
        if not pending:
            break
        download = json.loads((DATA/'download_status.json').read_text())
        failures = [f for f in download.get('failures', []) if f.get('file') in pending]
        update('waiting_for_required_inputs', remaining=len(pending), failures=failures)
        if failures:
            return 1
        time.sleep(20)
    environment = {**os.environ, 'MPLBACKEND': 'Agg', 'NUMBA_CACHE_DIR': '/private/tmp/rrg_numba'}
    for action, phase in [('build', 'building_complete_snapshot'), ('reproduce', 'running_upstream_citic_base')]:
        update(phase)
        with (DATA/f'formal_{action}.log').open('w') as log:
            result = subprocess.run([sys.executable, '-u', str(ROOT/'src/rrg_pipeline.py'), action],
                cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            update('requires_execution_review', failed_action=action, exit_code=result.returncode,
                log=str(DATA/f'formal_{action}.log'))
            return result.returncode
    update('reproduction_finished_pending_result_review',
        results=str(ROOT/'results/rrg/citic_base_reproduction'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
