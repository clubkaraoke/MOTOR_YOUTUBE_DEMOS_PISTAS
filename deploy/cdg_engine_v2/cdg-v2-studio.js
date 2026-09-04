(function(){
"use strict";
var PPS=300, CDGW=300, CDGH=216, VX=6, VY=12, VW=288, VH=192;
var M={mode:"karaoke",timeline:null,cdg:null,decoder:null,processed:0,busy:false,lastProject:""};
function el(id){return document.getElementById(id)}
function token(){try{return String(PANEL_TOKEN||"")}catch(_){return ""}}
function job(){try{return String(PANEL_JOB_ID||"")}catch(_){return ""}}
function api(path){return "/cdg-v2"+path}
function ftime(t){t=Number(t)||0;var m=Math.floor(t/60),s=t-m*60;return m+":"+(s<10?"0":"")+s.toFixed(3)}
function roleColor(r){r=String(r||"").toLowerCase();if(r==="hombre"||r==="male")return "#55b8ff";if(r==="mujer"||r==="female")return "#ff6fb6";if(r==="duo"||r==="duet")return "#63dfa2";return "#f2b705"}
function editorReady(){
  try{return !!(typeof S!=="undefined"&&S&&S.doc&&Array.isArray(S.doc.segments)&&S.doc.song)}catch(_){return false}
}
function project(){
  if(!editorReady())throw new Error("El editor todavía está terminando de cargar el proyecto.");
  if(typeof buildExport!=="function")throw new Error("No encuentro buildExport del editor.");
  var p=buildExport();
  if(!p||!Array.isArray(p.segments))throw new Error("El proyecto del editor todavía no está listo.");
  return p
}
function opts(){var x={};try{x.lines_per_screen=Number(S.cfg.linesPerPage||6);x.font_size=Number(S.cfg.fontSize||18);x.stroke_width=Number(S.cfg.strokeWidth||1)}catch(_){}return x}
async function request(path,body){
  var o={method:body?"POST":"GET",headers:{"Content-Type":"application/json"},cache:"no-store"};
  if(body)o.body=JSON.stringify(Object.assign({token:token()},body));
  var u=api(path)+(body?"":("?token="+encodeURIComponent(token())));
  var r=await fetch(u,o),d=await r.json().catch(function(){return{}});
  if(!r.ok||d.ok===false)throw new Error(d.error||("HTTP "+r.status));return d
}
function status(msg,kind){var x=el("v2status");if(!x)return;x.textContent=msg;x.dataset.kind=kind||""}
function inject(){
  var host=el("preview");if(!host||el("djV2"))return;
  host.classList.add("djV2host");
  var css=document.createElement("style");css.textContent=
  "#preview.djV2host> :not(#djV2){display:none!important}"+
  "#djV2{height:100%;display:flex;flex-direction:column;background:#101319;color:#e7ebf2;font-family:Arial,sans-serif}"+
  ".v2head{padding:10px 12px;border-bottom:1px solid #29303c;display:flex;justify-content:space-between;align-items:center}.v2badge{font:800 10px monospace;color:#b89cff;letter-spacing:.08em}.v2safe{font:700 9px monospace;color:#63d6a3}"+
  ".v2tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;padding:9px}.v2tab{border:1px solid #343d4d;background:#171c25;color:#aeb8c8;padding:7px 3px;border-radius:6px;font:700 10px Arial;cursor:pointer}.v2tab.on{border-color:#8b5cf6;background:#2a1e4a;color:#fff}"+
  ".v2body{padding:0 9px 9px;display:flex;flex-direction:column;gap:8px;min-height:0;flex:1}.v2screen{aspect-ratio:300/216;background:#000;border:1px solid #343b47;display:flex;align-items:center;justify-content:center}.v2screen canvas{width:100%;height:100%;image-rendering:pixelated}"+
  ".v2meta{font:10px monospace;color:#98a5b7;line-height:1.45;min-height:30px}.v2actions{display:grid;grid-template-columns:1fr 1fr;gap:7px}.v2btn{border:1px solid #414a59;background:#1a202a;color:#fff;border-radius:6px;padding:8px;font-weight:800;cursor:pointer}.v2btn.primary{background:#7047d6;border-color:#8b5cf6}.v2btn:disabled{opacity:.45}"+
  "#v2status{font:10px monospace;border-top:1px solid #29303c;padding-top:7px;color:#9aa7b8}#v2status[data-kind=ok]{color:#63d6a3}#v2status[data-kind=bad]{color:#ff7979}#v2status[data-kind=warn]{color:#f2b705}"+
  ".v2scroll{overflow:auto;min-height:0;max-height:360px;border:1px solid #29303c;border-radius:6px}.v2row{padding:7px 8px;border-bottom:1px solid #252c36;font:10px monospace}.v2row b{color:#fff}.v2warn{color:#f2b705}.v2diag{white-space:pre-wrap;padding:9px;font:10px monospace;color:#b9c4d3}"+
  ".v2transport{display:grid;grid-template-columns:58px 1fr 58px;gap:6px;align-items:center;font:10px monospace}.v2transport input{width:100%;accent-color:#8b5cf6}";
  document.head.appendChild(css);
  var box=document.createElement("div");box.id="djV2";box.innerHTML=
  '<div class="v2head"><span class="v2badge">CDG ENGINE V2 · LAB</span><span class="v2safe">PRODUCCIÓN INTACTA</span></div>'+
  '<div class="v2tabs"><button class="v2tab on" data-v2="karaoke">Karaoke V2</button><button class="v2tab" data-v2="pages">Páginas</button><button class="v2tab" data-v2="diag">Diagnóstico</button><button class="v2tab" data-v2="cdg">CDG V2</button></div>'+
  '<div class="v2body"><div class="v2screen"><canvas id="v2canvas" width="300" height="216"></canvas></div><div id="v2panel"></div>'+
  '<div class="v2transport"><span id="v2now">0:00</span><input id="v2seek" type="range" min="0" max="1000" value="0"><span id="v2dur">0:00</span></div>'+
  '<div class="v2actions"><button class="v2btn" id="v2refresh">↻ Recalcular</button><button class="v2btn primary" id="v2render">⚡ Generar V2</button></div>'+
  '<div id="v2status">Cargando timeline V2…</div></div>';
  host.appendChild(box);
  box.querySelectorAll(".v2tab").forEach(function(b){b.onclick=function(){M.mode=b.dataset.v2;box.querySelectorAll(".v2tab").forEach(function(x){x.classList.toggle("on",x===b)});paintPanel();paint()}});
  el("v2refresh").onclick=function(){refresh(true)};
  el("v2render").onclick=render;
  el("v2seek").oninput=function(e){try{var d=Number(S.audio.duration||S.duration||0);if(d)S.audio.currentTime=d*Number(e.target.value)/1000}catch(_){}paint()};
  var old=el("btnCdg");if(old){old.textContent="⚡ Generar V2";old.addEventListener("click",function(e){e.preventDefault();e.stopImmediatePropagation();render()},true)}
  document.addEventListener("click",function(e){var t=e.target&&e.target.closest?e.target.closest('[data-phase="3"]'):null;if(t){e.preventDefault();e.stopImmediatePropagation();render()}},true);
  autoRefreshWhenReady();requestAnimationFrame(loop)
}
async function autoRefreshWhenReady(){
  for(var i=0;i<240;i++){
    if(editorReady()){
      await refresh(false);
      return;
    }
    status("Esperando que termine de cargar audio + proyecto…","warn");
    await new Promise(function(r){setTimeout(r,250)});
  }
  status("El editor tardó demasiado en preparar el proyecto. Pulsa Recalcular.","bad");
}
async function refresh(force){
  if(M.busy)return;var jid=job();if(!jid){status("Abre un trabajo guardado del panel.","warn");return}
  if(!editorReady()){status("El proyecto aún se está cargando. Espera un momento y vuelve a Recalcular.","warn");return}
  M.busy=true;status("Construyendo timeline desde el JSON actual…","warn");
  try{var p=project();var d=await request("/api/v2/jobs/"+encodeURIComponent(jid)+"/timeline",{project:p,options:opts()});M.timeline=d.timeline;M.cdg=null;resetDecoder();status("Timeline V2 lista · filas NOMAD LINE_DELAYED · START/END conservados.","ok");paintPanel();paint()}
  catch(e){status(e.message||String(e),"bad")}finally{M.busy=false}
}
async function render(){
  if(M.busy)return;var jid=job();if(!jid){status("No hay trabajo del panel.","bad");return}
  M.busy=true;el("v2render").disabled=true;status("Generando CDG V2 real con la misma timeline…","warn");
  try{var p=project();var d=await request("/api/v2/jobs/"+encodeURIComponent(jid)+"/render",{project:p,options:opts()});M.timeline=d.timeline;await loadCdg();M.mode="cdg";document.querySelectorAll(".v2tab").forEach(function(x){x.classList.toggle("on",x.dataset.v2==="cdg")});status("CDG V2 generado · no se publicó a Dropbox.","ok");paintPanel();paint()}
  catch(e){status(e.message||String(e),"bad")}finally{M.busy=false;el("v2render").disabled=false}
}
function current(){try{return Number(S.audio.currentTime||0)}catch(_){return 0}}
function paint(){
  var c=el("v2canvas");if(!c)return;var x=c.getContext("2d");x.fillStyle="#000";x.fillRect(0,0,300,216);
  var t=current(),tl=M.timeline;if(!tl)return;
  if(M.mode==="cdg"&&M.cdg){renderCdg(t);return}
  var lay=tl.layout||{},lpp=Number(lay.lines_per_screen||6),fs=Number(lay.font_size||18),lth=Number(lay.line_tile_height||2),row=Number(lay.row||2);
  x.font="900 "+fs+"px Impact, Arial Black, sans-serif";x.textBaseline="middle";x.textAlign="left";x.lineJoin="round";x.lineWidth=Math.max(2,Number(lay.stroke_width||1)*2);
  tl.lines.forEach(function(line){if(t<line.display_at||t>=line.remove_at)return;
    var ng=line.nomad||{},y=Number.isFinite(Number(ng.y))?(Number(ng.y)+Math.max(1,Number(ng.height||fs))/2):((row*12)+((Number(line.slot)-1)*lth*12)+(lth*6)),parts=line.words||[],total=0,widths=[];
    parts.forEach(function(w,i){var z=x.measureText(w.text).width+(i?x.measureText(" ").width:0);widths.push(z);total+=z});
    var px=Number.isFinite(Number(ng.x))?Number(ng.x):((300-total)/2);
    parts.forEach(function(w,i){if(i){var sp=x.measureText(" ").width;px+=sp}var ww=x.measureText(w.text).width;x.strokeStyle="#000";x.fillStyle="#fff";x.strokeText(w.text,px,y);x.fillText(w.text,px,y);
      var q=t<=w.start?0:t>=w.end?1:(t-w.start)/Math.max(.001,w.end-w.start);if(q>0){x.save();x.beginPath();x.rect(px-2,y-fs,ww*q+4,fs*2);x.clip();x.strokeStyle="#000";x.fillStyle=roleColor(w.role);x.strokeText(w.text,px,y);x.fillText(w.text,px,y);x.restore()}px+=ww});
  });
}
function paintPanel(){
  var p=el("v2panel"),tl=M.timeline;if(!p)return;if(!tl){p.innerHTML='<div class="v2meta">Sin timeline.</div>';return}
  if(M.mode==="karaoke"){var t=current(),act=tl.lines.find(function(l){return t>=l.display_at&&t<l.remove_at});p.innerHTML='<div class="v2meta">'+(act?('<b>'+act.text+'</b><br>pantalla '+act.page_index+' · slot '+act.slot+' · NOMAD draw '+ftime(act.display_at)+' · erase '+ftime(act.remove_at)+'<br>voz '+ftime(act.sweep_start)+' → '+ftime(act.sweep_end)):'Esperando línea activa')+'<br>FILAS: NOMAD LINE_DELAYED · OFFSET OCULTO: 0.000 s · '+tl.source_word_count+' palabras</div>';return}
  if(M.mode==="pages"){p.innerHTML='<div class="v2scroll">'+tl.lines.map(function(l){var n=l.nomad||{};return '<div class="v2row"><b>P'+l.page_index+' · L'+l.slot+' · NOMAD</b> '+l.text+'<br>draw '+ftime(l.display_at)+' ['+(n.line_draw_frame??"?")+'f] · sweep '+ftime(l.sweep_start)+'–'+ftime(l.sweep_end)+' · erase '+ftime(l.remove_at)+' ['+(n.line_erase_frame??"?")+'f]</div>'}).join("")+'</div>';return}
  if(M.mode==="diag"){var warn=tl.warnings||[],rm=tl.render_metadata||{},lay=tl.layout||{};p.innerHTML='<div class="v2scroll"><div class="v2diag">ENGINE: '+tl.engine+'\nUPSTREAM: '+tl.upstream_commit+'\nROW SCHEDULER: '+(rm.cdg_row_scheduler||lay.row_scheduler||"N/A")+'\nCLEAR MODE: '+(lay.clear_mode||"N/A")+'\nSTART/END INMUTABLES: SI\nPREVIEW = CDG TIMELINE: SI\nINTRO_DELAY: 0.000 s\nOFFSETS OCULTOS: NO\nLINEAS: '+tl.rendered_line_count+'\nWARNINGS: '+warn.length+'\n\n'+warn.map(function(w){return '⚠ '+(w.text||w.kind)+' · '+(w.detail||"")}).join("\n")+'</div></div>';return}
  p.innerHTML='<div class="v2meta">CDG real a 300 paquetes/s, usando el mismo reloj del audio.<br><button class="v2btn" id="v2download" type="button">Descargar CDG V2</button></div>';setTimeout(function(){var b=el("v2download");if(b)b.onclick=download},0)
}
async function loadCdg(){
  var r=await fetch(api("/api/v2/jobs/"+encodeURIComponent(job())+"/cdg?token="+encodeURIComponent(token())),{cache:"no-store"});if(!r.ok)throw new Error("No pude abrir CDG V2 ("+r.status+")");M.cdg=new Uint8Array(await r.arrayBuffer());resetDecoder()
}
function download(){var a=document.createElement("a");a.href=api("/api/v2/jobs/"+encodeURIComponent(job())+"/cdg?token="+encodeURIComponent(token()));a.download=(job()||"karaoke")+"-V2.cdg";a.click()}
function resetDecoder(){M.decoder={frame:new Uint8Array(CDGW*CDGH),pal:Array.from({length:16},function(){return[0,0,0]}),border:0,trans:0,mem:0,ho:0,vo:0};M.processed=0}
function packet(p){var d=M.decoder;if(!p||p.length!==24||(p[0]&63)!==9)return;var ins=p[1]&63,a=[];for(var i=0;i<16;i++)a[i]=p[4+i]&63;
  if(ins===1){d.mem=a[0]&15;d.frame.fill(d.mem)}else if(ins===2)d.border=a[0]&15;else if(ins===6||ins===38){var c0=a[0]&15,c1=a[1]&15,y0=(a[2]&31)*12,x0=(a[3]&63)*6;for(var y=0;y<12;y++)for(var q=0;q<6;q++){var xx=x0+q,yy=y0+y;if(xx<CDGW&&yy<CDGH){var v=(a[4+y]&(1<<(5-q)))?c1:c0,ii=yy*CDGW+xx;d.frame[ii]=ins===38?(d.frame[ii]^v):v}}}
  else if(ins===30||ins===31){var base=ins===30?0:8;for(var k=0;k<8;k++){var aa=a[k*2],bb=a[k*2+1];d.pal[base+k]=[((aa&60)>>2)*17,(((aa&3)<<2)|((bb&48)>>4))*17,(bb&15)*17]}}else if(ins===28)d.trans=a[0]&15
}
function renderCdg(t){if(!M.cdg)return;var target=Math.min(Math.floor(M.cdg.length/24),Math.floor(Math.max(0,t)*PPS));if(target<M.processed)resetDecoder();for(var i=M.processed;i<target;i++)packet(M.cdg.subarray(i*24,i*24+24));M.processed=target;
  var c=el("v2canvas"),x=c.getContext("2d"),im=x.createImageData(300,216),o=im.data,d=M.decoder;for(var y=0;y<216;y++)for(var q=0;q<300;q++){var ci=d.frame[y*300+q]&15,rgb=d.pal[ci]||[0,0,0];var z=(y*300+q)*4;o[z]=rgb[0];o[z+1]=rgb[1];o[z+2]=rgb[2];o[z+3]=255}x.putImageData(im,0,0)
}
function loop(){try{var t=current(),d=Number(S.audio.duration||S.duration||0);el("v2now").textContent=ftime(t);el("v2dur").textContent=ftime(d);if(d)el("v2seek").value=String(Math.round(t/d*1000));paint();if(M.mode==="karaoke")paintPanel()}catch(_){}requestAnimationFrame(loop)}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",inject,{once:true});else inject();
})();