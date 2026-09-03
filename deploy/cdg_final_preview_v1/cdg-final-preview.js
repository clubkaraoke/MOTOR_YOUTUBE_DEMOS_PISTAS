(() => {
  'use strict';

  const WIDTH=300, HEIGHT=216, VX=6, VY=12, VW=288, VH=192, PPS=300;

  class CDGDecoder {
    constructor(){ this.reset(); }
    reset(){
      this.frame=new Uint8Array(WIDTH*HEIGHT);
      this.palette=Array.from({length:16},()=>[0,0,0,255]);
      this.border=0; this.transparent=0; this.memoryColor=0; this.hOffset=0; this.vOffset=0;
    }
    clear(color){ this.memoryColor=color&15; this.frame.fill(this.memoryColor); }
    scroll(color,hcmd,vcmd,copyMode){
      const hdir=(hcmd>>4)&3,vdir=(vcmd>>4)&3;
      this.hOffset=hcmd&7; this.vOffset=vcmd&15;
      const dx=hdir===1?6:(hdir===2?-6:0),dy=vdir===1?12:(vdir===2?-12:0);
      if(!dx&&!dy)return;
      const old=this.frame.slice(),fill=color&15;
      for(let y=0;y<HEIGHT;y++)for(let x=0;x<WIDTH;x++){
        const sx=x-dx,sy=y-dy,dst=y*WIDTH+x;
        if(sx>=0&&sx<WIDTH&&sy>=0&&sy<HEIGHT)this.frame[dst]=old[sy*WIDTH+sx];
        else if(copyMode){
          const wx=((sx%WIDTH)+WIDTH)%WIDTH,wy=((sy%HEIGHT)+HEIGHT)%HEIGHT;
          this.frame[dst]=old[wy*WIDTH+wx];
        }else this.frame[dst]=fill;
      }
    }
    packet(p){
      if(!p||p.length!==24||((p[0]&63)!==9))return;
      const ins=p[1]&63,d=new Uint8Array(16);
      for(let i=0;i<16;i++)d[i]=p[4+i]&63;
      if(ins===1)this.clear(d[0]);
      else if(ins===2)this.border=d[0]&15;
      else if(ins===6||ins===38){
        const c0=d[0]&15,c1=d[1]&15,row=d[2]&31,col=d[3]&63,y0=row*12,x0=col*6;
        for(let r=0;r<12;r++){
          const bits=d[4+r],y=y0+r;if(y>=HEIGHT)continue;
          for(let c=0;c<6;c++){
            const x=x0+c;if(x>=WIDTH)continue;
            const value=(bits&(1<<(5-c)))?c1:c0,idx=y*WIDTH+x;
            this.frame[idx]=ins===38?(this.frame[idx]^value):value;
          }
        }
      }else if(ins===20)this.scroll(d[0],d[1],d[2],false);
      else if(ins===24)this.scroll(d[0],d[1],d[2],true);
      else if(ins===30||ins===31){
        const base=ins===30?0:8;
        for(let i=0;i<8;i++){
          const a=d[i*2],b=d[i*2+1];
          const r=(a&60)>>2,g=((a&3)<<2)|((b&48)>>4),bl=b&15;
          this.palette[base+i]=[r*17,g*17,bl*17,255];
        }
      }else if(ins===28)this.transparent=d[0]&15;
    }
  }

  const P={
    active:false, loadedJob:'', decoder:new CDGDecoder(), data:null, processed:0,
    voice:new Audio(), inst:new Audio(), hasInst:false, raf:0, loading:false
  };
  P.voice.preload='metadata'; P.inst.preload='metadata';

  function q(id){return document.getElementById(id);}
  function tokenUrl(path){
    const sep=path.includes('?')?'&':'?';
    return path+sep+'token='+encodeURIComponent(typeof PANEL_TOKEN!=='undefined'?PANEL_TOKEN:'');
  }
  function fmt(t){
    if(!Number.isFinite(t)||t<0)t=0;
    const m=Math.floor(t/60),s=Math.floor(t%60);
    return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
  }
  function setStatus(msg,kind=''){
    const el=q('cdgFinalStatus'); if(!el)return;
    el.textContent=msg; el.dataset.kind=kind;
  }
  function setPct(id,out,v){ q(id).value=String(v); q(out).textContent=Math.round(v)+'%'; }

  function inject(){
    const tabs=document.querySelector('.pvTabs'),preview=document.getElementById('preview');
    if(!tabs||!preview||q('pvTabCdg'))return;

    const style=document.createElement('style');
    style.textContent=`
      .pvTabs{grid-template-columns:repeat(4,1fr)!important}
      #cdgFinalPanel{display:flex;flex-direction:column;gap:10px;padding:0 10px 12px}
      #cdgFinalPanel[hidden]{display:none!important}
      .cdgfScreen{background:#050505;border:1px solid #30363f;aspect-ratio:3/2;display:flex;align-items:center;justify-content:center;overflow:hidden}
      #cdgFinalCanvas{width:100%;height:100%;object-fit:fill;image-rendering:pixelated;background:#000}
      .cdgfTransport{display:grid;grid-template-columns:46px 1fr 46px;gap:7px;align-items:center;color:#aab5c3;font:10px var(--mono,monospace)}
      #cdgFinalSeek{width:100%;accent-color:#8b5cf6}
      .cdgfButtons{display:flex;justify-content:center;gap:8px}
      .cdgfBtn{border:1px solid #383f4b;background:#171b24;color:#e8ebf2;border-radius:7px;padding:7px 11px;font:700 11px var(--sans,Arial);cursor:pointer}
      .cdgfBtn.primary{border-color:#7047d6;background:#342366}
      .cdgfMix{border:1px solid #2d3440;background:#11151d;border-radius:8px;padding:9px;display:flex;flex-direction:column;gap:8px}
      .cdgfMixTitle{font:700 9px var(--mono,monospace);color:#8d99aa;letter-spacing:.08em;text-transform:uppercase}
      .cdgfRow{display:grid;grid-template-columns:88px 1fr 35px;gap:8px;align-items:center;color:#dfe4ed;font:11px var(--sans,Arial)}
      .cdgfRow input{width:100%;accent-color:#8b5cf6}
      .cdgfRow.off{opacity:.42}
      #cdgFinalStatus{font:10px var(--mono,monospace);line-height:1.35;color:#9da9b8;border-top:1px solid #292f39;padding-top:8px}
      #cdgFinalStatus[data-kind="ok"]{color:#63d6a3}
      #cdgFinalStatus[data-kind="warn"]{color:#f2b705}
      #cdgFinalStatus[data-kind="bad"]{color:#ff7979}
      .cdgfNote{font:9px var(--mono,monospace);color:#778395;line-height:1.35}
    `;
    document.head.appendChild(style);

    const btn=document.createElement('button');
    btn.className='pvTab'; btn.id='pvTabCdg'; btn.type='button'; btn.textContent='CDG'; btn.dataset.pvmode='cdg';
    tabs.appendChild(btn);

    const panel=document.createElement('div'); panel.id='cdgFinalPanel'; panel.hidden=true;
    panel.innerHTML=`
      <div class="cdgfScreen"><canvas id="cdgFinalCanvas" width="288" height="192"></canvas></div>
      <div class="cdgfTransport"><span id="cdgFinalNow">00:00</span><input id="cdgFinalSeek" type="range" min="0" max="1000" value="0" disabled><span id="cdgFinalDur">00:00</span></div>
      <div class="cdgfButtons">
        <button class="cdgfBtn" id="cdgFinalBack" type="button">−5 s</button>
        <button class="cdgfBtn primary" id="cdgFinalPlay" type="button">▶ Reproducir</button>
        <button class="cdgfBtn" id="cdgFinalFwd" type="button">+5 s</button>
      </div>
      <div class="cdgfMix">
        <div class="cdgfMixTitle">Mezcla de audio sincronizada</div>
        <div class="cdgfRow"><span>🎙 Voz</span><input id="cdgVoiceVol" type="range" min="0" max="100" value="100"><span id="cdgVoicePct">100%</span></div>
        <div class="cdgfRow" id="cdgInstRow"><span>♫ Instrumental</span><input id="cdgInstVol" type="range" min="0" max="100" value="100"><span id="cdgInstPct">100%</span></div>
        <div class="cdgfNote">CDG + Voz + Instrumental usan el mismo reloj. Los dos volúmenes son independientes.</div>
        <div id="cdgFinalStatus">Buscando el último CDG renderizado…</div>
      </div>
      <button class="cdgfBtn" id="cdgFinalRender" type="button">↻ Crear / actualizar CDG final</button>
    `;
    const pvBox=q('pvBox'); pvBox.insertAdjacentElement('afterend',panel);

    btn.addEventListener('click',async()=>{
      try{ if(typeof PV!=='undefined')PV.mode='cdg'; }catch(_){}
      document.querySelectorAll('.pvTab').forEach(x=>x.classList.toggle('on',x===btn));
      document.querySelectorAll('.pvCtlGroup').forEach(g=>g.classList.remove('on'));
      pvBox.hidden=true; q('pvInfo').hidden=true; q('pvDesigner').hidden=true; panel.hidden=false;
      P.active=true; await loadFinal();
    });

    tabs.querySelectorAll('.pvTab:not(#pvTabCdg)').forEach(b=>b.addEventListener('click',()=>{
      panel.hidden=true; pvBox.hidden=false; q('pvInfo').hidden=false; q('pvDesigner').hidden=false;
      P.active=false; pauseAll();
    }));

    q('cdgFinalPlay').onclick=togglePlay;
    q('cdgFinalBack').onclick=()=>seekTo((P.voice.currentTime||0)-5);
    q('cdgFinalFwd').onclick=()=>seekTo((P.voice.currentTime||0)+5);
    q('cdgFinalSeek').oninput=e=>{
      const d=P.voice.duration;
      if(Number.isFinite(d)&&d>0)seekTo((Number(e.target.value)/1000)*d);
    };
    q('cdgVoiceVol').oninput=e=>{
      const v=Number(e.target.value);P.voice.volume=v/100;q('cdgVoicePct').textContent=Math.round(v)+'%';
    };
    q('cdgInstVol').oninput=e=>{
      const v=Number(e.target.value);P.inst.volume=v/100;q('cdgInstPct').textContent=Math.round(v)+'%';
    };
    q('cdgFinalRender').onclick=async()=>{
      if(typeof crearCdg!=='function'){setStatus('No encuentro el renderer del editor.','bad');return;}
      pauseAll(); setStatus('Generando el CDG final real…','warn');
      window.DJGABO_CDG_PREVIEW_RENDERING=true;
      try{
        await crearCdg(false);
      }finally{
        window.DJGABO_CDG_PREVIEW_RENDERING=false;
      }
      if(P.active) await loadFinal(true);
    };

    P.voice.addEventListener('loadedmetadata',()=>{
      q('cdgFinalDur').textContent=fmt(P.voice.duration);
      q('cdgFinalSeek').disabled=false;
      if(P.hasInst&&P.inst.readyState>=1)P.inst.currentTime=Math.min(P.voice.currentTime||0,P.inst.duration||Infinity);
    });
    P.voice.addEventListener('ended',pauseAll);
    P.inst.addEventListener('loadedmetadata',()=>{
      if(P.active)P.inst.currentTime=Math.min(P.voice.currentTime||0,P.inst.duration||Infinity);
    });
    setPct('cdgVoiceVol','cdgVoicePct',100);setPct('cdgInstVol','cdgInstPct',100);
    loop();
  }

  async function loadFinal(force=false){
    if(P.loading)return;
    const jid=typeof PANEL_JOB_ID!=='undefined'?PANEL_JOB_ID:'';
    if(!jid){setStatus('Abre un trabajo del panel para usar el preview CDG.','warn');return;}
    if(!force&&P.loadedJob===jid&&P.data){renderAt(P.voice.currentTime||0);return;}
    P.loading=true; pauseAll(); setStatus('Leyendo el CDG final renderizado…');
    try{
      const mr=await fetch(tokenUrl('/api/jobs/'+encodeURIComponent(jid)+'/preview/meta'),{cache:'no-store'});
      const meta=await mr.json(); if(!mr.ok||meta.ok===false)throw new Error(meta.error||'No pude leer el estado del render.');
      if(!meta.has_cdg){
        P.data=null;P.loadedJob='';
        clearCanvas();
        setStatus('Todavía no hay CDG final. Pulsa «Crear / actualizar CDG final».','warn');
        return;
      }
      const cr=await fetch(tokenUrl(meta.cdg_url),{cache:'no-store'});
      if(!cr.ok)throw new Error('No pude abrir el CDG final ('+cr.status+').');
      P.data=new Uint8Array(await cr.arrayBuffer());
      if(P.data.length<24)throw new Error('El CDG final está vacío.');
      P.decoder.reset();P.processed=0;P.loadedJob=jid;

      P.voice.pause();P.voice.src=tokenUrl(meta.voice_url);P.voice.load();
      P.hasInst=!!meta.has_instrumental;
      const row=q('cdgInstRow');
      row.classList.toggle('off',!P.hasInst);q('cdgInstVol').disabled=!P.hasInst;
      if(P.hasInst){P.inst.pause();P.inst.src=tokenUrl(meta.instrumental_url);P.inst.load();}
      else{P.inst.pause();P.inst.removeAttribute('src');P.inst.load();}
      renderAt(0);
      setStatus('CDG final real cargado'+(P.hasInst?' · Voz + Instrumental listos para comparar.':' · Sin WAV instrumental vinculado.'),'ok');
    }catch(e){
      P.data=null;clearCanvas();setStatus((e&&e.message)||String(e),'bad');
    }finally{P.loading=false;}
  }

  function clearCanvas(){
    const c=q('cdgFinalCanvas');if(!c)return;const x=c.getContext('2d',{alpha:false});x.fillStyle='#000';x.fillRect(0,0,VW,VH);
  }
  function resetDecoder(){P.decoder.reset();P.processed=0;}
  function processTo(target){
    if(!P.data)return;
    const total=Math.floor(P.data.length/24);target=Math.max(0,Math.min(total,target|0));
    if(target<P.processed)resetDecoder();
    for(let i=P.processed;i<target;i++)P.decoder.packet(P.data.subarray(i*24,i*24+24));
    P.processed=target;
  }
  function renderAt(t){
    if(!P.data)return;
    processTo(Math.floor(Math.max(0,t)*PPS));
    const c=q('cdgFinalCanvas'),ctx=c.getContext('2d',{alpha:false}),img=ctx.createImageData(VW,VH),out=img.data;
    let o=0;const hf=Math.min(5,P.decoder.hOffset),vf=Math.min(11,P.decoder.vOffset);
    for(let y=0;y<VH;y++){
      const sy=Math.min(HEIGHT-1,VY+y+vf);
      for(let x=0;x<VW;x++){
        const sx=Math.min(WIDTH-1,VX+x+hf),ci=P.decoder.frame[sy*WIDTH+sx]&15,rgba=P.decoder.palette[ci];
        out[o++]=rgba[0];out[o++]=rgba[1];out[o++]=rgba[2];out[o++]=255;
      }
    }
    ctx.putImageData(img,0,0);
  }

  function pauseAll(){
    P.voice.pause();P.inst.pause();
    const b=q('cdgFinalPlay');if(b)b.textContent='▶ Reproducir';
  }
  async function togglePlay(){
    if(!P.data){await loadFinal();if(!P.data)return;}
    if(!P.voice.paused){pauseAll();return;}
    if(P.hasInst&&P.inst.readyState>=1){
      try{P.inst.currentTime=Math.min(P.voice.currentTime||0,Math.max(0,(P.inst.duration||Infinity)-.02));}catch(_){}
    }
    const jobs=[P.voice.play()];
    if(P.hasInst)jobs.push(P.inst.play().catch(()=>{}));
    await Promise.allSettled(jobs);
    q('cdgFinalPlay').textContent='❚❚ Pausar';
  }
  function seekTo(t){
    const d=P.voice.duration;
    if(Number.isFinite(d)&&d>0)t=Math.max(0,Math.min(d,t));
    else t=Math.max(0,t);
    try{P.voice.currentTime=t;}catch(_){}
    if(P.hasInst&&P.inst.readyState>=1)try{P.inst.currentTime=Math.min(t,Math.max(0,(P.inst.duration||t)-.02));}catch(_){}
    renderAt(t);paintTransport();
  }
  function paintTransport(){
    const t=P.voice.currentTime||0,d=P.voice.duration;
    q('cdgFinalNow').textContent=fmt(t);q('cdgFinalDur').textContent=fmt(d);
    if(Number.isFinite(d)&&d>0)q('cdgFinalSeek').value=String(Math.round(Math.min(1,t/d)*1000));
  }
  function loop(){
    if(P.active&&P.data){
      const t=P.voice.currentTime||0;
      if(P.hasInst&&!P.voice.paused&&P.inst.readyState>=1){
        const drift=(P.inst.currentTime||0)-t;
        if(Math.abs(drift)>.075)try{P.inst.currentTime=t;}catch(_){}
        if(P.inst.paused&&t<(P.inst.duration||Infinity)-.05)P.inst.play().catch(()=>{});
      }
      renderAt(t);paintTransport();
      if(P.voice.paused&&q('cdgFinalPlay'))q('cdgFinalPlay').textContent='▶ Reproducir';
    }
    P.raf=requestAnimationFrame(loop);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',inject,{once:true});
  else inject();

  window.DJGABO_CDG_FINAL_PREVIEW={reload:()=>loadFinal(true),pause:pauseAll};
})();
