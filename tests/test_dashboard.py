import copy
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import patch
from coach.core import Invalid, Store, digest
from coach.fixtures import make_demo
from coach.dashboard import demo_details, saved_details, validate_details
from coach.chat import CoachChat, command, context, run_agent, validate_response
from coach.anthropic_api import API_URL, PROMPT_SEPARATOR, request_message


class DashboardCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.bootstrap()
        with self.store.lock():
            self.store.commit(make_demo(self.store.profile()))
        self.bundle = self.store.current()

    def test_fixture_series_and_ranges_are_valid_and_bounded(self):
        details = demo_details(self.bundle)
        self.assertEqual(details['source'], 'fixture')
        self.assertEqual(len(details['activities']), 12)
        self.assertEqual(len(details['history']['readiness']), 28)
        self.assertEqual(details['training']['acute_load']['recommended_range'], [600, 850])
        self.assertEqual(len(next(iter(details['activities'].values()))['samples']), 161)

    def test_real_snapshot_does_not_receive_demo_measurements(self):
        real = copy.deepcopy(self.bundle)
        real['activities']['source'] = 'garmin'
        details = saved_details(self.store, real)
        self.assertEqual(details['source'], 'unavailable')
        self.assertEqual(details['activities'], {})
        self.assertIsNone(details['training']['readiness']['value'])
        self.store.write('dashboard_details', demo_details(self.bundle))
        self.assertEqual(saved_details(self.store, real)['source'], 'unavailable')

    def test_bad_coordinates_unknown_ids_and_reversed_range_rejected(self):
        details = demo_details(self.bundle)
        cases = []
        bad = copy.deepcopy(details)
        next(iter(bad['activities'].values()))['samples'][0]['latitude'] = 100
        cases.append(bad)
        bad = copy.deepcopy(details)
        next(iter(bad['activities'].values()))['samples'][1]['time_seconds'] = 0
        cases.append(bad)
        bad = copy.deepcopy(details)
        bad['training']['acute_load']['recommended_range'] = [850, 600]
        cases.append(bad)
        bad = copy.deepcopy(details)
        bad['activities']['unknown'] = next(iter(bad['activities'].values()))
        cases.append(bad)
        bad = copy.deepcopy(details)
        bad['training']['readiness'].update(value=None, date='bad-date', missing_reason='not_reported')
        cases.append(bad)
        for bad in cases:
            with self.assertRaises(Invalid):
                validate_details(bad, self.bundle)

    def test_missing_samples_remain_missing(self):
        details = demo_details(self.bundle)
        sample = next(iter(details['activities'].values()))['samples'][10]
        sample.update(latitude=None, longitude=None, heart_rate_bpm=None)
        validate_details(details, self.bundle)
        self.assertIsNone(sample['heart_rate_bpm'])

    def wait_job(self, chat, job):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            current = chat.get(job['id'])
            if current['state'] != 'working':
                return current
            time.sleep(.01)
        self.fail('Mock chat did not finish')

    def response(self):
        return {'answer': 'These are synthetic observations.', 'references': ['activity:' + self.bundle['activities']['items'][0]['id']], 'action': 'none', 'workout': None}

    def test_chat_saves_validated_response_without_replacing_plan(self):
        before = digest(self.store.current())
        captured = []
        def execute(prompt, timeout, event):
            captured.append(prompt)
            return self.response()
        chat = CoachChat(self.store, executor=execute)
        job = chat.start({'message': 'Explain this run', 'activity_id': self.bundle['activities']['items'][0]['id']})
        self.assertEqual(self.wait_job(chat, job)['state'], 'complete')
        self.assertEqual(digest(self.store.current()), before)
        self.assertEqual(len(self.store.read('coach_chat')['items']), 1)
        self.assertIn('not tool instructions', captured[0])
        self.assertNotIn('latitude', captured[0])

    def test_unknown_evidence_rejects_answer_and_preserves_history(self):
        answer = self.response()
        answer['references'] = ['activity:invented']
        chat = CoachChat(self.store, executor=lambda *_: answer)
        job = chat.start({'message': 'Explain'})
        self.assertEqual(self.wait_job(chat, job)['state'], 'failed')
        self.assertIsNone(self.store.read('coach_chat'))

    def test_chat_only_one_request_and_cancellation(self):
        started = threading.Event()
        def execute(prompt, timeout, cancelled):
            started.set()
            cancelled.wait(2)
            return self.response()
        chat = CoachChat(self.store, executor=execute)
        job = chat.start({'message': 'Explain'})
        self.assertTrue(started.wait(1))
        self.assertEqual(chat.current_job()['id'], job['id'])
        with self.assertRaises(Invalid):
            chat.start({'message': 'Second question'})
        chat.cancel(job['id'])
        self.assertEqual(self.wait_job(chat, job)['state'], 'cancelled')
        self.assertIsNone(self.store.read('coach_chat'))
        self.assertIsNone(chat.current_job())

    def test_workout_proposal_requires_evidence_steps_and_confirmed_limits(self):
        _, refs, profile = context(self.store, None)
        result = self.response()
        session = self.bundle['training_plan']['sessions'][0]
        result['workout'] = {key: copy.deepcopy(session[key]) for key in ('title', 'date', 'rationale', 'references', 'steps')}
        validate_response(result, refs, profile)
        profile['profile_confirmed'] = True
        profile['constraints']['max_session_minutes'] = 10
        with self.assertRaises(Invalid):
            validate_response(result, refs, profile)

    def test_claude_request_uses_strict_output_and_no_remote_tools(self):
        seen = {}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self, _):
                return json.dumps({'type': 'message', 'stop_reason': 'end_turn', 'content':
                    [{'type': 'thinking', 'thinking': ''}, {'type': 'text', 'text': json.dumps(self_case.response())}]}).encode()
        self_case = self
        def open_request(request, timeout):
            seen['request'], seen['timeout'] = request, timeout
            return Response()
        prompt = 'Coach instructions' + PROMPT_SEPARATOR + json.dumps({'question': 'Explain this run', 'context': {'source': 'fixture'}})
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key', 'ANTHROPIC_MODEL': 'claude-sonnet-5-5'}):
            result = request_message(prompt, 10, opener=open_request)
        self.assertEqual(result, self.response())
        self.assertEqual(seen['request'].full_url, API_URL)
        self.assertEqual(seen['request'].get_header('X-api-key'), 'test-key')
        body = json.loads(seen['request'].data)
        self.assertEqual(body['model'], 'claude-sonnet-5-5')
        self.assertEqual(body['output_config']['format']['type'], 'json_schema')
        self.assertNotIn('tools', body)
        self.assertNotIn('latitude', json.dumps(body))

    def test_claude_missing_key_and_http_error_are_safe(self):
        prompt = 'Coach instructions' + PROMPT_SEPARATOR + json.dumps({'question': 'Explain', 'context': {}})
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}), patch('coach.anthropic_api.load_local_env'):
            with self.assertRaisesRegex(Invalid, 'key is missing'):
                run_agent(prompt, 5, threading.Event())
        def denied(*_args, **_kwargs):
            raise urllib.error.HTTPError(API_URL, 401, 'private detail', {}, io.BytesIO(b'private response'))
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key'}):
            with self.assertRaisesRegex(Invalid, 'rejected the key') as error:
                request_message(prompt, 5, opener=denied)
        self.assertNotIn('private', str(error.exception))
        self.assertNotIn('test-key', str(error.exception))

    def test_chat_worker_command_exposes_no_key_or_garmin(self):
        cmd = command()
        self.assertIn('coach.anthropic_worker', cmd)
        self.assertFalse(any('garmin' in x or 'test-key' in x for x in cmd))


if __name__ == '__main__':
    unittest.main()
