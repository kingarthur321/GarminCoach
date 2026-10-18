"""Bounded, source-preserving Garmin training import. No workout or profile writes."""
import copy
import re
import statistics
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .core import Invalid, atomic_json, digest, evidence_set, iso_date, local_timestamp, now, number, require, validate_bundle, weekly_distance
from .mcp import collect
from .dashboard import CATEGORIES, CONTEXT_METRICS, METRICS, empty_details, observation, saved_details, validate_details


def days_between(start, end):
    current = iso_date(start)
    while current <= iso_date(end):
        yield current.isoformat()
        current += timedelta(days=1)


def stage_training(store, client, days=42, report=lambda *_: None):
    require(type(days) is int and 1 <= days <= 42, 'Training lookback must be 1–42 days')
    with store.lock():
        bundle = store.current()
        require(bundle and bundle['activities']['source']=='garmin', 'Training import requires a saved Garmin snapshot')
        profile = store.profile()
        expected = {key:digest(store.read(key)) for key in ('current','dashboard_details','athlete_profile','feedback','workout_registry')}
    end = datetime.now(ZoneInfo(profile['timezone'])).date().isoformat()
    start = (iso_date(end)-timedelta(days=days-1)).isoformat()
    staged = {'fetched_at':now(),'start':start,'end':end,'expected':expected,'observations':None,
              'current':{},'readiness':{},'load_trends':[],'vo2_trends':[],'resting_hr':{},'routes':{},'failures':[]}
    folder = store.path / 'imports' / ('training-'+datetime.now().strftime('%Y%m%dT%H%M%S'))
    consecutive_failures = 0
    def read(name, args):
        nonlocal consecutive_failures
        if name not in client.schemas:
            staged['failures'].append({'tool':name,'arguments':args,'reason':'unsupported'})
            return {'empty':True,'missing_reason':'unsupported'}
        try:
            response = client.call(name,args)
            atomic_json(folder/(name+'-'+(args.get('date') or args.get('start_date'))+'.json'),response)
            consecutive_failures = 0
            return response
        except Invalid:
            staged['failures'].append({'tool':name,'arguments':args,'reason':'read_failed'})
            consecutive_failures += 1
            require(consecutive_failures < 3, 'Repeated Garmin failures; source archive and prior outputs retained')
            return {'empty':True,'missing_reason':'read_failed'}
    report('Refreshing the bounded activity and sleep snapshot')
    staged['observations'] = collect(client,profile)
    # Fetch detail only for new IDs; never repeat the verified GPS import.
    known = saved_details(store,bundle)['activities']
    from .routes import fetch_route
    for activity in sorted(staged['observations']['activities']['items'],key=lambda a:a['started_at'] or a['local_date'],reverse=True)[:50]:
        if activity['id'] not in known:
            staged['routes'][activity['id']] = fetch_route(client,activity)
    for name in ('get_training_status','get_training_load_balance','get_recovery_time_remaining'):
        staged['current'][name] = read(name,{'date':end})
    for index,day in enumerate(days_between(start,end),1):
        staged['readiness'][day] = read('get_training_readiness',{'date':day})
        if index%7==0 or index==days:report(f'Readiness: {index}/{days} days read')
    for offset in range(0,days,14):
        first = (iso_date(start)+timedelta(days=offset)).isoformat()
        last = min(end,(iso_date(first)+timedelta(days=13)).isoformat())
        for tool,target in (('get_training_load_trend','load_trends'),('get_vo2max_trend','vo2_trends')):
            staged[target].append({'start':first,'end':last,'response':read(tool,{'start_date':first,'end_date':last})})
        report(f'Load/VO₂ history: {min(offset+14,days)}/{days} days read')
    nights=staged['observations']['wellness']['items']
    for index,night in enumerate(nights,1):
        day=night['date']
        response=read('get_rhr_day',{'date':day})
        staged['resting_hr'][day]={'tool':'get_rhr_day','response':response}
        if response.get('empty'):
            staged['resting_hr'][day]={'tool':'get_heart_rates_summary','response':read('get_heart_rates_summary',{'date':day})}
        if index%7==0 or index==len(nights):report(f'Resting heart rate: {index}/{len(nights)} days read')
    staged.update(fetched_at=now(),archive=str(folder.relative_to(store.path)))
    atomic_json(store.path/'pending/training-import.json',staged)
    report('Source responses saved privately; ready for validation')
    return staged


def clean_status(value):
    require(isinstance(value,str) and 0 < len(value) <= 150, 'Invalid Garmin status text')
    return re.sub(r'_\d+$','',value).lower()


def metric_observation(value, key, day, tool, *, context=False, bounds=None, method=None):
    unit,cap = (CONTEXT_METRICS if context else METRICS)[key]
    if value is not None:
        if cap is not None:
            number(value, key, 0, cap)
        else:
            value = clean_status(value)
    item = observation(value,unit,day,tool,bounds,missing_reason='not_reported' if value is None else None)
    item['kind'] = 'garmin_estimate' if key=='vo2_max' else 'garmin_reported'
    if method is not None:item['source_method']=method
    return item


def bounds_for(row, low, high):
    lo,hi = row.get(low),row.get(high)
    if lo is None or hi is None:return None
    number(lo,'target minimum',0,100000);number(hi,'target maximum',0,100000)
    require(lo < hi,'Garmin target range is reversed')
    return [lo,hi]


def normalize_training(staged, previous, bundle, timezone):
    require(staged and iso_date(staged['start']) <= iso_date(staged['end']), 'Invalid staged training coverage')
    require((iso_date(staged['end'])-iso_date(staged['start'])).days < 42, 'Training coverage exceeds 42 days')
    start,end = staged['start'],staged['end']
    details=copy.deepcopy(previous)
    if details['source']=='unavailable':details=empty_details()
    details.update(source='garmin',fetched_at=staged['fetched_at'])
    known={a['id'] for a in bundle['activities']['items']}
    details['activities']={k:v for k,v in details['activities'].items() if k in known}
    details['activities'].update(staged.get('routes',{}))
    history={key:{item['date']:copy.deepcopy(item) for item in previous['history'].get(key,[]) if start <= item['date'] <= end} for key in METRICS}
    extra={key:{item['date']:copy.deepcopy(item) for item in previous.get('context_history',{}).get(key,[]) if start <= item['date'] <= end} for key in ('chronic_load','load_ratio','load_status')}
    current_readiness={}
    seen_load=set();seen_vo2=set()
    skipped_vo2=0
    for query_day,response in staged['readiness'].items():
        require(start <= query_day <= end,'Readiness query outside coverage')
        if isinstance(response,dict) and response.get('empty'):
            item=metric_observation(None,'readiness',query_day,'get_training_readiness')
            item['missing_reason']=response.get('missing_reason','not_reported')
        else:
            require(isinstance(response,list) and len(response)<=50,'Invalid readiness response')
            rows=[r for r in response if r.get('score') is not None]
            for row in rows:require(row.get('date')==query_day,'Readiness source date mismatch')
            row=max(rows,key=lambda r:r.get('timestamp',''),default={})
            item=metric_observation(row.get('score'),'readiness',query_day,'get_training_readiness')
            if row.get('timestamp'):
                item.update(original_timestamp=row['timestamp'])
                item['recorded_at'],item['timezone_basis']=local_timestamp(row['timestamp'],timezone)
                item['source_id']=row['timestamp']
            if query_day==end:
                current_readiness=row
        if item['value'] is not None or query_day not in history['readiness']:
            history['readiness'][query_day]=item
    for part in staged['load_trends']:
        response=part['response']
        if response.get('empty'):continue
        require(response.get('start_date')==part['start'] and response.get('end_date')==part['end'],'Load history range mismatch')
        require(isinstance(response.get('trend'),list) and len(response['trend'])<=42,'Invalid load history')
        for row in response['trend']:
            day=row.get('date');require(part['start'] <= day <= part['end'] and start <= day <= end,'Load source date outside coverage')
            require(day not in seen_load,'Duplicate load history date');seen_load.add(day)
            history['acute_load'][day]=metric_observation(row.get('atl'),'acute_load',day,'get_training_load_trend')
            phrase=row.get('training_status')
            history['training_status'][day]=metric_observation(phrase,'training_status',day,'get_training_load_trend')
            if phrase:history['training_status'][day]['raw_value']=phrase
            for key,field in (('chronic_load','ctl'),('load_ratio','acwr'),('load_status','acwr_status')):
                bounds=bounds_for(row,'optimal_chronic_load_min','optimal_chronic_load_max') if key=='chronic_load' and row.get(field) is not None else None
                extra[key][day]=metric_observation(row.get(field),key,day,'get_training_load_trend',context=True,bounds=bounds)
    for part in staged['vo2_trends']:
        response=part['response']
        if response.get('empty'):continue
        require(response.get('start_date')==part['start'] and response.get('end_date')==part['end'],'VO₂ history range mismatch')
        require(isinstance(response.get('trend'),list) and len(response['trend'])<=90,'Invalid VO₂ history')
        # The tool may select cycling for accounts with more cycling observations.
        if response.get('sport')!='running':continue
        for row in response['trend']:
            day=row.get('date');require(start <= day <= end and part['start'] <= day <= part['end'],'VO₂ date outside coverage')
            if row.get('carried_forward'):
                skipped_vo2+=1;continue
            require(row.get('source') in ('get_max_metrics','get_training_status'),'Unknown VO₂ source method')
            require(day not in seen_vo2,'Duplicate VO₂ history date');seen_vo2.add(day)
            history['vo2_max'][day]=metric_observation(row.get('vo2_max'),'vo2_max',day,'get_vo2max_trend',method=row['source'])
    current=staged['current'].get('get_training_status',{})
    if not current.get('empty'):
        day=current.get('date');require(start <= day <= end,'Current status date outside coverage')
        phrase=current.get('training_status_feedback')
        if phrase is None and current.get('training_status') is not None:phrase='Garmin status '+str(current['training_status'])
        for key,value in (('acute_load',current.get('acute_load')),('training_status',phrase)):
            item=metric_observation(value,key,day,'get_training_status')
            if key=='training_status' and phrase:item['raw_value']=phrase
            history[key][day]=item
        for key,field in (('chronic_load','chronic_load'),('load_ratio','load_ratio'),('load_status','acwr_status')):
            bounds=bounds_for(current,'optimal_chronic_load_min','optimal_chronic_load_max') if key=='chronic_load' and current.get(field) is not None else None
            extra[key][day]=metric_observation(current.get(field),key,day,'get_training_status',context=True,bounds=bounds)
    details['history']={key:[items.get(day,metric_observation(None,key,day,'get_training_readiness' if key=='readiness' else 'get_vo2max_trend' if key=='vo2_max' else 'get_training_load_trend')) for day in days_between(start,end)] for key,items in history.items()}
    # Sparse/carry-forward VO₂ days remain gaps, never become new observations.
    for key,items in details['history'].items():
        latest=next((item for item in reversed(items) if item['value'] is not None),None)
        details['training'][key]=copy.deepcopy(latest or items[-1])
    if details['training']['vo2_max']['value'] is None and not current.get('empty') and current.get('sport')=='RUNNING':
        value=current.get('vo2_max_precise',current.get('vo2_max'))
        if value is not None:details['training']['vo2_max']=metric_observation(value,'vo2_max',current['date'],'get_training_status')
    details['context_history']={key:[items.get(day,metric_observation(None,key,day,'get_training_load_trend',context=True)) for day in days_between(start,end)] for key,items in extra.items()}
    details['context']={key:copy.deepcopy(next((item for item in reversed(items) if item['value'] is not None),items[-1])) for key,items in details['context_history'].items()}
    for key,field in (('readiness_level','level'),('readiness_feedback','feedback'),('weekly_hrv','hrv_weekly_avg'),('sleep_factor','sleep_factor_percent'),('recovery_factor','recovery_factor_percent'),('load_factor','training_load_factor_percent'),('hrv_factor','hrv_factor_percent'),('stress_history_factor','stress_history_factor_percent'),('sleep_history_factor','sleep_history_factor_percent')):
        details['context'][key]=metric_observation(current_readiness.get(field),key,end,'get_training_readiness',context=True)
    balance=staged['current'].get('get_training_load_balance',{})
    day=balance.get('date',end);require(start <= day <= end,'Load-focus source date outside coverage')
    details['load_focus']={'start':None,'end':day,'period':'trailing_month','source_tool':'get_training_load_balance','categories':{}}
    for key,source in zip(CATEGORIES,('aerobic_low','aerobic_high','anaerobic')):
        band=balance.get(source,{})
        value=band.get('load');bounds=bounds_for(band,'target_min','target_max') if value is not None else None
        if value is not None:number(value,'load focus',0,100000)
        details['load_focus']['categories'][key]=observation(value,'load',day,'get_training_load_balance',bounds,missing_reason='not_reported' if value is None else None)
    details['context']['load_focus_feedback']=metric_observation(balance.get('feedback'),'load_focus_feedback',day,'get_training_load_balance',context=True)
    recovery=staged['current'].get('get_recovery_time_remaining',{})
    day=recovery.get('date',end);require(start <= day <= end,'Recovery source date outside coverage')
    item=metric_observation(recovery.get('remaining_hours'),'recovery_hours',day,'get_recovery_time_remaining',context=True,method=recovery.get('source'))
    if recovery.get('source')=='recent_activity':item['kind']='derived_from_activity'
    details['context']['recovery_hours']=item
    return validate_details(details,bundle),skipped_vo2


def resting_hr(response,tool,day):
    if response.get('empty'):return None
    if tool=='get_heart_rates_summary':
        require(response.get('date')==day,'Heart-rate source date mismatch')
        value=response.get('resting_heart_rate_bpm')
    else:
        require(tool=='get_rhr_day','Unsupported resting HR tool')
        rows=(response.get('allMetrics') or {}).get('metricsMap',{}).get('WELLNESS_RESTING_HEART_RATE',[])
        require(isinstance(rows,list) and len(rows)<=14,'Invalid resting HR response')
        for row in rows:require(row.get('calendarDate')==day,'Resting HR source date mismatch')
        require(len(rows)<=1,'Duplicate resting HR date')
        value=rows[0].get('value') if rows else None
    if value is None:return None
    return number(value,'resting heart rate',20,150)


def factual_summary(bundle,profile):
    activities,wellness=bundle['activities'],bundle['wellness']
    runs=activities['items'];weeks=weekly_distance(activities);observations=[]
    if runs:
        total=sum(a['distance_km'] or 0 for a in runs)
        observations.append({'kind':'derived','text':f'{len(runs)} recorded runs total {total:.1f} km in the requested activity window.','references':['activity:'+a['id'] for a in runs]})
    else:
        observations.append({'kind':'measured','text':'No running activities were returned in the bounded request.','references':['profile:goal']})
    for key,label,unit in (('sleep_seconds','Sleep duration','h'),('sleep_score','Sleep score','/100'),('hrv_ms','Overnight HRV','ms'),('resting_hr_bpm','Resting heart rate','bpm')):
        measured=[(n['date'],n['values'][key]['value']) for n in wellness['items'] if n['values'][key]['value'] is not None]
        if measured:
            value=statistics.median([v for _,v in measured])/(3600 if key=='sleep_seconds' else 1)
            observations.append({'kind':'derived','text':f'{label} was reported on {len(measured)}/{len(wellness["items"])} days; median {value:.1f} {unit}.','references':['wellness:'+day+':'+key for day,_ in measured]})
    uncertainty=['This is a data-import overview; the saved training plan was retained and awaits coaching review.',
                 'Garmin scores and VO₂ values are wearable estimates, not diagnoses or performance guarantees.']
    if not profile['profile_confirmed']:uncertainty.append('The goal, availability and current illustrative plan still use an unconfirmed example profile.')
    return {'source':'garmin','generated_at':now(),'headline':f'{len(runs)} saved runs; Garmin observations imported through {activities["coverage"]["end"]}.','observations':observations,'uncertainty':uncertainty}


def apply_training(store,staged):
    require(isinstance(staged,dict),'No staged training import')
    with store.lock():
        for key,value in staged['expected'].items():
            require(digest(store.read(key))==value,'Saved inputs changed during import; prior outputs retained')
        before=store.current();profile=store.profile();previous=saved_details(store,before)
        bundle=copy.deepcopy(before)
        bundle.update(copy.deepcopy(staged['observations']))
        require(bundle['activities']['source']=='garmin' and bundle['wellness']['source']=='garmin','Import source mismatch')
        require(bundle['wellness']['coverage']['status']!='read_failed','Sleep refresh failed; prior snapshot retained')
        for night in bundle['wellness']['items']:
            saved=staged['resting_hr'].get(night['date'])
            if saved:
                value=resting_hr(saved['response'],saved['tool'],night['date'])
                if value is not None:night['values']['resting_hr_bpm'].update(value=value,source_tool=saved['tool'],missing_reason=None)
        details,skipped=normalize_training(staged,previous,bundle,profile['timezone'])
        bundle['coach_summary']=factual_summary(bundle,profile)
        bundle['feedback']=store.read('feedback')
        validate_bundle(bundle,profile,before,details)
        require(digest(bundle['training_plan'])==digest(before['training_plan']),'Import must preserve the saved plan')
        available={key:sum(i['value'] is not None for i in rows) for key,rows in details['history'].items()}
        missing=[{'field':key,'reason':item['missing_reason']} for key,item in details['training'].items() if item['value'] is None]
        missing.append({'field':'acute_load_target_range','reason':'Pinned MCP reports chronic-load targets; an acute target range is not exposed. The chronic and focus ranges are preserved separately.'})
        report={'source':'garmin','status':'complete' if not staged['failures'] else 'complete_with_read_failures','fetched_at':now(),
                'coverage':{'activities':bundle['activities']['coverage'],'wellness':bundle['wellness']['coverage'],'training':{'start':staged['start'],'end':staged['end']}},
                'runs':len(bundle['activities']['items']),'detailed_runs':len(details['activities']),'wellness_days':len(bundle['wellness']['items']),
                'wellness_fields':{key:sum(n['values'][key]['value'] is not None for n in bundle['wellness']['items']) for key in ('sleep_seconds','sleep_score','hrv_ms','resting_hr_bpm')},
                'training_days_reported':available,'load_focus_categories':sum(i['value'] is not None for i in details['load_focus']['categories'].values()),
                'unavailable':missing,'failed_reads':staged['failures'],'excluded_carried_forward_vo2_points':skipped,
                'plan_preserved':True,'profile_confirmed':profile['profile_confirmed'],'garmin_writes':0,'scheduler_enabled':profile['automation']['enabled']}
        backup={key:store.read(key) for key in ('current','run_status','dashboard_details','garmin_import_report')}
        try:
            store.commit(bundle,details=details)
            store.write('dashboard_details',details)
            store.write('garmin_import_report',report)
            store.mark(review_required=True,last_successful_read=report['fetched_at'],data_imported_at=report['fetched_at'])
        except BaseException:
            for key,value in backup.items():
                if value is None:(store.path/(key+'.json')).unlink(missing_ok=True)
                else:store.write(key,value)
            raise
        atomic_json(store.path/'verification/training-import.json',{'report':report,'source_archive':staged['archive'],
                    'previous_snapshot':backup['current'],'saved_snapshot':store.read('current'),
                    'plan_hash_before':digest(before['training_plan']),'plan_hash_after':digest(bundle['training_plan']),
                    'routes_preserved':{key:digest(value)==digest(details['activities'].get(key)) for key,value in previous['activities'].items() if key in details['activities']},'result':'passed'})
    return report
