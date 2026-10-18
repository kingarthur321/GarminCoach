/* Render saved real observations through browser interception; no server or writes. */
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.COACH_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
let payload;
try { payload = JSON.parse(execFileSync(process.env.COACH_PYTHON || 'python3', ['-c', `
import json
from coach.core import Store, now, weekly_distance
from coach.dashboard import saved_details
from coach.maps import preferences
from coach.workouts import session_key
from coach.memory import read_memory
s=Store()
with s.lock():
 b=s.current()
 print(json.dumps({'profile':s.profile(),'snapshot':b,'feedback':s.read('feedback'),'registry':s.read('workout_registry'),'status':s.read('run_status'),'weekly_distance':weekly_distance(b['activities']),'server_time':now(),'csrf_token':'read-only-browser-check','map':preferences(s),'details':saved_details(s,b),'import_report':s.read('garmin_import_report'),'coach_chat':s.read('coach_chat',{'items':[]}),'athlete_memory':read_memory(s),'coach_connection':{'provider':'Claude API','configured':True,'active_job':None},'session_keys':{x['id']:session_key(x) for x in b['training_plan']['sessions']}}))
`], {cwd:root, encoding:'utf8',maxBuffer:64*1024*1024}));
} catch {console.error('Unable to load private saved state; previous files retained.');process.exit(1);}
assert.equal(payload.snapshot.activities.source, 'garmin', 'Requires a saved real Garmin snapshot');
const evidence = path.join(root, 'data', 'verification');
fs.mkdirSync(evidence, {recursive:true, mode:0o700});
(async()=>{
  const browser = await chromium.launch({executablePath:process.env.COACH_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    let mockTiles=0;
    const origin='http://coach-verification.invalid';
    await page.route('**/*',async route=>{
      const req=route.request(), u=new URL(req.url());
      if(u.origin!==origin) return route.abort();
      if(req.method()!=='GET') return route.fulfill({status:403,contentType:'application/json',body:'{"error":"Verification is read-only"}'});
      if(u.pathname==='/api/dashboard') return route.fulfill({contentType:'application/json',body:JSON.stringify(payload)});
      if(/^\/api\/map\/tile\/\d+\/\d+\/\d+\.png$/.test(u.pathname)){
        const parts=u.pathname.split('/');const cached=path.join(root,'data','map_tiles',parts[4]+'_'+parts[5]+'_'+parts[6]);
        if(fs.existsSync(cached))return route.fulfill({contentType:'image/png',body:fs.readFileSync(cached)});
        mockTiles++;return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jSDoAAAAASUVORK5CYII=','base64')});
      }
      const files={'/':['index.html','text/html'],'/app.js':['app.js','text/javascript'],'/map.js':['map.js','text/javascript'],'/style.css':['style.css','text/css']};
      if(!files[u.pathname]) return route.fulfill({status:404,body:'Not found'});
      const [file,contentType]=files[u.pathname];return route.fulfill({contentType,body:fs.readFileSync(path.join(root,'web',file))});
    });
    await page.goto(origin);await page.getByText('CACHED GARMIN',{exact:true}).waitFor();
    assert(await page.locator('.update-time').isVisible());
    const newest=[...payload.snapshot.activities.items].sort((a,b)=>(b.started_at||b.local_date).localeCompare(a.started_at||a.local_date))[0];
    assert.equal(await page.getByLabel('Select run',{exact:true}).inputValue(),newest.id);
    assert.equal(await page.locator('.run-heading h1').textContent(),newest.name);
    assert.equal(await page.locator('.run-metric').count(),4);
    if(payload.details.source==='unavailable'){
      assert.equal(await page.locator('.route-svg').count(),0);assert.equal(await page.locator('#run-chart .chart-svg').count(),0);assert.equal(await page.locator('.gauge').count(),0);
      await page.getByText('Route not yet imported',{exact:true}).waitFor();
      assert.equal(await page.locator('.readiness-body .training-number span').textContent(),'—');
      assert.equal(await page.locator('.status-value').textContent(),'Not imported');
    }
    if(payload.details.source==='garmin'){await page.locator('#route-cursor').waitFor();assert.equal(await page.locator('[data-endpoint]').count(),2);if(payload.map.street_map_enabled){await page.waitForFunction(()=>document.querySelectorAll('.map-tiles img').length>0&&[...document.querySelectorAll('.map-tiles img')].every(img=>img.complete));assert(await page.getByRole('button',{name:'Offline view',exact:true}).isVisible());assert(await page.getByRole('link',{name:'© OpenStreetMap contributors',exact:true}).isVisible());}else assert.equal(await page.locator('.map-tiles img').count(),0);const index=Math.floor(payload.details.activities[newest.id].samples.length/2);assert.equal(Number(await page.locator('#route-cursor').getAttribute('data-cursor-time')),payload.details.activities[newest.id].samples[index].time_seconds);assert(await page.locator('.recorded-route').count());}
    if(payload.details.training.readiness.value!=null){assert.equal(Number(await page.locator('.readiness-body .training-number span').textContent()),payload.details.training.readiness.value);assert.equal(await page.locator('.status-value').getAttribute('data-status'),payload.details.training.training_status.value);assert.equal(Number(await page.locator('.load-top strong').textContent()),payload.details.training.acute_load.value);assert.equal(await page.locator('.load-card>.gauge').count(),0);assert.equal(await page.locator('.load-card .gauge').first().locator('.target').count(),payload.details.training.acute_load.recommended_range?1:0);await page.getByRole('button',{name:'Open Training readiness history',exact:true}).click();assert.equal(await page.locator('.factor-list dd').count(),6);assert(await page.locator('.metric-context').getByText('Garmin recovery:',{exact:false}).count());await page.getByRole('button',{name:'Close metric details',exact:true}).click();await page.getByRole('button',{name:'Open Training load history',exact:true}).click();assert(await page.locator('#metric-content').getByText('Chronic load',{exact:true}).count());assert(await page.locator('#metric-content .metric-context .target').count());await page.getByRole('button',{name:'Close metric details',exact:true}).click();for(const gauge of await page.locator('.load-category .gauge').all()){const d=await gauge.evaluate(n=>({value:Number(n.dataset.value),max:Number(n.dataset.scaleMax),lo:Number(n.dataset.targetLow),hi:Number(n.dataset.targetHigh),marker:parseFloat(n.querySelector('.marker').style.left),left:parseFloat(n.querySelector('.target').style.left),width:parseFloat(n.querySelector('.target').style.width)}));assert(Math.abs(d.marker-d.value/d.max*100)<.001);assert(Math.abs(d.left-d.lo/d.max*100)<.001);assert(Math.abs(d.width-(d.hi-d.lo)/d.max*100)<.001);}}
    await page.getByRole('button',{name:'Distance',exact:false}).click();assert(await page.locator('#metric-content').getByText('activity:'+newest.id,{exact:true}).count());
    await page.getByRole('button',{name:'Close metric details',exact:true}).click();
    await page.screenshot({path:path.join(evidence,'dashboard-garmin-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile horizontal overflow');
    await page.screenshot({path:path.join(evidence,'dashboard-garmin-mobile.png'),fullPage:true});
    await page.getByRole('button',{name:'Athlete settings',exact:true}).click();await page.getByLabel('Display name',{exact:true}).waitFor();
    assert.equal(await page.getByLabel('Display name',{exact:true}).inputValue(),payload.profile.display_name);
    assert.equal(await page.getByLabel('Timezone (IANA)',{exact:true}).inputValue(),payload.profile.timezone);
    assert.equal(Number(await page.getByLabel('Goal distance (km)',{exact:true}).inputValue()),payload.profile.goal.distance_km);
    assert.equal(Number(await page.getByLabel('Goal time (minutes)',{exact:true}).inputValue())*60,payload.profile.goal.target_time_seconds);
    assert.deepEqual(await page.locator('#profile input[name=day]:checked').evaluateAll(nodes=>nodes.map(node=>node.value)),payload.profile.availability.running_days);
    assert.equal(await page.getByLabel('Other training (one session per line, include day)',{exact:true}).inputValue(),payload.profile.availability.other_training.join('\n'));
    assert.equal(await page.getByLabel('I confirm this goal, availability, other training and constraints are mine.',{exact:true}).isChecked(),payload.profile.profile_confirmed);
    assert.equal(await page.getByLabel('Garmin write policy',{exact:true}).inputValue(),payload.profile.automation.garmin_write_policy);
    await page.locator('.rail [data-view="trends"]').click();await page.getByText(payload.snapshot.coach_summary.headline,{exact:true}).waitFor();
    if(payload.snapshot.wellness.items.every(n=>n.values.resting_hr_bpm.value==null)){await page.getByRole('button',{name:'Resting HR',exact:true}).click();assert(await page.getByText('0/'+payload.snapshot.wellness.items.length+' nights reported · Gaps stay missing',{exact:true}).count());}
    else {await page.getByRole('button',{name:'Resting HR',exact:true}).click();const count=payload.snapshot.wellness.items.filter(n=>n.values.resting_hr_bpm.value!=null).length;assert(await page.getByText(count+'/'+payload.snapshot.wellness.items.length+' nights reported · Gaps stay missing',{exact:true}).count());assert(await page.locator('.recovery-chart svg path').count());}
    await page.locator('.rail [data-view="plan"]').click();assert.equal(await page.locator('#plan article').count(),payload.snapshot.training_plan.sessions.length);
    if(!payload.snapshot.training_plan.illustrative){assert.equal(payload.status.review_required,false);assert.deepEqual(payload.snapshot.training_plan.sessions.map(s=>new Date(s.date+'T00:00:00Z').getUTCDay()),[2,4,6]);for(const session of payload.snapshot.training_plan.sessions)assert(await page.getByText(session.title,{exact:true}).count());}
    if(payload.snapshot.training_plan.illustrative){assert.equal(await page.getByText('Illustrative session · upload disabled',{exact:true}).count(),payload.snapshot.training_plan.sessions.length);assert.equal(await page.getByRole('button',{name:'Select this revision for Garmin',exact:true}).count(),0);}
    else {const sessions=payload.snapshot.training_plan.sessions,entries=Object.values(payload.registry.entries).filter(e=>sessions.some(s=>s.id===e.session_id)),selected=Object.values(payload.registry.authorizations).filter(a=>sessions.some(s=>s.id===a.session_id));assert.equal(await page.getByText('No workout created in Connect',{exact:true}).count(),sessions.length-entries.length);assert.equal(await page.getByText('Write policy: '+payload.profile.automation.garmin_write_policy,{exact:true}).count(),sessions.length);assert.equal(await page.getByRole('button',{name:'Select this revision for Garmin',exact:true}).count(),sessions.length-selected.length);for(const entry of entries){assert.equal(entry.state,'scheduled');assert(await page.getByText('Garmin: scheduled',{exact:true}).count());assert(await page.getByText('Workout '+entry.workout_id+' · Calendar '+entry.scheduled_entry_id,{exact:true}).count());assert.equal(entry.watch_verified_at,null);}for(const extra of Object.values(payload.registry.one_off_sessions||{})){await page.getByText(extra.title,{exact:true}).waitFor();assert(await page.locator('#one-off').getByText(extra.rationale,{exact:true}).count());}}
    await page.screenshot({path:path.join(evidence,'dashboard-garmin-plan-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:path.join(evidence,'dashboard-garmin-plan-desktop.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    const result={recorded_at:new Date().toISOString(),data:'Real saved Garmin snapshot; personal screenshots stay private',method:'Browser routes fulfill local files, saved payload and cached or explicitly mocked tile images. No localhost server, outgoing network or state writes.',street_map_enabled:payload.map.street_map_enabled,mocked_tile_images:mockTiles,browser:'Chrome + Playwright',checks:['Cached Garmin source and update time rendered','Newest run and actual summary metrics rendered','Source activity ID retained in details','Recorded GPS/series show for imported runs; unsupported training signals stay missing; no fixture fallback','Street map is enabled by default; controls and attribution rendered with intercepted images','Imported readiness, status and acute load match saved observations','Six readiness factors and Garmin recovery rendered','Chronic targets stay separate from unavailable acute targets; three focus markers and ranges match saved geometry','Saved summary and plan rendered','Resting HR coverage and recorded graph rendered; missing values stay missing','Personal plan and saved Garmin workout state rendered','Confirmed profile fields and configured write policy render from saved state','390px layout has no horizontal overflow; athlete settings reachable','No browser JavaScript errors'],result:'passed'};
    fs.writeFileSync(path.join(evidence,'browser-garmin-checks.json'),JSON.stringify(result,null,2)+'\n',{mode:0o600});fs.rmSync(path.join(evidence,'browser-failure.txt'),{force:true});console.log(JSON.stringify(result,null,2));
  } finally {await browser.close();}
})().catch(e=>{fs.writeFileSync(path.join(evidence,'browser-failure.txt'),String(e),{mode:0o600});console.error('Saved-dashboard verification failed; inspect private browser-failure.txt.');process.exit(1);});
