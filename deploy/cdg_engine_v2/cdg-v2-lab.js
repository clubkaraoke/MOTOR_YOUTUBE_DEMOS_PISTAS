(function(){
"use strict";
const PREFIX="/cdg-v2";
let submitting=false;

function byId(id){return document.getElementById(id)}
function safeCall(name,args){
  try{
    const fn=globalThis[name];
    if(typeof fn==="function") return fn.apply(null,args||[]);
  }catch(_){}
}
function getToken(){
  try{return String(SESSION_TOKEN||"")}catch(_){return localStorage.getItem("consola_session_token")||""}
}
function getVoiceFile(){
  try{if(archivoSeleccionado&&archivoSeleccionado.file)return archivoSeleccionado.file}catch(_){}
  const x=byId("inAudio"); return x&&x.files&&x.files[0]||null;
}
function getVoiceDuration(){
  try{return Number(archivoSeleccionado&&archivoSeleccionado.duracion||0)}catch(_){return 0}
}
function getInstFile(){
  try{if(instrumentalSeleccionado&&instrumentalSeleccionado.file)return instrumentalSeleccionado.file}catch(_){}
  const x=byId("inInstrumental"); return x&&x.files&&x.files[0]||null;
}
function notify(msg,kind){
  try{if(typeof toast==="function"){toast(msg,kind);return}}catch(_){}
  console.log("[V2 LAB]",msg);
}
function progress(pct,stage,detail){
  try{if(typeof setIaProgress==="function"){setIaProgress(pct,stage,detail);return}}catch(_){}
  const box=byId("uploadProgress"),st=byId("iaProgressStage"),pe=byId("iaProgressPct"),fi=byId("iaProgressFill"),de=byId("iaProgressDetail");
  if(box)box.style.display="block";if(st)st.textContent=stage||"";if(pe)pe.textContent=Math.round(pct||0)+"%";if(fi)fi.style.width=Math.max(0,Math.min(100,pct||0))+"%";if(de)de.textContent=detail||"";
}
function ensureLabCss(){
  if(document.getElementById("v2LabEssentialCss"))return;
  const st=document.createElement("style");st.id="v2LabEssentialCss";
  st.textContent="#dropboxNuevaStatus{display:none!important}.dropbox-dest-card{border-color:rgba(99,214,163,.45)!important}.v2-lab-hidden{display:none!important}";
  document.head.appendChild(st);
}
function applyLabUI(){
  ensureLabCss();
  const modal=byId("modalNueva"); if(!modal)return;
  const name=byId("dropboxDestName"),path=byId("dropboxDestPath"),stat=byId("dropboxNuevaStatus"),pick=byId("btnElegirDropbox");
  if(name&&name.textContent!=="OVH · LAB CDG V2")name.textContent="OVH · LAB CDG V2";
  if(path&&path.textContent!=="/var/lib/djgabo-cdg-v2/jobs/")path.textContent="/var/lib/djgabo-cdg-v2/jobs/";
  if(stat){
    const txt="✓ SOLO LAB · Dropbox / Drive / Sheet desactivados";
    stat.textContent=txt;
    stat.style.color="#63d6a3";
    stat.style.display="block";
  }
  if(pick)pick.style.display="none";
  const label=[...modal.querySelectorAll("label")].find(x=>/Destino Dropbox actual/i.test(x.textContent||""));
  if(label){
    const html='Destino de esta prueba <span style="color:#63d6a3;font-size:10px">· SOLO OVH LAB · nada se publica</span>';
    if(label.innerHTML!==html)label.innerHTML=html;
  }
  const btn=byId("btnEnviarNueva");
  if(btn&&!submitting&&btn.textContent!=="✨ Crear y sincronizar con IA · LAB V2")btn.textContent="✨ Crear y sincronizar con IA · LAB V2";
}
async function ensureQrReader(){
  if(typeof window.jsQR==="function")return true;
  return await new Promise((resolve)=>{
    const old=document.querySelector('script[data-v2-jsqr="1"]');
    if(old){old.addEventListener("load",()=>resolve(typeof window.jsQR==="function"),{once:true});setTimeout(()=>resolve(typeof window.jsQR==="function"),1500);return}
    const s=document.createElement("script");s.src=PREFIX+"/api/vendor/jsQR.js";s.async=true;s.dataset.v2Jsqr="1";
    s.onload=()=>{
      try{_jsQrCompatPromise=null}catch(_){}
      resolve(typeof window.jsQR==="function");
    };
    s.onerror=()=>resolve(false);document.head.appendChild(s);
  });
}
async function apiJson(path,method,body,headers){
  const r=await fetch(PREFIX+path,{
    method:method||"GET",
    headers:Object.assign({"Content-Type":"application/json"},headers||{}),
    body:body===undefined?undefined:JSON.stringify(body),
    cache:"no-store"
  });
  let d={};try{d=await r.json()}catch(_){}
  if(!r.ok||d.ok===false)throw new Error(d.error||("HTTP "+r.status));
  return d;
}
async function uploadBlobChunks(uploadId,kind,file,chunkSize,state){
  let offset=0;
  while(offset<file.size){
    const end=Math.min(file.size,offset+chunkSize);
    const blob=file.slice(offset,end);
    const r=await fetch(PREFIX+"/api/v2/uploads/"+encodeURIComponent(uploadId)+"/"+kind,{
      method:"PUT",
      headers:{
        "Content-Type":"application/octet-stream",
        "X-Session-Token":getToken(),
        "X-Upload-Offset":String(offset)
      },
      body:blob,
      cache:"no-store"
    });
    let d={};try{d=await r.json()}catch(_){}
    if(!r.ok||d.ok===false){
      if(r.status===409&&Number.isFinite(Number(d.expected_offset))){
        offset=Number(d.expected_offset);continue;
      }
      throw new Error(d.error||("Error subiendo "+kind+" ("+r.status+")"));
    }
    offset=Number(d.received||end);
    state.sent += blob.size;
    const frac=state.sent/Math.max(1,state.total);
    const btn=byId("btnEnviarNueva");
    if(btn)btn.textContent="OVH "+Math.round(frac*100)+"%";
    progress(
      4+frac*44,
      "Subiendo al OVH del LAB…",
      (state.sent/1048576).toFixed(1)+" / "+(state.total/1048576).toFixed(1)+" MB · bloques de "+Math.round(chunkSize/1048576)+" MB"
    );
  }
}
async function createChunkedLab(artist,title,lyrics,voice,inst,voiceDuration){
  const init=await apiJson("/api/v2/uploads/init","POST",{
    token:getToken(),artist:artist,title:title,lyrics:lyrics,
    voice_name:voice.name,voice_size:voice.size,
    instrumental_name:inst.name,instrumental_size:inst.size,
    voice_duration:Number(voiceDuration||0)
  });
  const state={sent:0,total:voice.size+inst.size};
  try{
    await uploadBlobChunks(init.upload_id,"voice",voice,Number(init.chunk_size||8388608),state);
    await uploadBlobChunks(init.upload_id,"instrumental",inst,Number(init.chunk_size||8388608),state);
    progress(49,"✓ Archivos recibidos en OVH","Creando trabajo aislado…");
    return await apiJson("/api/v2/uploads/"+encodeURIComponent(init.upload_id)+"/finalize","POST",{token:getToken()});
  }catch(e){
    try{
      await fetch(PREFIX+"/api/v2/uploads/"+encodeURIComponent(init.upload_id),{
        method:"DELETE",headers:{"X-Session-Token":getToken()},cache:"no-store"
      });
    }catch(_){}
    throw e;
  }
}
async function pollTask(taskId){
  try{
    if(typeof pollProductionIaTask==="function")return await pollProductionIaTask(taskId);
  }catch(_){}
  for(let i=0;i<2400;i++){
    const r=await fetch(PREFIX+"/api/ai/tasks/"+encodeURIComponent(taskId)+"?session_token="+encodeURIComponent(getToken()),{cache:"no-store"});
    const d=await r.json();if(!r.ok||d.ok===false)throw new Error(d.error||"No pude consultar ElevenLabs.");
    const t=d.task||{};progress(Number(t.progress||52),t.stage||"ElevenLabs Scribe v2…",t.status||"");
    if(t.status==="done")return t.result||{};
    if(t.status==="error")throw new Error(t.error||"Scribe v2 falló.");
    await new Promise(res=>setTimeout(res,500));
  }
  throw new Error("ElevenLabs tardó demasiado.");
}
async function submitLab(e){
  const btn=e.target&&e.target.closest?e.target.closest("#btnEnviarNueva"):null;if(!btn||submitting)return;
  e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();
  const artist=String(byId("inArtista")&&byId("inArtista").value||"").trim();
  const title=String(byId("inTitulo")&&byId("inTitulo").value||"").trim();
  const lyrics=String(byId("inLetra")&&byId("inLetra").value||"").trim();
  const voice=getVoiceFile(),inst=getInstFile();
  if(!artist)return notify("Escribe el nombre del artista.","error");
  if(!title)return notify("Escribe el título de la canción.","error");
  if(!voice)return notify("Selecciona o pega por QR la VOZ MP3.","error");
  if(!inst)return notify("Selecciona o pega por QR el INSTRUMENTAL WAV.","error");
  if(!/\.mp3$/i.test(voice.name||""))return notify("La VOZ debe ser MP3.","error");
  if(!/\.wav$/i.test(inst.name||""))return notify("El INSTRUMENTAL debe ser WAV.","error");
  submitting=true;btn.disabled=true;btn.textContent="Preparando LAB…";
  const timeEl=byId("iaProgressTime"),started=performance.now();
  const timer=setInterval(()=>{if(timeEl)timeEl.textContent=((performance.now()-started)/1000).toFixed(1)+" s"},100);
  let jid="";
  try{
    progress(2,"Preparando archivos…","Destino: OVH del clon · Dropbox/Drive/Sheet OFF");
    const created=await createChunkedLab(artist,title,lyrics,voice,inst,getVoiceDuration()||0);jid=created.idTrabajo;
    progress(52,"✓ Trabajo LAB guardado","Iniciando ElevenLabs Scribe v2…");
    const sr=await fetch(PREFIX+"/api/jobs/"+encodeURIComponent(jid)+"/ai-sync/start",{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({token:getToken(),use_existing_lyrics:!!lyrics})
    });
    let sd={};try{sd=await sr.json()}catch(_){}
    if(!sr.ok||sd.ok===false)throw new Error(sd.error||"No se pudo iniciar ElevenLabs.");
    const result=await pollTask(sd.task_id);
    progress(100,"✓ Sincronización completada",(result.words||0)+" palabras · guardado sólo en LAB V2");
    notify("Trabajo "+jid+" creado en LAB V2 · ElevenLabs OK");
    try{if(typeof cargarLista==="function")await cargarLista()}catch(_){}
    setTimeout(()=>{
      const m=byId("modalNueva");if(m)m.classList.remove("open");
      try{if(typeof abrirEditor==="function")abrirEditor(jid)}catch(_){}
    },350);
  }catch(err){
    const msg=err&&err.message?err.message:String(err);
    progress(100,"ERROR",jid?("El trabajo "+jid+" quedó guardado en LAB. "+msg):msg);
    notify(jid?("Trabajo "+jid+" guardado en LAB · IA pendiente: "+msg):msg,"error");
  }finally{
    clearInterval(timer);if(timeEl)timeEl.textContent=((performance.now()-started)/1000).toFixed(1)+" s";
    submitting=false;btn.disabled=false;applyLabUI();
  }
}
function init(){
  applyLabUI();
  ensureQrReader().then(ok=>{
    if(ok){
      try{_jsQrCompatPromise=null}catch(_){}
      for(const id of ["qrVozEstado","qrInstrumentalEstado"]){
        const x=byId(id);if(x&&/No pude cargar el lector QR/i.test(x.textContent||""))x.textContent=id==="qrVozEstado"?"Copia el QR de VOZ (MP3) en UVR y pulsa aquí.":"Copia el QR del INSTRUMENTAL (WAV) en UVR y pulsa aquí.";
      }
    }
  });
  document.addEventListener("click",submitLab,true);
  const openBtn=byId("btnNuevaCancion");
  if(openBtn)openBtn.addEventListener("click",()=>setTimeout(applyLabUI,0));
  setTimeout(applyLabUI,500);
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});else init();
})();