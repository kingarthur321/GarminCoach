/* Full browser rehearsal against simulated Garmin provenance and a mock MCP client. */
const assert = require('node:assert/strict');
const {chromium} = require(process.env.COACH_PLAYWRIGHT_MODULE || 'playwright');

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.COACH_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:950}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(process.env.COACH_QA_URL || 'http://127.0.0.1:8767');
    await page.getByRole('button',{name:'Open coach conversation'}).click();
    await page.locator('#coach-input').fill('Draft one structured workout for my next available running day after my saved training week.');
    await page.getByRole('button',{name:'Send to coach'}).click();
    const preview=page.locator('.chat-message.assistant').last().getByRole('button',{name:'Preview workout'});
    await preview.waitFor();
    await preview.click();
    assert.equal(await page.getByRole('button',{name:'Approve exact workout'}).isDisabled(),true);
    await page.getByLabel(/I reviewed this exact date/).check();
    await page.getByRole('button',{name:'Approve exact workout'}).click();
    await page.getByRole('button',{name:'Send to Garmin Connect'}).waitFor();
    let state=await page.evaluate(async()=>await (await fetch('/api/dashboard')).json());
    assert.equal(Object.keys(state.registry.one_off_sessions).length,1);
    assert.equal(Object.keys(state.registry.entries).length,0);
    assert.equal(state.snapshot.training_plan.sessions.length,3);
    await page.screenshot({path:'/tmp/coach-proposal-approved-desktop.png',fullPage:true});
    await page.getByRole('button',{name:'Send to Garmin Connect'}).click();
    await page.getByRole('button',{name:'I see it on my watch'}).waitFor();
    state=await page.evaluate(async()=>await (await fetch('/api/dashboard')).json());
    assert.equal(Object.values(state.registry.entries)[0].state,'scheduled');
    assert.equal(state.snapshot.training_plan.sessions.length,3);
    await page.getByRole('button',{name:'I see it on my watch'}).click();
    await page.locator('#watch-note').fill('Simulated watch report: test model, scheduled date and all steps visible.');
    await page.getByRole('button',{name:'Save watch confirmation'}).click();
    state=await page.evaluate(async()=>await (await fetch('/api/dashboard')).json());
    assert.equal(Object.values(state.registry.entries)[0].watch_evidence.source,'user_statement');
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile plan overflow');
    await page.screenshot({path:'/tmp/coach-proposal-scheduled-mobile.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('Proposal browser check: chat, exact review, separate send, mocked Connect readback, watch report and mobile layout passed.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
