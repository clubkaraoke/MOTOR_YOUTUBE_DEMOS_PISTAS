(function(){
"use strict";
var PPS=300, CDGW=300, CDGH=216, VX=6, VY=12, VW=288, VH=192;
var M={mode:"karaoke",timeline:null,cdg:null,decoder:null,processed:0,busy:false,lastProject:"",clearMode:"delayed",previewScale:2,features:{opening:true,instrumental:true,lead_in:true,ending:true}};
function el(id){return document.getElementById(id)}
function token(){try{return String(PANEL_TOKEN||"")}catch(_){return ""}}
function job(){try{return String(PANEL_JOB_ID||"")}catch(_){return ""}}
function api(path){return "/cdg-v2"+path}
function ftime(t){t=Number(t)||0;var m=Math.floor(t/60),s=t-m*60;return m+":"+(s<10?"0":"")+s.toFixed(3)}
function roleColor(r){r=String(r||"").toLowerCase();var st=M.timeline&&M.timeline.style||{},m=st.role_active||{};if(r==="hombre"||r==="male")return m.male||"#32B7FF";if(r==="mujer"||r==="female")return m.female||"#FF4FA3";if(r==="duo"||r==="duet"||r==="dúo")return m.duet||"#7ED957";return m.none||"#F2A900"}
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
function opts(){var x={clear_mode:M.clearMode,show_title_artist:!!M.features.opening,show_instrumental:!!M.features.instrumental,show_lead_in:!!M.features.lead_in,show_ending:!!M.features.ending};try{x.lines_per_screen=Number(S.cfg.linesPerPage||6);x.font_size=Number(S.cfg.fontSize||18);x.stroke_width=Number(S.cfg.strokeWidth||1);x.lyric_y_offset=Number(S.cfg.lyricYOffset||0)}catch(_){}return x}
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
  ".v2body{padding:0 9px 9px;display:flex;flex-direction:column;gap:8px;min-height:0;flex:1}.v2options{display:grid;grid-template-columns:1fr 1fr;gap:7px}.v2opt{display:flex;flex-direction:column;gap:3px;font:9px monospace;color:#93a1b4}.v2opt select{background:#171c25;color:#fff;border:1px solid #343d4d;border-radius:5px;padding:6px;font:700 10px Arial}.v2layers{border:1px solid #303848;border-radius:6px;padding:7px;background:#131821}.v2layersTitle{display:flex;justify-content:space-between;align-items:center;font:800 9px monospace;color:#b89cff;margin-bottom:6px}.v2checks{display:grid;grid-template-columns:1fr 1fr;gap:5px 8px}.v2check{display:flex;gap:5px;align-items:center;font:700 9px Arial;color:#dbe2ed}.v2check input{accent-color:#8b5cf6}.v2ab{display:flex;gap:5px}.v2mini{border:1px solid #3a4557;background:#1a202a;color:#dbe2ed;border-radius:4px;padding:3px 6px;font:700 8px Arial;cursor:pointer}.v2screen{aspect-ratio:300/216;background:#111427;border:1px solid #343b47;display:flex;align-items:center;justify-content:center;overflow:hidden}.v2screen canvas{width:100%;height:100%;image-rendering:auto}"+
  ".v2meta{font:10px monospace;color:#98a5b7;line-height:1.45;min-height:30px}.v2actions{display:grid;grid-template-columns:1fr 1fr;gap:7px}.v2btn{border:1px solid #414a59;background:#1a202a;color:#fff;border-radius:6px;padding:8px;font-weight:800;cursor:pointer}.v2btn.primary{background:#7047d6;border-color:#8b5cf6}.v2btn:disabled{opacity:.45}"+
  "#v2status{font:10px monospace;border-top:1px solid #29303c;padding-top:7px;color:#9aa7b8}#v2status[data-kind=ok]{color:#63d6a3}#v2status[data-kind=bad]{color:#ff7979}#v2status[data-kind=warn]{color:#f2b705}"+
  ".v2scroll{overflow:auto;min-height:0;max-height:360px;border:1px solid #29303c;border-radius:6px}.v2row{padding:7px 8px;border-bottom:1px solid #252c36;font:10px monospace}.v2row b{color:#fff}.v2warn{color:#f2b705}.v2diag{white-space:pre-wrap;padding:9px;font:10px monospace;color:#b9c4d3}"+
  ".v2transport{display:grid;grid-template-columns:58px 1fr 58px;gap:6px;align-items:center;font:10px monospace}.v2transport input{width:100%;accent-color:#8b5cf6}";
  document.head.appendChild(css);
  var box=document.createElement("div");box.id="djV2";box.innerHTML=
  '<div class="v2head"><span class="v2badge">CDG ENGINE V2 · LAB</span><span class="v2safe">PRODUCCIÓN INTACTA</span></div>'+
  '<div class="v2tabs"><button class="v2tab on" data-v2="karaoke">Karaoke V2</button><button class="v2tab" data-v2="pages">Páginas</button><button class="v2tab" data-v2="diag">Diagnóstico</button><button class="v2tab" data-v2="cdg">CDG V2</button></div>'+
  '<div class="v2body"><div class="v2options"><label class="v2opt">Filas Nomad<select id="v2clear"><option value="eager">EAGER · reemplaza antes</option><option value="delayed" selected>DELAYED · espera más</option><option value="page">PAGE · página completa</option></select></label><label class="v2opt">Resolución preview<select id="v2scale"><option value="1">1× · 300×216</option><option value="2" selected>2× · 600×432</option><option value="4">4× · 1200×864</option></select></label></div>'+
  '<div class="v2layers"><div class="v2layersTitle"><span>CAPAS VISUALES · PRUEBA A/B</span><span class="v2ab"><button class="v2mini" id="v2alloff" type="button">TODO OFF</button><button class="v2mini" id="v2allon" type="button">TODO ON</button></span></div><div class="v2checks"><label class="v2check"><input id="v2opening" type="checkbox" checked> Título + Artista</label><label class="v2check"><input id="v2inst" type="checkbox" checked> Instrumental</label><label class="v2check"><input id="v2lead" type="checkbox" checked> Lead-in &gt;&gt;&gt;</label><label class="v2check"><input id="v2ending" type="checkbox" checked> Ending</label></div></div>'+
  '<div class="v2screen"><canvas id="v2canvas" width="600" height="432"></canvas></div><div id="v2panel"></div>'+
  '<div class="v2transport"><span id="v2now">0:00</span><input id="v2seek" type="range" min="0" max="1000" value="0"><span id="v2dur">0:00</span></div>'+
  '<div class="v2actions"><button class="v2btn" id="v2refresh">↻ Recalcular</button><button class="v2btn primary" id="v2render">⚡ Generar V2</button></div>'+
  '<div id="v2status">Cargando timeline V2…</div></div>';
  host.appendChild(box);
  box.querySelectorAll(".v2tab").forEach(function(b){b.onclick=function(){M.mode=b.dataset.v2;box.querySelectorAll(".v2tab").forEach(function(x){x.classList.toggle("on",x===b)});paintPanel();paint()}});
  el("v2refresh").onclick=function(){refresh(true)};
  el("v2render").onclick=render;
  el("v2clear").onchange=function(e){M.clearMode=String(e.target.value||"delayed");try{S.doc.cdg_settings=Object.assign({},S.doc.cdg_settings||{},{clear_mode:M.clearMode});if(typeof scheduleSave==="function")scheduleSave()}catch(_){}refresh(true)};
  el("v2scale").onchange=function(e){M.previewScale=Math.max(1,Math.min(4,Number(e.target.value)||2));resizeBacking();paint()};
  function saveFeatures(){try{S.doc.cdg_settings=Object.assign({},S.doc.cdg_settings||{});S.doc.cdg_settings.v2_features=Object.assign({},M.features);if(typeof scheduleSave==="function")scheduleSave()}catch(_){}}
  function syncFeatureInputs(){var pairs=[["v2opening","opening"],["v2inst","instrumental"],["v2lead","lead_in"],["v2ending","ending"]];pairs.forEach(function(z){var n=el(z[0]);if(n)n.checked=!!M.features[z[1]]})}
  function featureChanged(){M.features.opening=!!el("v2opening").checked;M.features.instrumental=!!el("v2inst").checked;M.features.lead_in=!!el("v2lead").checked;M.features.ending=!!el("v2ending").checked;saveFeatures();refresh(true)}
  ["v2opening","v2inst","v2lead","v2ending"].forEach(function(id){el(id).onchange=featureChanged});
  el("v2alloff").onclick=function(){M.features={opening:false,instrumental:false,lead_in:false,ending:false};syncFeatureInputs();saveFeatures();refresh(true)};
  el("v2allon").onclick=function(){M.features={opening:true,instrumental:true,lead_in:true,ending:true};syncFeatureInputs();saveFeatures();refresh(true)};
  el("v2seek").oninput=function(e){try{var d=Number(S.audio.duration||S.duration||0);if(d)S.audio.currentTime=d*Number(e.target.value)/1000}catch(_){}paint()};
  syncFeatureInputs();resizeBacking();
  var old=el("btnCdg");if(old){old.textContent="⚡ Generar V2";old.addEventListener("click",function(e){e.preventDefault();e.stopImmediatePropagation();render()},true)}
  document.addEventListener("click",function(e){var t=e.target&&e.target.closest?e.target.closest('[data-phase="3"]'):null;if(t){e.preventDefault();e.stopImmediatePropagation();render()}},true);
  autoRefreshWhenReady();requestAnimationFrame(loop)
}
async function autoRefreshWhenReady(){
  for(var i=0;i<240;i++){
    if(editorReady()){
      try{
        var cfg=S.doc.cdg_settings||{},cm=String(cfg.clear_mode||"delayed").toLowerCase();if(["eager","delayed","page"].includes(cm))M.clearMode=cm;var cs=el("v2clear");if(cs)cs.value=M.clearMode;
        var vf=cfg.v2_features||{};["opening","instrumental","lead_in","ending"].forEach(function(k){if(typeof vf[k]==="boolean")M.features[k]=vf[k]});
        [["v2opening","opening"],["v2inst","instrumental"],["v2lead","lead_in"],["v2ending","ending"]].forEach(function(z){var n=el(z[0]);if(n)n.checked=!!M.features[z[1]]});
      }catch(_){}
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
  try{var p=project();var d=await request("/api/v2/jobs/"+encodeURIComponent(jid)+"/timeline",{project:p,options:opts()});M.timeline=d.timeline;M.cdg=null;resetDecoder();status("Timeline V2 lista · "+String((d.timeline.render_metadata||{}).feature_signature||"A/B")+" · NOMAD "+String(M.clearMode).toUpperCase()+" · START/END conservados.","ok");paintPanel();paint()}
  catch(e){status(e.message||String(e),"bad")}finally{M.busy=false}
}
async function render(){
  if(M.busy)return;var jid=job();if(!jid){status("No hay trabajo del panel.","bad");return}
  M.busy=true;el("v2render").disabled=true;status("Generando CDG V2 real con la misma timeline…","warn");
  try{var p=project();var d=await request("/api/v2/jobs/"+encodeURIComponent(jid)+"/render",{project:p,options:opts()});M.timeline=d.timeline;await loadCdg();M.mode="cdg";document.querySelectorAll(".v2tab").forEach(function(x){x.classList.toggle("on",x.dataset.v2==="cdg")});status("CDG V2 generado · no se publicó a Dropbox.","ok");paintPanel();paint()}
  catch(e){status(e.message||String(e),"bad")}finally{M.busy=false;el("v2render").disabled=false}
}
function current(){try{return Number(S.audio.currentTime||0)}catch(_){return 0}}
function resizeBacking(){
  var c=el("v2canvas");if(!c)return;var sc=Math.max(1,Math.min(4,Number(M.previewScale)||2));
  var w=Math.round(CDGW*sc),h=Math.round(CDGH*sc);
  if(c.width!==w)c.width=w;if(c.height!==h)c.height=h
}
function fontCss(lay){
  var fam=String(lay.font_family||"impact").toLowerCase();
  var f=fam==="arial"?"Arial, sans-serif":fam==="arialbd"?"Arial Black, Arial, sans-serif":"Impact, Arial Black, sans-serif";
  return "900 "+Number(lay.font_size||18)+"px "+f
}
function phaseAt(t,tl){
  var o=tl.opening||{};if(o.enabled&&t>=Number(o.start||0)&&t<Number(o.end||0))return{kind:"OPENING",data:o};
  var e=tl.ending||{};if(e.enabled&&t>=Number(e.start||0)&&t<=Number(e.end||0))return{kind:"ENDING",data:e};
  var ins=(tl.instrumentals||[]).find(function(z){return t>=Number(z.start||0)&&t<Number(z.prepare_at||z.end||0)});if(ins)return{kind:"INSTRUMENTAL",data:ins};
  return{kind:"KARAOKE",data:null}
}
function centerText(x,text,y,size,fill){
  x.save();x.font="900 "+size+"px Impact, Arial Black, sans-serif";x.textAlign="center";x.textBaseline="middle";x.lineJoin="round";x.lineWidth=2.5;x.strokeStyle="#000";x.fillStyle=fill;x.strokeText(text,150,y);x.fillText(text,150,y);x.restore()
}
function drawOpening(x,tl){
  var song=tl.song||{},o=tl.opening||{},title=String(song.title||"TÍTULO").toUpperCase(),artist=String(song.artist||"ARTISTA");
  centerText(x,title,72,22,"#fff");centerText(x,artist,128,18,(tl.style||{}).artist_color||"#F2A900");
  x.save();x.font="10px monospace";x.fillStyle="#9aa7b8";x.textAlign="center";x.fillText(o.rule||"OPENING NOMAD",150,166);x.restore()
}
function drawInstrumental(x,tl,it,t){
  centerText(x,String(it.text||"INSTRUMENTAL"),95,22,(tl.style||{}).instrumental_fill||"#F2A900");
  var remain=Math.max(0,Number(it.end||0)-t);x.save();x.font="700 11px monospace";x.textAlign="center";x.fillStyle="#fff";x.fillText("voz en "+remain.toFixed(1)+" s",150,130);x.restore()
}
function drawEnding(x,tl){
  var e=tl.ending||{},st=tl.style||{};centerText(x,String(e.line1||""),92,Number(e.size||18),st.outro_line1_color||"#fff");centerText(x,String(e.line2||""),124,Number(e.size||18),st.outro_line2_color||"#F2A900")
}
function paint(){
  var c=el("v2canvas");if(!c)return;resizeBacking();var x=c.getContext("2d"),sc=Math.max(1,Math.min(4,Number(M.previewScale)||2));x.setTransform(sc,0,0,sc,0,0);
  var t=current(),tl=M.timeline,lay=tl&&tl.layout||{},bg=lay.background||"#111427";x.fillStyle=bg;x.fillRect(0,0,300,216);
  if(!tl)return;
  if(M.mode==="cdg"&&M.cdg){renderCdg(t);return}
  var ph=phaseAt(t,tl);if(ph.kind==="OPENING"){drawOpening(x,tl);return}if(ph.kind==="INSTRUMENTAL"){drawInstrumental(x,tl,ph.data,t);return}if(ph.kind==="ENDING"){drawEnding(x,tl);return}
  var fs=Number(lay.font_size||18);
  x.font=fontCss(lay);x.textBaseline="middle";x.textAlign="left";x.lineJoin="round";x.lineWidth=Math.max(2,Number(lay.stroke_width||1)*2);
  tl.lines.forEach(function(line){if(t<Number(line.display_at)||t>=Number(line.remove_at))return;
    var ng=line.nomad||{},y=Number.isFinite(Number(ng.y))?(Number(ng.y)+Math.max(1,Number(ng.height||fs))/2):108,parts=line.words||[],total=0;
    parts.forEach(function(w,i){total+=x.measureText(w.text).width+(i?x.measureText(" ").width:0)});
    // Preview híbrido: Nomad manda Y/slot/página/timing; el navegador centra
    // X con SU propia métrica. Así no mezclamos x rasterizada por PIL con
    // ancho medido por Canvas, que era lo que corría el bloque a un costado.
    var fit=Math.min(1,284/Math.max(1,total)),px=(300-total)/2;
    x.save();if(fit<1){x.translate(150,0);x.scale(fit,1);x.translate(-150,0)}
    parts.forEach(function(w,i){if(i)px+=x.measureText(" ").width;var ww=x.measureText(w.text).width;x.strokeStyle="#000";x.fillStyle="#fff";x.strokeText(w.text,px,y);x.fillText(w.text,px,y);
      var q=t<=Number(w.start)?0:t>=Number(w.end)?1:(t-Number(w.start))/Math.max(.001,Number(w.end)-Number(w.start));if(q>0){x.save();x.beginPath();x.rect(px-2,y-fs,ww*q+4,fs*2);x.clip();x.strokeStyle="#000";x.fillStyle=roleColor(w.role);x.strokeText(w.text,px,y);x.fillText(w.text,px,y);x.restore()}px+=ww});
    x.restore();
  });
}
function paintPanel(){
  var p=el("v2panel"),tl=M.timeline;if(!p)return;if(!tl){p.innerHTML='<div class="v2meta">Sin timeline.</div>';return}
  var mode=String((tl.layout||{}).clear_mode||M.clearMode).toUpperCase(),t=current(),ph=phaseAt(t,tl);
  if(M.mode==="karaoke"){
    var act=tl.lines.find(function(l){return t>=Number(l.display_at)&&t<Number(l.remove_at)&&!(l.words||[]).every(function(w){return w.synthetic})});
    var phaseTxt=ph.kind!=="KARAOKE"?('<b>'+ph.kind+'</b><br>'):"";
    p.innerHTML='<div class="v2meta">'+phaseTxt+(act?('<b>'+act.text+'</b><br>pantalla '+act.page_index+' · slot '+act.slot+' · NOMAD draw '+ftime(act.display_at)+' · erase '+ftime(act.remove_at)+'<br>voz '+ftime(act.voice_start||act.sweep_start)+' → '+ftime(act.voice_end||act.sweep_end)):'Esperando línea activa')+'<br>FILAS: NOMAD '+mode+' · FONDO #111427 · OFFSET OCULTO: 0.000 s · '+tl.source_word_count+' palabras</div>';return
  }
  if(M.mode==="pages"){p.innerHTML='<div class="v2scroll">'+tl.lines.map(function(l){var n=l.nomad||{},syn=(l.words||[]).some(function(w){return w.synthetic})?' · LEAD-IN':'';return '<div class="v2row"><b>P'+l.page_index+' · L'+l.slot+' · NOMAD '+mode+syn+'</b> '+l.text+'<br>draw '+ftime(l.display_at)+' ['+(n.line_draw_frame??"?")+'f] · sweep '+ftime(l.sweep_start)+'–'+ftime(l.sweep_end)+' · erase '+ftime(l.remove_at)+'</div>'}).join("")+'</div>';return}
  if(M.mode==="diag"){var warn=tl.warnings||[],rm=tl.render_metadata||{},lay=tl.layout||{};p.innerHTML='<div class="v2scroll"><div class="v2diag">ENGINE: '+tl.engine+'\nUPSTREAM: '+tl.upstream_commit+'\nROW SCHEDULER: '+(rm.cdg_row_scheduler||lay.row_scheduler||"N/A")+'\nCLEAR MODE: '+(lay.clear_mode||"N/A")+'\nBACKGROUND: '+(lay.background||"N/A")+'\nA/B: '+((tl.render_metadata||{}).feature_signature||"N/A")+'\nTÍTULO+ARTISTA: '+(((tl.features||{}).opening)?"ON":"OFF")+'\nINSTRUMENTAL: '+(((tl.features||{}).instrumental)?"ON":"OFF")+' · detectados '+(tl.instrumentals||[]).length+' · GAP=END→START\nLEAD-IN: '+(((tl.features||{}).lead_in)?"ON":"OFF")+' · '+(tl.lead_ins||[]).length+'\nENDING: '+(((tl.features||{}).ending)?"ON":"OFF")+'\nPREVIEW X: CENTRADO EN BROWSER · Y: NOMAD\nSTART/END INMUTABLES: SI\nINTRO_DELAY: 0.000 s\nSYNC_OFFSET: 0.000 s\n+2 s INSTRUMENTAL OCULTO: NO\nMP4: NO IMPLEMENTADO\nLINEAS: '+tl.rendered_line_count+'\nWARNINGS: '+warn.length+'\n\n'+warn.map(function(w){return '⚠ '+(w.text||w.kind)+' · '+(w.detail||"")}).join("\n")+'</div></div>';return}
  p.innerHTML='<div class="v2meta">CDG real 300×216 · 300 paquetes/s · NOMAD '+mode+'.<br><button class="v2btn" id="v2download" type="button">Descargar CDG V2</button></div>';setTimeout(function(){var b=el("v2download");if(b)b.onclick=download},0)
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
function renderCdg(t){
  if(!M.cdg)return;var target=Math.min(Math.floor(M.cdg.length/24),Math.floor(Math.max(0,t)*PPS));if(target<M.processed)resetDecoder();for(var i=M.processed;i<target;i++)packet(M.cdg.subarray(i*24,i*24+24));M.processed=target;
  var c=el("v2canvas"),x=c.getContext("2d"),d=M.decoder,off=renderCdg._off||(renderCdg._off=document.createElement("canvas"));off.width=300;off.height=216;var ox=off.getContext("2d"),im=ox.createImageData(300,216),o=im.data;
  for(var y=0;y<216;y++)for(var q=0;q<300;q++){var ci=d.frame[y*300+q]&15,rgb=d.pal[ci]||[0,0,0],z=(y*300+q)*4;o[z]=rgb[0];o[z+1]=rgb[1];o[z+2]=rgb[2];o[z+3]=255}ox.putImageData(im,0,0);
  var sc=Math.max(1,Math.min(4,Number(M.previewScale)||2));x.setTransform(sc,0,0,sc,0,0);x.imageSmoothingEnabled=false;x.drawImage(off,0,0,300,216)
}
function loop(){try{var t=current(),d=Number(S.audio.duration||S.duration||0);el("v2now").textContent=ftime(t);el("v2dur").textContent=ftime(d);if(d)el("v2seek").value=String(Math.round(t/d*1000));paint();if(M.mode==="karaoke")paintPanel()}catch(_){}requestAnimationFrame(loop)}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",inject,{once:true});else inject();
})();