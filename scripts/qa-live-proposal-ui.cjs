/* Read-only browser check of the saved live proposal. No review or Garmin POST. */
const assert = require('node:assert/strict');
const {chromium} = require(process.env.COACH_PLAYWRIGHT_MODULE || 'playwright');

(async()=>{
  const browser=await chromium.launch({executablePath:process.env.COACH_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:900}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/map/tile/**',route=>route.abort());
    await page.goto('http://127.0.0.1:8765');
    await page.getByText('CACHED GARMIN',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Open coach conversation'}).click();
    const latest=page.locator('.chat-message.assistant').last();
    await latest.getByRole('button',{name:'Preview workout'}).click();
    assert(await page.locator('#workout-dialog').isVisible());
    assert.equal(await page.getByRole('button',{name:'Approve exact workout'}).isDisabled(),true);
    assert(await page.locator('#workout-content .steps li').count()>=3);
    await page.screenshot({path:'/tmp/coach-live-proposal-preview.png'});
    assert.deepEqual(errors,[]);
    console.log('Saved live proposal preview passed; exact approval remains unchecked. No state write.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
