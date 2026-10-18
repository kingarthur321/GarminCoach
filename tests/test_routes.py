import copy
import tempfile
import unittest
from coach.core import Store, Invalid, digest
from coach.fixtures import make_demo
from coach.dashboard import demo_details, validate_details
from coach.routes import normalize_fit, fetch_route, thin_samples, import_routes


def message(kind, values):
    return {'type':kind,'fields':[{'name':key,'value':value,'units':unit} for key,(value,unit) in values.items()]}


def records():
    return [message('session',{'start_time':('2026-10-01T07:00:00',None),'total_elapsed_time':(1800,'s')}),
            message('record',{'timestamp':('2026-10-01T07:00:00',None),'position_lat':(2**30,'semicircles'),'position_long':(2**29,'semicircles'),'heart_rate':(143,'bpm'),'distance':(0,'m'),'enhanced_speed':(3.33333333333333,'m/s'),'enhanced_altitude':(35,'m')}),
            message('record',{'timestamp':('2026-10-01T07:30:00',None),'position_lat':(2**30+1000,'semicircles'),'position_long':(2**29+1000,'semicircles'),'distance':(5000,'m'),'heart_rate':(150,'bpm')})]


class RoutesCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.store.bootstrap()
        bundle=make_demo(self.store.profile());self.fixture=copy.deepcopy(bundle);bundle['activities']['source']='garmin';bundle['wellness']['source']='garmin'
        for key in ('coach_summary','training_plan'):bundle[key]['source']='garmin'
        for night in bundle['wellness']['items']:
            for value in night['values'].values():value['source']='garmin'
        for activity in bundle['activities']['items']:
            activity['source']='garmin'
        with self.store.lock():self.store.commit(bundle)
        self.bundle=self.store.current();self.activity=self.bundle['activities']['items'][0]
        self.splits={'activity_id':self.activity['id'],'laps':[{'distance_meters':1000,'duration_seconds':300,'avg_hr_bpm':143}]}

    def test_fit_units_coordinates_elapsed_and_missing_values(self):
        detail=normalize_fit(records(),self.activity,self.splits)
        self.assertEqual(detail['samples'][0]['latitude'],90)
        self.assertEqual(detail['samples'][0]['longitude'],45)
        self.assertAlmostEqual(detail['samples'][0]['pace_seconds_per_km'],300)
        self.assertEqual(detail['samples'][1]['distance_km'],5)
        self.assertIsNone(detail['samples'][1]['latitude'])  # Invalid latitude does not become a fake point.
        self.assertIsNone(detail['samples'][1]['longitude'])
        self.assertEqual(detail['elapsed_duration_seconds'],1800)
        self.assertEqual(detail['splits'][0]['distance_km'],1)
        self.assertEqual(detail['splits'][0]['duration_seconds'],300)

    def test_unknown_units_wrong_id_and_duplicate_timestamps(self):
        data=records();data[1]['fields'][1]['units']='unknown'
        with self.assertRaises(Invalid):normalize_fit(data,self.activity,self.splits)
        wrong=copy.deepcopy(self.splits);wrong['activity_id']='unknown'
        with self.assertRaises(Invalid):normalize_fit(records(),self.activity,wrong)
        data=records();data.insert(2,copy.deepcopy(data[1]))
        self.assertEqual(len(normalize_fit(data,self.activity,self.splits)['samples']),2)

    def test_sample_thinning_preserves_endpoints_and_gps_gap(self):
        template=normalize_fit(records(),self.activity,self.splits)['samples'][0]
        points=[{**template,'time_seconds':i} for i in range(10000)]
        points[5001].update(latitude=None,longitude=None)
        result=thin_samples(points)
        self.assertEqual(len(result),4000)
        self.assertEqual(result[0]['time_seconds'],0);self.assertEqual(result[-1]['time_seconds'],9999)
        gap=next(i for i,p in enumerate(result) if p['time_seconds']==5001)
        self.assertEqual(result[gap-1]['time_seconds'],5000);self.assertEqual(result[gap+1]['time_seconds'],5002)

    def test_bounded_pagination_and_source_matching(self):
        activity=self.activity;calls=[]
        class Client:
            def call(self,name,args):
                calls.append((name,args))
                if name=='get_activity_splits':return {'empty':True}
                offset=args['message_offset']
                return {'source':'garmin_original_fit','activity_id':activity['id'],'messages':records()[:1] if offset==0 else records()[1:],'pagination':{'next_offset':1 if offset==0 else None}}
        result=fetch_route(Client(),activity)
        self.assertEqual(len(result['samples']),2)
        self.assertEqual(calls[1][1]['message_offset'],1)
        class Wrong:
            def call(self,*_):return {'source':'garmin_original_fit','activity_id':'other','messages':[]}
        with self.assertRaises(Invalid):fetch_route(Wrong(),activity)

    def test_import_preserves_snapshot_profile_and_training_signals(self):
        before=digest(self.store.current());profile=digest(self.store.profile())
        existing=demo_details(self.fixture);existing['source']='garmin'
        self.store.write('dashboard_details',existing)
        class Client:
            def call(self,name,args):
                if name=='get_activity_splits':return {'empty':True}
                return {'source':'garmin_original_fit','activity_id':args['activity_id'],'messages':records(),'pagination':{'next_offset':None}}
        result=import_routes(self.store,Client(),limit=1)
        self.assertEqual(len(result),1)
        self.assertEqual(digest(self.store.current()),before);self.assertEqual(digest(self.store.profile()),profile)
        self.assertEqual(self.store.read('dashboard_details')['training'],existing['training'])
        validate_details(self.store.read('dashboard_details'),self.bundle)

    def test_failed_read_keeps_previous_sidecar(self):
        previous={'test':'existing private observation'};self.store.write('dashboard_details',previous)
        class Broken:
            def call(self,*_):raise Invalid('Read failed')
        with self.assertRaises(Invalid):import_routes(self.store,Broken(),limit=1)
        self.assertEqual(self.store.read('dashboard_details'),previous)
