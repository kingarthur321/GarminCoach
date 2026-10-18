"""Import recorded routes through the pinned MCP's bounded original-FIT tool."""
import copy
import math
from datetime import datetime, timezone
from .core import Invalid, digest, now, number, require
from .dashboard import empty_details, saved_details, validate_details

FIT_TOOL = 'get_activity_fit_messages'
MAX_PAGES = 5
PAGE_SIZE = 5000


def fields(message):
    require(isinstance(message.get('fields'), list), 'Invalid FIT message fields')
    result = {}
    for field in message['fields']:
        if isinstance(field, dict) and isinstance(field.get('name'), str):
            if field['name'] not in result or (result[field['name']].get('value') is None and field.get('value') is not None):
                result[field['name']] = field
    return result


def scalar(data, names, units, low, high):
    for name in names:
        field = data.get(name, {})
        value = field.get('value')
        if value is None:
            continue
        require(field.get('units') in units, 'Unexpected FIT field unit: ' + name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            return None
        return value
    return None


def instant(data, name):
    value = data.get(name, {}).get('value')
    if value is None:
        return None
    require(isinstance(value, str), 'FIT timestamp must be decoded ISO text')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Invalid('Invalid FIT timestamp') from None
    # FIT profile timestamps are UTC; fitparse emits naive UTC datetimes.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def coordinate(data, name, cap):
    field = data.get(name, {})
    value = field.get('value')
    if value is None:
        return None
    units = field.get('units')
    require(units in ('semicircles', 'degrees'), 'Unexpected FIT coordinate unit')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    degrees = value * 180 / 2**31 if units == 'semicircles' else value
    return degrees if -cap <= degrees <= cap else None


def thin_samples(samples, maximum=4000):
    if len(samples) <= maximum:
        return samples
    essential = {0, len(samples)-1}
    for index in range(1, len(samples)):
        if (samples[index]['latitude'] is None) != (samples[index-1]['latitude'] is None):
            essential.update((index-1, index))
    require(len(essential) <= maximum, 'Too many GPS gaps for bounded route storage')
    remaining = [i for i in range(len(samples)) if i not in essential]
    count = maximum - len(essential)
    chosen = {remaining[round(i*(len(remaining)-1)/max(1,count-1))] for i in range(count)}
    return [samples[i] for i in sorted(essential | chosen)]


def normalize_fit(messages, activity, splits):
    sessions = [fields(m) for m in messages if m.get('type') == 'session']
    require(len(sessions) <= 1, 'Multiple FIT sessions need explicit activity matching')
    recorded = [fields(m) for m in messages if m.get('type') == 'record']
    times = [instant(r, 'timestamp') for r in recorded]
    start = instant(sessions[0], 'start_time') if sessions else None
    start = start or next((t for t in times if t is not None), None)
    samples, previous, previous_distance = [], -1, -1
    for record, time in zip(recorded, times):
        if time is None or start is None:
            continue
        elapsed = (time-start).total_seconds()
        if elapsed <= previous or not 0 <= elapsed <= 172800:
            continue  # Device duplicate/untimed records do not become invented samples.
        previous = elapsed
        distance = scalar(record, ('distance',), ('m',), 0, 300000)
        distance = distance/1000 if distance is not None else None
        if distance is not None and distance < previous_distance:
            distance = None
        if distance is not None:
            previous_distance = distance
        latitude, longitude = coordinate(record, 'position_lat', 90), coordinate(record, 'position_long', 180)
        if latitude is None or longitude is None:
            latitude = longitude = None
        speed = scalar(record, ('enhanced_speed','speed'), ('m/s',), 0, 50)
        pace = 1000/speed if speed and 20 <= 1000/speed <= 3600 else None
        samples.append({'time_seconds': elapsed, 'distance_km': distance, 'latitude': latitude, 'longitude': longitude,
                        'heart_rate_bpm': scalar(record, ('heart_rate',), ('bpm',), 1, 250),
                        'pace_seconds_per_km': pace,
                        'elevation_m': scalar(record, ('enhanced_altitude','altitude'), ('m',), -500, 10000)})
    elapsed = scalar(sessions[0], ('total_elapsed_time',), ('s',), 0, 172800) if sessions else None
    elapsed = elapsed if elapsed is not None else (samples[-1]['time_seconds'] if samples else activity['duration_seconds'])
    laps = []
    require(splits.get('empty') or str(splits.get('activity_id')) == activity['id'], 'Split activity ID mismatch')
    for row in splits.get('laps', [])[:300]:
        km, seconds = row.get('distance_meters'), row.get('duration_seconds')
        if km is None or seconds is None or km <= 0 or seconds <= 0:
            continue
        laps.append({'index':len(laps)+1,'distance_km':number(km,'lap distance',.001,300000)/1000,
                     'duration_seconds':number(seconds,'lap duration',.01,172800),
                     'avg_hr_bpm':number(row.get('avg_hr_bpm'),'lap heart rate',0,250,nullable=True)})
    return {'source_tool':FIT_TOOL,'source_id':activity['id'],'samples':thin_samples(samples),'splits':laps,
            'elapsed_duration_seconds':elapsed,'record_count':len(recorded)}


def fetch_route(client, activity):
    messages, offset = [], 0
    for _ in range(MAX_PAGES):
        page = client.call(FIT_TOOL, {'activity_id':activity['id'],'message_types':['session','record'],
                                    'include_records':True,'message_offset':offset,'message_limit':PAGE_SIZE})
        require(page.get('source') == 'garmin_original_fit' and str(page.get('activity_id')) == activity['id'], 'Original FIT activity ID/source mismatch')
        require(isinstance(page.get('messages'), list) and len(page['messages']) <= PAGE_SIZE, 'Invalid bounded FIT page')
        messages.extend(page['messages'])
        following = page.get('pagination',{}).get('next_offset')
        if following is None:
            break
        require(isinstance(following,int) and following > offset, 'FIT pagination did not advance')
        offset = following
    else:
        raise Invalid('FIT record limit reached; previous route retained')
    splits = client.call('get_activity_splits', {'activity_id':activity['id']})
    return normalize_fit(messages, activity, splits)


def import_routes(store, client, limit=12):
    require(isinstance(limit,int) and 1 <= limit <= 50, 'Route import limit must be 1–50')
    with store.lock():
        bundle = store.current()
        require(bundle and bundle['activities']['source']=='garmin', 'Recorded routes require an existing Garmin snapshot')
        fingerprint = digest(bundle)
        sidecar_fingerprint = digest(store.read('dashboard_details'))
        details = copy.deepcopy(saved_details(store,bundle))
        if details['source']=='unavailable':
            details = empty_details()
        details.update(source='garmin',fetched_at=now())
        activities = sorted(bundle['activities']['items'],key=lambda a:a['started_at'] or a['local_date'],reverse=True)[:limit]
    results = []
    for activity in activities:
        detail = fetch_route(client,activity)
        details['activities'][activity['id']] = detail
        validate_details(details,bundle)
        results.append({'activity_id':activity['id'],'record_count':detail['record_count'],'saved_samples':len(detail['samples']),
                        'gps_samples':sum(p['latitude'] is not None for p in detail['samples']),'splits':len(detail['splits'])})
    with store.lock():
        require(digest(store.current())==fingerprint,'Snapshot changed during route import; previous data retained')
        require(digest(store.read('dashboard_details'))==sidecar_fingerprint,'Supplemental observations changed; route import retained previous state')
        store.write('dashboard_details',validate_details(details,bundle))
    return results
