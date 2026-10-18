/* Independent geographic invariants for projection, camera fitting and gaps. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const sandbox={window:{},Math};vm.createContext(sandbox);vm.runInContext(fs.readFileSync('web/map.js','utf8'),sandbox);
const {project,unproject,positions,fit,screen}=sandbox.window.RunningMap;
const close=(a,b)=>assert(Math.abs(a-b)<1e-9);
close(project(0,0)[0],.5);close(project(0,0)[1],.5);
close(project(0,180)[0],1);close(project(0,-180)[0],0);
for(const [lat,lon] of [[0,0],[59.3,18.1],[-33.9,151.2],[80,-145]]){const restored=unproject(...project(lat,lon));close(restored[0],lat);close(restored[1],lon);}
const route=positions([{latitude:59.3,longitude:18.1},{latitude:null,longitude:null},{latitude:59.31,longitude:18.12}]);assert.equal(route[1],null);
for(const [w,h] of [[600,400],[200,600],[340,330]]){const camera=fit(route,w,h);for(const point of route.filter(Boolean)){const [x,y]=screen(point,camera,w,h);assert(x>=38&&x<=w-38);assert(y>=50&&y<=h-50);}const p=route[0],scale=256*2**camera.zoom,center=screen(p,camera,w,h),east=screen([p[0]+.0001,p[1]],camera,w,h),south=screen([p[0],p[1]+.0001],camera,w,h);close(east[0]-center[0],.0001*scale);close(south[1]-center[1],.0001*scale);}
const dateline=positions([{latitude:10,longitude:179.99},{latitude:10,longitude:-179.99}]);assert(Math.abs(dateline[0][0]-dateline[1][0])<.001);
console.log('Map geometry passed: Mercator reference points, inverse roundtrip, GPS gaps, responsive fit, equal axis scale and dateline wrapping.');
