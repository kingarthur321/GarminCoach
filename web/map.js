'use strict';
/* One Web Mercator camera for the route, markers and optional street tiles. */
(() => {
  const NS='http://www.w3.org/2000/svg';
  const el=(tag,attrs={},text)=>{const n=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,value);if(text!==undefined)n.textContent=text;return n;};
  function project(latitude,longitude){
    const lat=Math.max(-85.05112878,Math.min(85.05112878,latitude))*Math.PI/180;
    return [(longitude+180)/360,(1-Math.asinh(Math.tan(lat))/Math.PI)/2];
  }
  function unproject(x,y){return [Math.atan(Math.sinh(Math.PI*(1-2*y)))*180/Math.PI,x*360-180];}
  function positions(samples){
    let reference=null;
    return samples.map(p=>{
      if(p.latitude==null||p.longitude==null)return null;
      const xy=project(p.latitude,p.longitude);
      if(reference===null)reference=xy[0];
      xy[0]+=Math.round(reference-xy[0]);
      return xy;
    });
  }
  function fit(points,width,height){
    const valid=points.filter(Boolean),xs=valid.map(p=>p[0]),ys=valid.map(p=>p[1]);
    const loX=Math.min(...xs),hiX=Math.max(...xs),loY=Math.min(...ys),hiY=Math.max(...ys);
    const availableX=Math.max(32,width-76),availableY=Math.max(32,height-100);
    const zoom=Math.max(1,Math.min(18,Math.floor(Math.log2(Math.min(availableX/Math.max(hiX-loX,1e-8),availableY/Math.max(hiY-loY,1e-8))/256))));
    return {center:[(loX+hiX)/2,(loY+hiY)/2],zoom};
  }
  function screen(point,camera,width,height){const scale=256*2**camera.zoom;return [width/2+(point[0]-camera.center[0])*scale,height/2+(point[1]-camera.center[1])*scale];}
  class RouteMap {
    constructor(canvas,samples,{cursor=0,street=false,label='Recorded route'}={}){
      this.canvas=canvas;this.samples=samples;this.points=positions(samples);this.cursor=cursor;this.street=street;this.label=label;this.images=new Map();this.drag=null;
      this.layer=document.createElement('div');this.layer.className='map-tiles';this.svg=el('svg',{class:'route-svg',role:'img','aria-label':label});canvas.replaceChildren(this.layer,this.svg);
      canvas.tabIndex=0;canvas.setAttribute('role','group');canvas.setAttribute('aria-label',label+'. Drag to pan. Plus and minus zoom. Arrow keys pan.');
      this.resize=new ResizeObserver(()=>this.fit());this.resize.observe(canvas);
      this.events=[];
      this.on('pointerdown',e=>{if(e.button!==0)return;this.drag={x:e.clientX,y:e.clientY,center:[...this.camera.center]};canvas.setPointerCapture(e.pointerId);canvas.classList.add('dragging');});
      this.on('pointermove',e=>{if(!this.drag)return;const scale=256*2**this.camera.zoom;this.camera.center=[this.drag.center[0]-(e.clientX-this.drag.x)/scale,this.drag.center[1]-(e.clientY-this.drag.y)/scale];this.draw();});
      const stop=()=>{this.drag=null;canvas.classList.remove('dragging');};this.on('pointerup',stop);this.on('pointercancel',stop);
      this.on('wheel',e=>{if(!e.ctrlKey&&!e.metaKey)return;e.preventDefault();this.zoom(e.deltaY<0?1:-1,[e.offsetX,e.offsetY]);},{passive:false});
      this.on('keydown',e=>{const movement={ArrowLeft:[40,0],ArrowRight:[-40,0],ArrowUp:[0,40],ArrowDown:[0,-40]};if(movement[e.key]){e.preventDefault();const s=256*2**this.camera.zoom;this.camera.center=this.camera.center.map((n,i)=>n+movement[e.key][i]/s);this.draw();}else if(['+','=','-'].includes(e.key)){e.preventDefault();this.zoom(e.key==='-'?-1:1);}else if(e.key==='Home'){e.preventDefault();this.fit();}});
      this.fit();
    }
    on(type,handler,options){this.canvas.addEventListener(type,handler,options);this.events.push([type,handler,options]);}
    dimensions(){return [Math.max(1,this.canvas.clientWidth),Math.max(1,this.canvas.clientHeight)];}
    fit(){const [w,h]=this.dimensions();this.camera=fit(this.points,w,h);this.draw();}
    zoom(delta,anchor){const [w,h]=this.dimensions(),before=256*2**this.camera.zoom,next=Math.max(1,Math.min(19,this.camera.zoom+delta));if(next===this.camera.zoom)return;const after=256*2**next,p=anchor||[w/2,h/2];this.camera.center=this.camera.center.map((v,i)=>v+(p[i]-[w/2,h/2][i])*(1/before-1/after));this.camera.zoom=next;this.draw();}
    inspect(index){this.cursor=index;const point=this.points[index];let marker=this.svg.querySelector('#route-cursor');if(!point){marker?.remove();return;}const [x,y]=screen(point,this.camera,...this.dimensions());if(!marker){marker=el('circle',{id:'route-cursor',r:5.5,fill:'#1678ff',stroke:'#fff','stroke-width':2.5});this.svg.querySelector('.route-geometry').append(marker);}marker.setAttribute('cx',x);marker.setAttribute('cy',y);marker.setAttribute('data-cursor-time',this.samples[index].time_seconds);}
    draw(){
      const [w,h]=this.dimensions();this.svg.setAttribute('viewBox','0 0 '+w+' '+h);this.svg.dataset.zoom=this.camera.zoom;this.svg.dataset.centerX=this.camera.center[0];this.svg.dataset.centerY=this.camera.center[1];
      this.drawTiles(w,h);this.drawRoute();this.drawScale();
    }
    drawTiles(w,h){
      if(!this.street){this.layer.hidden=true;return;}this.layer.hidden=false;
      const n=2**this.camera.zoom,world=256*n,left=this.camera.center[0]*world-w/2,top=this.camera.center[1]*world-h/2,needed=new Set();
      for(let x=Math.floor(left/256);x<=Math.floor((left+w)/256);x++)for(let y=Math.floor(top/256);y<=Math.floor((top+h)/256);y++){
        if(y<0||y>=n)continue;const wrapped=((x%n)+n)%n,key=this.camera.zoom+'/'+x+'/'+y;needed.add(key);
        let img=this.images.get(key);if(!img){img=document.createElement('img');img.alt='';img.draggable=false;img.src='/api/map/tile/'+this.camera.zoom+'/'+wrapped+'/'+y+'.png';img.addEventListener('error',()=>{img.hidden=true;this.canvas.dataset.tiles='unavailable';const note=this.canvas.parentElement.querySelector('.route-footnote');if(note)note.textContent='Street map unavailable · Recorded GPS';});img.addEventListener('load',()=>{this.canvas.dataset.tiles='available';});this.images.set(key,img);this.layer.append(img);}
        img.style.left=(x*256-left)+'px';img.style.top=(y*256-top)+'px';
      }
      for(const [key,img] of this.images)if(!needed.has(key)){img.remove();this.images.delete(key);}
    }
    drawRoute(){
      if(!this.camera)return;const [w,h]=this.dimensions(),svg=this.svg;
      svg.replaceChildren();
      if(!this.street){svg.append(el('rect',{width:w,height:h,fill:'#eef3f7'}));const offsetX=(w/2-this.camera.center[0]*256*2**this.camera.zoom)%64,offsetY=(h/2-this.camera.center[1]*256*2**this.camera.zoom)%64;
        for(let x=offsetX;x<w;x+=64)svg.append(el('line',{x1:x,y1:0,x2:x,y2:h,stroke:'#dfe7ef'}));for(let y=offsetY;y<h;y+=64)svg.append(el('line',{x1:0,y1:y,x2:w,y2:y,stroke:'#dfe7ef'}));
      }
      const group=el('g',{class:'route-geometry'});let segment=[];
      const flush=()=>{if(segment.length<2){segment=[];return;}const d=segment.map((p,i)=>(i?'L':'M')+p.map(n=>n.toFixed(3)).join(' ')).join(' ');group.append(el('path',{d,fill:'none',stroke:'#fff','stroke-width':7,'stroke-linejoin':'round','stroke-linecap':'round'}),el('path',{d,class:'recorded-route',fill:'none',stroke:'#1678ff','stroke-width':3.5,'stroke-linejoin':'round','stroke-linecap':'round'}));segment=[];};
      this.points.forEach(p=>{if(!p){flush();return;}segment.push(screen(p,this.camera,w,h));});flush();
      const valid=this.points.filter(Boolean);
      const close=Math.hypot(...screen(valid[0],this.camera,w,h).map((v,i)=>v-screen(valid[valid.length-1],this.camera,w,h)[i]))<14;
      const endpoint=(p,label,color)=>{const [x,y]=screen(p,this.camera,w,h);group.append(el('circle',{cx:x,cy:y,r:close&&label==='S'?10:7,fill:color,stroke:'#fff','stroke-width':2.5,'data-endpoint':label}),el('text',{x:x+11,y:y+(close?(label==='S'?-10:16):4),fill:color,stroke:'#fff','stroke-width':3,'paint-order':'stroke','font-size':11,'font-weight':600},label));};
      endpoint(valid[0],'S','#20a174');endpoint(valid[valid.length-1],'F','#f25560');
      const p=this.points[this.cursor];if(p){const [x,y]=screen(p,this.camera,w,h);group.append(el('circle',{cx:x,cy:y,r:5.5,fill:'#1678ff',stroke:'#fff','stroke-width':2.5,id:'route-cursor','data-cursor-time':this.samples[this.cursor].time_seconds}));}
      svg.append(group);
    }
    drawScale(){const [lat]=unproject(...this.camera.center),metersPerPixel=156543.03392804097*Math.cos(lat*Math.PI/180)/2**this.camera.zoom,target=70*metersPerPixel,base=10**Math.floor(Math.log10(target)),meters=[5,2,1].map(n=>n*base).find(n=>n<=target)||base;
      const scale=this.canvas.parentElement.querySelector('.map-scale');if(scale){scale.textContent=meters>=1000?meters/1000+' km':meters+' m';scale.style.width=(meters/metersPerPixel)+'px';scale.dataset.meters=meters;}
    }
    destroy(){this.resize.disconnect();for(const [type,handler,options] of this.events)this.canvas.removeEventListener(type,handler,options);}
  }
  window.RunningMap={RouteMap,project,unproject,positions,fit,screen};
})();
