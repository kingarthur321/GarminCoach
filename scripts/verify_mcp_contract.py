"""Run the pinned server's own tools with synthetic API responses, no account."""
import asyncio
import copy
import json
import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from coach.core import atomic_json, load_json, normalize_activities, normalize_wellness
from coach.fixtures import make_demo
from coach.workouts import compile_workout, verify_details
from garmin_mcp import workouts, health_wellness, activity_management, training
from mcp.server.fastmcp import FastMCP


async def main():
    api = Mock()
    profile = load_json(ROOT / "athlete_profile.example.json")
    sample = make_demo(profile)
    payload = compile_workout(sample["training_plan"]["sessions"][0])
    api.upload_workout.return_value = {"workoutId": 111, "workoutName": payload["workoutName"]}
    api.get_workout_by_id.return_value = {**payload, "workoutId": 111}
    api.get_sleep_data.return_value = {"dailySleepDTO": {"sleepTimeSeconds": 27000, "restingHeartRate": 52, "sleepScores": {"overall": {"value": 80}}}, "avgOvernightHrv": 49}
    api.connectapi.return_value = [{"activityId": 222, "activityName": "Synthetic MCP contract run", "activityType": {"typeKey": "running"}, "startTimeLocal": "2026-09-30T07:00:00", "distance": 5000, "duration": 1500}]
    api.query_garmin_graphql.return_value = {"data": {"workoutScheduleSummariesScalar": [{"scheduleDate": sample["training_plan"]["sessions"][0]["date"], "scheduledWorkoutId": 333, "workoutId": 111, "workoutName": payload["workoutName"]}]}}
    day='2026-10-02'
    api.get_training_status.return_value={'mostRecentTrainingStatus':{'latestTrainingStatusData':{'111':{'calendarDate':day,'primaryTrainingDevice':True,'sport':'RUNNING','trainingStatus':7,'trainingStatusFeedbackPhrase':'PRODUCTIVE_3','acuteTrainingLoadDTO':{'dailyTrainingLoadAcute':350,'dailyTrainingLoadChronic':360,'dailyAcuteChronicWorkloadRatio':.9,'minTrainingLoadChronic':280,'maxTrainingLoadChronic':540,'acwrStatus':'OPTIMAL'}}}},'mostRecentVO2Max':{'generic':{'calendarDate':day,'vo2MaxValue':55,'vo2MaxPreciseValue':54.9}},'mostRecentTrainingLoadBalance':{'metricsTrainingLoadBalanceDTOMap':{'111':{'calendarDate':day,'primaryTrainingDevice':True,'trainingBalanceFeedbackPhrase':'AEROBIC_HIGH_SHORTAGE','monthlyLoadAerobicLow':680,'monthlyLoadAerobicLowTargetMin':430,'monthlyLoadAerobicLowTargetMax':950,'monthlyLoadAerobicHigh':450,'monthlyLoadAerobicHighTargetMin':520,'monthlyLoadAerobicHighTargetMax':1040,'monthlyLoadAnaerobic':220,'monthlyLoadAnaerobicTargetMin':170,'monthlyLoadAnaerobicTargetMax':520}}}}
    api.get_training_readiness.return_value=[{'calendarDate':day,'timestampLocal':day+'T12:00:00','score':56,'recoveryTime':120,'recoveryTimeChangePhrase':'REACHED_ZERO'}]
    api.get_rhr_day.return_value={'allMetrics':{'metricsMap':{'WELLNESS_RESTING_HEART_RATE':[{'calendarDate':day,'value':50}]}}}
    api.garmin_connect_metrics_url='/metrics-service/metrics/maxmet/daily'
    workouts.configure(api); health_wellness.configure(api); activity_management.configure(api);training.configure(api)
    app = FastMCP("Synthetic contract verification")
    app = workouts.register_tools(app); app = health_wellness.register_tools(app); app = activity_management.register_tools(app);app=training.register_tools(app)
    async def call(name, args):
        result = await app.call_tool(name, args)
        content = result[0] if isinstance(result, tuple) else result
        text = next(c.text for c in content if c.type == "text")
        return json.loads(text)
    uploaded = await call("upload_workout", {"workout_data": copy.deepcopy(payload)})
    assert uploaded["workout_id"] == 111
    details = await call("get_workout_by_id", {"workout_id": 111})
    verify_details(details, payload)
    activities = await call("get_activities_by_date", {"start_date": "2026-09-30", "end_date": "2026-09-30", "activity_type": "running", "page_size": 100})
    normalized = normalize_activities(activities["activities"], "Europe/Stockholm", "fixture", "2026-09-30", "2026-09-30")
    assert normalized["items"][0]["pace_seconds_per_km"] == 300
    sleep = await call("get_sleep_summary_range", {"start_date": "2026-09-30", "end_date": "2026-09-30"})
    wellness = normalize_wellness(sleep["nights"], "fixture", "2026-09-30", "2026-09-30")
    assert wellness["items"][0]["values"]["hrv_ms"]["value"] == 49
    scheduled = await call("get_scheduled_workouts", {"start_date": "2026-10-01", "end_date": "2026-10-12"})
    assert scheduled["scheduled_workouts"][0]["scheduled_workout_id"] == 333
    status=await call('get_training_status',{'date':day})
    assert status['acute_load']==350 and status['chronic_load']==360 and status['vo2_max_precise']==54.9
    assert status['optimal_chronic_load_min']==280 and status['optimal_chronic_load_max']==540
    focus=await call('get_training_load_balance',{'date':day})
    assert focus['aerobic_high']=={'load':450,'target_min':520,'target_max':1040,'status':'below'}
    readiness=await call('get_training_readiness',{'date':day})
    assert readiness[0]['score']==56 and readiness[0]['timestamp']==day+'T12:00:00'
    recovery=await call('get_recovery_time_remaining',{'date':day})
    assert recovery['remaining_hours']==0 and recovery['source']=='training_readiness'
    rhr=await call('get_rhr_day',{'date':day})
    assert rhr['allMetrics']['metricsMap']['WELLNESS_RESTING_HEART_RATE'][0]['value']==50
    trend=await call('get_vo2max_trend',{'start_date':day,'end_date':day})
    assert trend['sport']=='running' and trend['trend'][0]['vo2_max']==54.9
    result = {"data": "Synthetic API responses through actual pinned MCP tools; no Garmin account", "commit": "cfc5d799ab0f165e837f1188a1d093c65838aaf7", "checks": ["Actual upload_workout accepts compiled structured steps", "Actual get_workout_by_id curator matches local verification", "Actual activity curator matches normalization", "Actual sleep range curator matches normalization", "Actual calendar curator preserves scheduled entry ID", "Actual readiness timestamps and status/load curators match import", "Actual load focus retains category target ranges separately from chronic targets", "Actual VO2 tool retains precise running estimate", "Actual RHR tool retains dated resting values", "Actual recovery tool handles REACHED_ZERO without using stale assigned recovery"]}
    atomic_json(ROOT / "docs/evidence/mcp-contract-checks.json", result)
    print(json.dumps(result, indent=2))


asyncio.run(main())
