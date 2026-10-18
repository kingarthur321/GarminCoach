"""Import bounded dashboard training/recovery observations, never Garmin writes."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from coach.core import Store, Invalid, atomic_json, now
from coach.mcp import MCP


def save_tool_schemas(client):
    path=Path('docs/mcp-tools.json')
    existing=json.loads(path.read_text()) if path.exists() else []
    saved={item['name']:item for item in existing}
    for name,schema in client.schemas.items():saved[name]={'name':name,'inputSchema':schema}
    atomic_json(path,list(saved.values()))

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--inspect', action='store_true', help='Inspect current responses privately before normalization')
parser.add_argument('--days', type=int, default=42)
parser.add_argument('--stage', action='store_true', help='Save source responses privately before normalization')
parser.add_argument('--apply-staged', action='store_true', help='Validate/apply already staged source responses, without account reads')
args = parser.parse_args()
store = Store()
try:
    if args.apply_staged:
        from coach.training import apply_training
        print(json.dumps(apply_training(store,store.read('pending/training-import'))))
        sys.exit(0)
    with MCP() as client:
        save_tool_schemas(client)
        if args.inspect:
            day = datetime.now(ZoneInfo(store.profile()['timezone'])).date().isoformat()
            results = {}
            for name in ('get_training_status','get_training_readiness','get_training_load_balance','get_rhr_day','get_heart_rates_summary','get_recovery_time_remaining'):
                response = client.call(name, {'date':day})
                results[name] = response
                print(json.dumps({'tool':name,'shape':type(response).__name__,'keys':list(response) if isinstance(response,dict) else sorted({key for row in response for key in row}) if isinstance(response,list) else [],'records':len(response) if isinstance(response,list) else None}))
            atomic_json(store.path/'pending/training-inspection.json', {'fetched_at':now(),'date':day,'responses':results})
        else:
            from coach.training import stage_training
            staged = stage_training(store,client,args.days,lambda message:print(message,flush=True))
            if not args.stage:
                from coach.training import apply_training
                print(json.dumps(apply_training(store,staged)))
except (Invalid,OSError,ValueError,KeyError,TypeError) as exc:
    reason=str(exc) if isinstance(exc,Invalid) else 'Local import failed; inspect the private staged state.'
    print('Training import retained prior saved data. '+reason,file=sys.stderr)
    sys.exit(1)
