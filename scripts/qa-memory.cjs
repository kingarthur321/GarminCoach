/* Disposable browser check of reviewed memory. Never opens the real athlete store. */
const assert = require('node:assert/strict');
const {chromium} = require(process.env.COACH_PLAYWRIGHT_MODULE || 'playwright');

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.COACH_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:950}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(process.env.COACH_QA_URL || 'http://127.0.0.1:8766');
    await page.getByText('DEMO DATA',{exact:true}).waitFor();
    await page.locator('.settings-link').click();
    await page.getByRole('button',{name:'Add a note'}).click();
    await page.locator('#memory-text').fill('I prefer an easy run after strength training.');
    await page.getByRole('button',{name:'Save memory'}).click();
    await page.getByText('Memory saved. Your plan needs a coach review.',{exact:true}).waitFor();
    let state=await page.evaluate(async()=>await (await fetch('/api/dashboard')).json());
    assert.equal(state.athlete_memory.items.length,1);
    assert.equal(state.athlete_memory.items[0].source.type,'manual');
    assert.equal(state.status.review_required,true);
    await page.locator('.memory-entry').first().getByRole('button',{name:'Edit'}).click();
    await page.locator('#memory-text').fill('I prefer an easy run on Friday after strength training.');
    await page.getByRole('button',{name:'Save memory'}).click();
    state=await page.evaluate(async()=>await (await fetch('/api/dashboard')).json());
    assert.equal(state.athlete_memory.items[0].revision,2);
    assert.equal(state.athlete_memory.items[0].text,'I prefer an easy run on Friday after strength training.');
    await page.screenshot({path:'/tmp/coach-memory-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile settings overflow');
    await page.getByRole('button',{name:'Open coach conversation'}).click();
    await page.locator('#coach-input').fill('Explain my recent running.');
    await page.getByRole('button',{name:'Send to coach'}).click();
    await page.locator('.chat-message.assistant').first().waitFor();
    await page.getByRole('button',{name:'Remember insight'}).first().click();
    assert(await page.locator('#memory-dialog').isVisible());
    await page.locator('#memory-text').fill('Keep the next workout controlled.');
    await page.getByRole('button',{name:'Save memory'}).click();
    state=await page.evaluate(async()=>await (await fetch('/api/dashboard')).json());
    assert.equal(state.athlete_memory.items.length,2);
    assert.equal(state.athlete_memory.items[1].source.type,'chat');
    assert.equal(state.athlete_memory.items[1].source.data_source,'fixture');
    await page.getByRole('button',{name:'Close conversation'}).click();
    await page.screenshot({path:'/tmp/coach-memory-mobile.png',fullPage:true});
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('.memory-entry').first().getByRole('button',{name:'Delete'}).click();
    await page.getByText('Memory deleted. Your plan needs a coach review.',{exact:true}).waitFor();
    state=await page.evaluate(async()=>await (await fetch('/api/dashboard')).json());
    assert.equal(state.athlete_memory.items.length,1);
    assert.equal(state.athlete_memory.items[0].kind,'user_statement');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile memory overflow');
    assert.deepEqual(errors,[]);
    console.log('Memory browser check: create, edit, review coach insight, delete, mobile layout and no page errors passed.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
