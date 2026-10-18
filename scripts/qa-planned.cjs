/* Read-only browser verification of the current imported Garmin planned runs. */
const assert=require('node:assert/strict');
const {chromium}=require(process.env.COACH_PLAYWRIGHT_MODULE||'playwright');

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.COACH_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/map/tile/**',route=>route.abort());
    await page.goto('http://127.0.0.1:8765/#plan');
    await page.getByText('CACHED GARMIN',{exact:true}).waitFor();
    await page.getByRole('heading',{name:'Garmin Connect planned runs'}).waitFor();
    assert.equal(await page.locator('.garmin-planned-row').count(),2);
    let payload=await page.evaluate(async()=>await(await fetch('/api/dashboard')).json());
    assert.equal(payload.garmin_planned_runs.items.length,2);
    assert.equal(payload.coach_chat.items.length,0);
    assert.equal(payload.athlete_memory.items.length,0);
    await page.screenshot({path:'/tmp/coach-planned-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile Plan overflow');
    await page.screenshot({path:'/tmp/coach-planned-mobile.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('Garmin planned runs: two Connect rows, empty chat and memory, desktop/mobile layout, no JavaScript errors.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
