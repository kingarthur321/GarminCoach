/* Synthetic component check: tile images are intercepted, never downloaded. */
const fs=require('node:fs');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.COACH_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
  const browser=await chromium.launch({executablePath:process.env.COACH_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:600,height:480}});
    const requests=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
    const origin='http://map-verification.invalid';
    await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.origin!==origin)return route.abort();
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<div><div id="map-canvas" style="width:500px;height:360px;position:relative;overflow:hidden"></div><span class="map-scale"></span><span class="route-footnote">Recorded GPS</span></div>'});
      if(url.pathname.startsWith('/api/map/tile/')){requests.push(url.pathname);return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jSDoAAAAASUVORK5CYII=','base64')});}
      return route.abort();
    });
    await page.goto(origin);
    await page.addStyleTag({content:'.map-tiles{position:absolute;inset:0}.map-tiles img{position:absolute;width:256px;height:256px}.route-svg{position:absolute;inset:0;width:100%;height:100%}'});
    await page.addScriptTag({content:fs.readFileSync('web/map.js','utf8')});
    await page.evaluate(()=>{
      const n=2**13,center=[(4494.5)/n,(2473.5)/n],near=32/(256*n);
      const samples=[-1,0,1].map((offset,i)=>{const [latitude,longitude]=RunningMap.unproject(center[0]+offset*near,center[1]);return {latitude,longitude,time_seconds:i*10};});
      window.route=new RunningMap.RouteMap(document.querySelector('#map-canvas'),samples,{street:false,cursor:1});
      window.reference=center;
    });
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(requests.length,0,'Offline map must never request tiles');
    await page.evaluate(()=>{route.street=true;route.camera={center:reference,zoom:13};route.draw();});
    await page.waitForFunction(()=>document.querySelectorAll('.map-tiles img').length>0&&[...document.querySelectorAll('.map-tiles img')].every(img=>img.complete));
    const inspect=()=>page.evaluate(()=>{const tile=[...document.querySelectorAll('.map-tiles img')].find(img=>img.src.endsWith('/13/4494/2473.png'));const cursor=document.querySelector('#route-cursor');return {tile:[parseFloat(tile.style.left),parseFloat(tile.style.top)],cursor:[Number(cursor.getAttribute('cx')),Number(cursor.getAttribute('cy'))],zoom:Number(document.querySelector('.route-svg').dataset.zoom)};});
    const initial=await inspect();assert.deepEqual(initial.tile,[122,52]);assert.deepEqual(initial.cursor,[250,180]);assert.equal(initial.zoom,13);
    await page.locator('#map-canvas').focus();await page.keyboard.press('ArrowRight');
    const moved=await inspect();for(let i=0;i<2;i++)assert.equal(moved.tile[i]-initial.tile[i],moved.cursor[i]-initial.cursor[i]);assert.equal(moved.cursor[0]-initial.cursor[0],40);
    await page.keyboard.press('+');assert.equal(Number(await page.locator('.route-svg').getAttribute('data-zoom')),14);assert(await page.locator('.recorded-route').count());
    await page.locator('.map-tiles img').first().evaluate(img=>img.dispatchEvent(new Event('error')));
    assert.equal(await page.locator('.route-footnote').textContent(),'Street map unavailable · Recorded GPS');assert(await page.locator('#route-cursor').count());
    assert.deepEqual(errors,[]);
    const result={recorded_at:new Date().toISOString(),data:'Synthetic coordinates and intercepted fixture tile images only',provider_requests:0,checks:['Offline route makes no tile requests','Mercator route cursor and known tile center align','Keyboard pan moves route and tiles by the same pixels','Zoom preserves recorded route','Tile failure retains route and shows missing-map state'],result:'passed'};
    fs.writeFileSync('docs/evidence/map-checks.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
