"""Read recorded GPS/series for saved runs only; never updates the plan or Garmin."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from coach.core import Store, Invalid, atomic_json, now
from coach.mcp import MCP
from coach.routes import import_routes

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--limit',type=int,default=12)
parser.add_argument('--inspect',action='store_true',help='Inspect first saved run schema into private pending state')
args=parser.parse_args()
store=Store()
try:
    with MCP() as client:
        if args.inspect:
            bundle=store.current()
            activity=sorted(bundle['activities']['items'],key=lambda a:a['started_at'] or a['local_date'],reverse=True)[0]
            page=client.call('get_activity_fit_messages',{'activity_id':activity['id'],'message_types':['session','record'],'include_records':True,'message_limit':5000})
            atomic_json(store.path/'pending/route-inspection.json',page)
            print(json.dumps({'source':page.get('source'),'message_count':len(page.get('messages',[])),'pagination':page.get('pagination')}))
            for kind in ('session','record'):
                sample=next((m for m in page.get('messages',[]) if m['type']==kind),None)
                if sample:
                    print(kind,json.dumps([{'name':f['name'],'units':f.get('units'),'type':type(f['value']).__name__} for f in sample['fields']]))
        else:
            results=import_routes(store,client,args.limit)
            atomic_json(store.path/'verification/route-import.json',{'recorded_at':now(),'data':'Real Garmin original FIT records','activities':results,'result':'passed'})
            print(json.dumps({'runs':len(results),'gps_samples':sum(r['gps_samples'] for r in results),'splits':sum(r['splits'] for r in results),'result':'passed'}))
except (Invalid,OSError,ValueError,KeyError,TypeError):
    print('Route read failed; prior saved route/plan retained. Check the local MCP connection.',file=sys.stderr)
    sys.exit(1)
