import copy
import tempfile
import unittest
from unittest.mock import patch
from coach.core import Invalid, Store, digest, iso_date, now
from coach.dashboard import demo_details, empty_details, validate_details
from coach.fixtures import make_demo
from coach.training import apply_training, normalize_training, resting_hr, stage_training


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.store.bootstrap()
        fixture=make_demo(self.store.profile());self.fixture=copy.deepcopy(fixture)
        for key in ('activities','wellness','coach_summary','training_plan'):fixture[key]['source']='garmin'
        for activity in fixture['activities']['items']:activity['source']='garmin'
        for night in fixture['wellness']['items']:
            for value in night['values'].values():value['source']='garmin'
        with self.store.lock():self.store.commit(fixture)
        self.bundle=self.store.current()
        details=demo_details(self.fixture);details['source']='garmin'
        # Test-only mock Garmin detail with a measured route, never a real account.
        details['training']=empty_details()['training'];details['history']=empty_details()['history'];details['load_focus']=empty_details()['load_focus']
        for detail in details['activities'].values():detail['source_tool']='mock_original_fit'
        self.store.write('dashboard_details',validate_details(details,self.bundle))
        self.day=self.bundle['activities']['coverage']['end']
        self.staged={'fetched_at':now(),'start':self.day,'end':self.day,
            'expected':{key:digest(self.store.read(key)) for key in ('current','dashboard_details','athlete_profile','feedback','workout_registry')},
            'observations':{key:copy.deepcopy(self.bundle[key]) for key in ('activities','wellness')},
            'current':{'get_training_status':{'date':self.day,'training_status':7,'training_status_feedback':'PRODUCTIVE_3','sport':'RUNNING','acute_load':350,'chronic_load':360,'load_ratio':.9,'acwr_status':'OPTIMAL','optimal_chronic_load_min':280,'optimal_chronic_load_max':540,'vo2_max':55,'vo2_max_precise':54.9},
                       'get_training_load_balance':{'date':self.day,'feedback':'AEROBIC_HIGH_SHORTAGE','aerobic_low':{'load':680,'target_min':430,'target_max':950},'aerobic_high':{'load':450,'target_min':520,'target_max':1040},'anaerobic':{'load':220,'target_min':170,'target_max':520}},
                       'get_recovery_time_remaining':{'date':self.day,'remaining_hours':0,'state':'recovered','source':'training_readiness','change_phrase':'REACHED_ZERO'}},
            'readiness':{self.day:[{'date':self.day,'timestamp':self.day+'T12:42:00','score':56,'level':'MODERATE','sleep_factor_percent':39,'recovery_time_hours':2}, {'date':self.day,'timestamp':self.day+'T06:28:00','score':60}]},
            'load_trends':[{'start':self.day,'end':self.day,'response':{'start_date':self.day,'end_date':self.day,'trend':[{'date':self.day,'atl':350,'ctl':360,'acwr':.9,'training_status':'PRODUCTIVE_3'}]}}],
            'vo2_trends':[{'start':self.day,'end':self.day,'response':{'start_date':self.day,'end_date':self.day,'sport':'running','trend':[{'date':self.day,'vo2_max':54.9,'source':'get_max_metrics'}]}}],
            'resting_hr':{n['date']:{'tool':'get_rhr_day','response':{'allMetrics':{'metricsMap':{'WELLNESS_RESTING_HEART_RATE':[{'calendarDate':n['date'],'value':50}]}}}} for n in self.bundle['wellness']['items']},
            'failures':[],'routes':{},'archive':'imports/synthetic-unit-test'}

    def normalize(self):
        return normalize_training(self.staged,self.store.read('dashboard_details'),self.bundle,self.store.profile()['timezone'])

    def test_latest_readiness_timestamp_and_zero_recovery_are_preserved(self):
        details,_=self.normalize()
        self.assertEqual(details['training']['readiness']['value'],56)
        self.assertTrue(details['training']['readiness']['recorded_at'].endswith('+02:00'))
        self.assertEqual(details['context']['recovery_hours']['value'],0)
        self.assertEqual(details['context']['recovery_hours']['kind'],'garmin_reported')
        self.assertEqual(details['context']['sleep_factor']['value'],39)

    def test_precise_running_vo2_and_separate_load_targets(self):
        details,_=self.normalize()
        self.assertEqual(details['training']['vo2_max']['value'],54.9)
        self.assertEqual(details['training']['training_status']['value'],'productive')
        self.assertIsNone(details['training']['acute_load']['recommended_range'])
        self.assertEqual(details['context']['chronic_load']['recommended_range'],[280,540])
        self.assertEqual(details['load_focus']['categories']['high_aerobic']['recommended_range'],[520,1040])
        self.assertIsNone(details['load_focus']['start'])
        self.assertEqual(details['load_focus']['period'],'trailing_month')

    def test_carried_vo2_is_not_historical_measurement_and_cycling_is_not_running(self):
        self.staged['vo2_trends'][0]['response']['trend'][0]['carried_forward']=True
        details,skipped=self.normalize()
        self.assertEqual(skipped,1)
        self.assertIsNone(details['history']['vo2_max'][0]['value'])
        self.assertEqual(details['training']['vo2_max']['value'],54.9) # Explicit current running status fallback only.
        self.staged['vo2_trends'][0]['response']['sport']='cycling'
        self.staged['current']['get_training_status']['sport']='CYCLING'
        details,_=self.normalize()
        self.assertIsNone(details['training']['vo2_max']['value'])

    def test_invalid_date_range_and_units_retain_prior_outputs(self):
        before=digest(self.store.current());sidecar=digest(self.store.read('dashboard_details'))
        self.staged['current']['get_training_load_balance']['aerobic_low']['target_min']=1000
        with self.assertRaises(Invalid):apply_training(self.store,self.staged)
        self.assertEqual(digest(self.store.current()),before)
        self.assertEqual(digest(self.store.read('dashboard_details')),sidecar)
        self.staged['current']['get_training_load_balance']['aerobic_low']['target_min']=430
        self.staged['readiness'][self.day][0]['date']='2000-01-01'
        with self.assertRaises(Invalid):apply_training(self.store,self.staged)
        self.assertEqual(digest(self.store.current()),before)

    def test_resting_hr_requires_correct_date_and_explicit_resting_measurement(self):
        self.assertEqual(resting_hr({'date':self.day,'resting_heart_rate_bpm':50},'get_heart_rates_summary',self.day),50)
        self.assertIsNone(resting_hr({'date':self.day,'min_heart_rate_bpm':43,'avg_heart_rate_bpm':61},'get_heart_rates_summary',self.day))
        with self.assertRaises(Invalid):resting_hr({'date':'2000-01-01','resting_heart_rate_bpm':50},'get_heart_rates_summary',self.day)
        with self.assertRaises(Invalid):resting_hr({'date':self.day,'resting_heart_rate_bpm':True},'get_heart_rates_summary',self.day)
        with self.assertRaises(Invalid):resting_hr({'date':self.day,'resting_heart_rate_bpm':0},'get_heart_rates_summary',self.day)

    def test_success_preserves_plan_profile_routes_and_sets_review(self):
        before=self.store.current();profile=digest(self.store.profile());routes=copy.deepcopy(self.store.read('dashboard_details')['activities'])
        report=apply_training(self.store,self.staged)
        self.assertEqual(report['status'],'complete')
        self.assertEqual(report['wellness_fields']['resting_hr_bpm'],14)
        self.assertEqual(digest(self.store.profile()),profile)
        self.assertEqual(self.store.current()['training_plan'],before['training_plan'])
        self.assertEqual(self.store.read('dashboard_details')['activities'],routes)
        self.assertTrue(self.store.read('run_status')['review_required'])
        self.assertFalse(self.store.profile()['profile_confirmed'])
        self.assertFalse(self.store.profile()['automation']['enabled'])
        self.assertEqual(self.store.current()['wellness']['items'][-1]['values']['resting_hr_bpm']['source_tool'],'get_rhr_day')

    def test_refresh_preserves_unchanged_plan_when_cited_week_leaves_window(self):
        before = copy.deepcopy(self.store.current()['training_plan'])
        self.staged['observations']['activities']['items'] = [activity for activity in self.staged['observations']['activities']['items']
            if not ('2026-09-21' <= activity['local_date'] <= '2026-09-27')]
        apply_training(self.store, self.staged)
        self.assertEqual(self.store.current()['training_plan'], before)
        self.assertTrue(self.store.read('run_status')['review_required'])

    def test_concurrent_feedback_and_disk_failure_do_not_publish_partial_import(self):
        before=copy.deepcopy(self.store.read('current'))
        self.store.feedback({'text':'Synthetic concurrent note','feeling':'easy'})
        with self.assertRaises(Invalid):apply_training(self.store,self.staged)
        self.assertEqual(self.store.read('current'),before)
        self.staged['expected']['feedback']=digest(self.store.read('feedback'))
        original=self.store.write
        def fail_once(key,value):
            if key=='garmin_import_report':raise OSError('Synthetic disk failure')
            return original(key,value)
        with patch.object(self.store,'write',side_effect=fail_once):
            with self.assertRaises(OSError):apply_training(self.store,self.staged)
        self.assertEqual(self.store.read('current'),before)

    def test_stage_bounds_and_repeated_failures_stop_reads(self):
        class FailedClient:
            schemas={'get_training_status':{},'get_training_load_balance':{},'get_recovery_time_remaining':{}}
            calls=0
            def call(self,*_):
                self.calls+=1
                raise Invalid('Synthetic failed read')
        client=FailedClient()
        with self.assertRaises(Invalid):stage_training(self.store,client,43)
        with patch('coach.training.collect',return_value={key:copy.deepcopy(self.bundle[key]) for key in ('activities','wellness')}):
            with self.assertRaisesRegex(Invalid,'Repeated Garmin failures'):stage_training(self.store,client,1)
        self.assertEqual(client.calls,3)


if __name__=='__main__':unittest.main()
