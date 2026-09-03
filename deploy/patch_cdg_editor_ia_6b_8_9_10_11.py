#!/usr/bin/env python3
from pathlib import Path
import json, math, re

ROOT=Path('/opt/djgabo-cdg-ia-test')
SERVER=ROOT/'server.py'
EDITOR=ROOT/'editor_v1'/'index.html'
NORMALIZE=ROOT/'renderer'/'normalize.py'
STYLE=ROOT/'renderer'/'style.json'

def must(text, old, new, label, count=1):
    if old not in text:
        raise SystemExit('PATCH_FAIL:'+label)
    return text.replace(old,new,count)

# ---------------- server.py ----------------
s=SERVER.read_text(encoding='utf-8')

if "def _ai_visual_units(" not in s:
    a=s.find("def _ai_segment_scribe_words(items):")
    b=s.find("\ndef _ai_project_from_words",a)
    if a<0 or b<0: raise SystemExit('PATCH_FAIL:ai_segment_bounds')
    helper=r'''def _ai_visual_units(text):
    """Aproxima el ancho visual de Impact sin depender de la UI."""
    total=0.0
    for ch in str(text or '').upper():
        if ch in 'MWÁÉÓÚQG': total+=1.20
        elif ch in 'IÍLJT1.,;:!¡?¿': total+=0.58
        elif ch==' ': total+=0.55
        else: total+=1.0
    return total

def _ai_balance_phrase(tokens, max_units=29.0):
    toks=[dict(t) for t in tokens if str(t.get('text') or '').strip()]
    if not toks: return []
    widths=[_ai_visual_units(t.get('text')) for t in toks]
    spaces=.55
    total=sum(widths)+spaces*max(0,len(toks)-1)
    n_lines=max(1,int(math.ceil(total/max_units)))
    n_lines=min(n_lines,max(1,len(toks)))
    prefix=[0.0]
    for i,w in enumerate(widths):
        prefix.append(prefix[-1]+w+(spaces if i else 0.0))
    target=total/n_lines
    inf=10**18
    dp=[[inf]*(len(toks)+1) for _ in range(n_lines+1)]
    back=[[None]*(len(toks)+1) for _ in range(n_lines+1)]
    dp[0][0]=0.0
    def span(i,j):
        val=prefix[j]-prefix[i]
        if i>0: val-=spaces
        return val
    for ln in range(1,n_lines+1):
        for j in range(ln,len(toks)+1):
            for i in range(ln-1,j):
                w=span(i,j)
                if w>max_units*1.12: continue
                count=j-i
                penalty=(w-target)**2
                if count==1 and len(toks)>2: penalty+=28.0
                last_txt=str(toks[j-1].get('text') or '')
                if re.search(r'[,;:]$',last_txt): penalty-=2.5
                if re.search(r'[.!?…]["”’\']?$',last_txt): penalty-=4.0
                score=dp[ln-1][i]+penalty
                if score<dp[ln][j]:
                    dp[ln][j]=score; back[ln][j]=i
    if not math.isfinite(dp[n_lines][len(toks)]) or dp[n_lines][len(toks)]>=inf/2:
        out=[]; cur=[]
        for t in toks:
            probe=cur+[t]
            txt=' '.join(str(x.get('text') or '').strip() for x in probe)
            if cur and _ai_visual_units(txt)>max_units:
                out.append(cur); cur=[t]
            else: cur=probe
        if cur: out.append(cur)
        return out
    cuts=[]; j=len(toks)
    for ln in range(n_lines,0,-1):
        i=back[ln][j]
        if i is None: return [toks]
        cuts.append((i,j)); j=i
    cuts.reverse()
    return [toks[i:j] for i,j in cuts]

def _ai_segment_scribe_words(items):
    """Automaquetado IA: frase -> líneas equilibradas; pausas >=2 s -> nueva pantalla."""
    clean=[dict(w) for w in (items or []) if str(w.get('text') or '').strip()]
    if not clean: return []
    groups=[]; phrase=[]
    SMART_PAGE_GAP=2.0
    PHRASE_GAP=.90
    def flush_phrase(page_break=False):
        nonlocal phrase
        if phrase:
            for line in _ai_balance_phrase(phrase):
                groups.append(('line',line))
            phrase=[]
        if page_break and groups and groups[-1][0]!='break':
            groups.append(('break',[]))
    for idx,item in enumerate(clean):
        phrase.append(item)
        txt=str(item.get('text') or '').strip()
        nxt=clean[idx+1] if idx+1<len(clean) else None
        gap=0.0
        if nxt is not None:
            try: gap=max(0.0,float(nxt.get('start'))-float(item.get('end')))
            except Exception: gap=0.0
        strong=bool(re.search(r'[.!?…]["”’\']?$',txt))
        phrase_cut=(nxt is None) or strong or gap>=PHRASE_GAP
        if phrase_cut:
            flush_phrase(page_break=(gap>=SMART_PAGE_GAP))
    flush_phrase(False)
    while groups and groups[-1][0]=='break': groups.pop()
    return groups
'''
    s=s[:a]+helper+s[b:]

s=s.replace("'format_version':2,","'format_version':3,")
s=s.replace("'line_mode':'master' if source_mode=='compare_master' else 'scribe_punctuation_pauses',",
            "'line_mode':'master' if source_mode=='compare_master' else 'balanced_visual_v3',")

if "@app.post('/api/jobs/<jid>/ai-align-block')" not in s:
    marker="\ndef _render_set(task_id, **kw):"
    route=r'''
@app.post('/api/jobs/<jid>/ai-align-block')
def ai_align_block(jid):
    if not TEST_MODE:
        return jsonify(ok=False,error='Alineación IA de bloque disponible sólo en el clon TEST.'),403
    d=request.get_json() or {}
    try:
        session(d.get('token'),'ADMIN')
        words=d.get('words') or []
        if not isinstance(words,list) or not words:
            raise ValueError('Selecciona primero las palabras del bloque.')
        clean=[]
        for item in words:
            if not isinstance(item,dict): continue
            wid=str(item.get('id') or '').strip(); txt=str(item.get('text') or '').strip()
            if wid and txt: clean.append({'id':wid,'text':txt})
        if not clean: raise ValueError('La selección no contiene texto utilizable.')
        clip_start=max(0.0,float(d.get('clip_start') or 0))
        clip_end=float(d.get('clip_end') or 0)
        if clip_end<=clip_start+.25:
            raise ValueError('No pude determinar el tramo de audio del bloque.')
        if clip_end-clip_start>180:
            raise ValueError('El bloque es demasiado largo. Selecciona un tramo menor de 3 minutos.')
        with db() as c: job=dict(jobrow(c,jid))
        if (job.get('origin') or '')=='HISTORICO_DRIVE':
            raise ValueError('Esta función IA de bloque está habilitada por ahora para trabajos nuevos del clon.')
        source=JOBS/jid/job['voice_filename']
        if not source.is_file(): raise ValueError('No encuentro la pista de voz del trabajo.')
        with tempfile.TemporaryDirectory(prefix='karaoke_block_ai_') as td0:
            td=Path(td0); clip=td/'block.wav'
            dur=clip_end-clip_start
            cmd=['ffmpeg','-hide_banner','-loglevel','error','-i',str(source),
                 '-ss',f'{clip_start:.6f}','-t',f'{dur:.6f}',
                 '-vn','-ac','1','-ar','44100','-c:a','pcm_s16le',str(clip)]
            proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=180)
            if proc.returncode!=0 or not clip.is_file() or clip.stat().st_size<1000:
                raise ValueError('No pude preparar el fragmento de audio: '+(proc.stderr or '')[-500:])
            text_block=' '.join(x['text'] for x in clean)
            with clip.open('rb') as fh:
                rr=requests.post(
                    'http://127.0.0.1:8097/api/elevenlabs/forced-align',
                    files={'audio':('block.wav',fh,'audio/wav')},
                    data={'text':text_block},
                    timeout=(30,1200)
                )
            if not rr.ok:
                try: detail=rr.json().get('detail') or rr.text[:800]
                except Exception: detail=rr.text[:800]
                raise ValueError('ElevenLabs no pudo alinear el bloque: '+str(detail))
            payload=rr.json(); aligned=payload.get('words') or []
            if len(aligned)!=len(clean):
                raise ValueError(
                    f'ElevenLabs devolvió {len(aligned)} palabras para una selección de {len(clean)}. '
                    'No aplicaré timings ambiguos; ajusta el texto del bloque y vuelve a intentar.'
                )
            updates=[]
            for srcw,aw in zip(clean,aligned):
                a=float(aw.get('start'))+clip_start
                b=float(aw.get('end'))+clip_start
                if b<=a: b=a+.03
                updates.append({'id':srcw['id'],'text':srcw['text'],'start':round(a,6),'end':round(b,6),'loss':aw.get('loss')})
            with db() as c: log(c,jid,'ELEVENLABS · ALINEAR BLOQUE',f"{len(updates)} palabras · {clip_start:.2f}-{clip_end:.2f}")
            return jsonify(ok=True,engine='elevenlabs-forced-alignment',updates=updates,
                           clip_start=clip_start,clip_end=clip_end,loss=payload.get('loss'),elapsed_s=payload.get('elapsed_s'))
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        app.logger.exception('ai-align-block %s',jid)
        return jsonify(ok=False,error='No se pudo alinear el bloque: '+str(e)),500

'''
    if marker not in s: raise SystemExit('PATCH_FAIL:render_marker')
    s=s.replace(marker,route+marker)

SERVER.write_text(s,encoding='utf-8')

# ---------------- editor ----------------
e=EDITOR.read_text(encoding='utf-8')
roles='<button class="hbtn spoken" id="btnSpoken2">HABLADO <span class="roleKey">4</span></button><span style="font-size:10px;color:var(--dimmer);margin-left:6px">Selecciona (clic/Shift/Ctrl+arrastre/L#) → pulsa el rol</span>'
if 'id="btnAiBlock"' not in e:
    repl='<button class="hbtn spoken" id="btnSpoken2">HABLADO <span class="roleKey">4</span></button><button class="hbtn" id="btnAiBlock" title="Alinear sólo la selección con ElevenLabs Forced Alignment">✨ IA BLOQUE</button><span style="font-size:10px;color:var(--dimmer);margin-left:6px">Manual = SPACE · IA BLOQUE = sólo la selección</span>'
    e=must(e,roles,repl,'editor_ai_button')
if "#btnAiBlock{" not in e:
    e=e.replace("</style>",'''
#btnAiBlock{border-color:rgba(139,92,246,.55);color:#c7b5ff;background:rgba(139,92,246,.08)}
#btnAiBlock:hover{border-color:#9f7aea;color:#fff;background:rgba(139,92,246,.16)}
#btnAiBlock.busy{opacity:.65;pointer-events:none}
</style>''',1)

oldcfg='''    uppercase: true, tailSeconds: 0.45,
    instrumental: {label:"INSTRUMENTAL", dot:"\\u25CF", dots:4,
                   span:6.0, lead:4.0, minGap:6.0,
                   spokenMin:4.0, spokenLead:3.0, spokenJoin:0.75},'''
newcfg='''    uppercase: true, tailSeconds: 0.45, smartPageGap:2.0,
    instrumental: {label:"INSTRUMENTAL", dot:"\\u25CF", dots:4,
                   span:6.0, lead:4.0, minGap:6.0,
                   spokenMin:6.0, spokenLead:4.0, spokenJoin:0.75},'''
if oldcfg in e: e=e.replace(oldcfg,newcfg,1)

ps=e.find("function pvWrap(){"); pe=e.find("\n}\n\n/* El bloque de instrumental",ps)
if ps<0 or pe<0: raise SystemExit('PATCH_FAIL:pvWrap_bounds')
if "segStart-prevVisibleEnd" not in e[ps:pe]:
    newpv=r'''function pvWrap(){
  const lpp=PV.cfg.linesPerPage;
  const blocks=[]; let block=[]; let prevVisibleEnd=null;
  const flushBlock=()=>{ if(block.length){ blocks.push(block); block=[]; } };
  for(const seg of S.doc.segments){
    if(seg.kind==="break"){ flushBlock(); prevVisibleEnd=null; continue; }
    const renderWords=(seg.words||[]).filter(w=>!w.spoken);
    if(!renderWords.length) continue;
    if(renderWords.some(w=>w.start_time===null)){ flushBlock(); break; }
    const segStart=renderWords[0].start_time;
    if(prevVisibleEnd!==null && segStart!==null && (segStart-prevVisibleEnd)>=PV.cfg.smartPageGap){ flushBlock(); }
    let cur=[];
    for(const w of renderWords){
      const probe=cur.concat([w]),txt=probe.map(x=>pvText(x.text)).join(" ");
      if(cur.length && advWidth(txt)>PV.WRAP){ block.push(cur); cur=[w]; } else cur=probe;
    }
    if(cur.length) block.push(cur);
    prevVisibleEnd=Math.max(...renderWords.map(w=>w.end_time??w.start_time));
  }
  flushBlock();
  const out=[]; const padPage=()=>{ while(out.length%lpp) out.push([]); };
  for(const b of blocks){
    if(out.length) padPage();
    for(let i=0;i<b.length;i+=lpp){
      if(i>0) padPage();
      const chunk=b.slice(i,i+lpp),top=Math.floor((lpp-chunk.length)/2),bottom=lpp-chunk.length-top;
      for(let j=0;j<top;j++) out.push([]);
      out.push(...chunk);
      for(let j=0;j<bottom;j++) out.push([]);
    }
  }
  while(out.length && !out[out.length-1].length) out.pop();
  return out;
}'''
    e=e[:ps]+newpv+e[pe+2:]

oldplan='''  // geometría y sílabas
  const flatSync = [];
  for(const line of lines) for(const w of line) flatSync.push(cs(w.start_time));

  const syl = [], geom = [];
  let si = 0;
  for(let li=0; li<lines.length; li++){
    const line = lines[li];
    const txt = line.map(w => pvText(w.text)).join(" ");
    const lw = advWidth(txt);
    const x0 = Math.floor((PV.W - lw) / 2);
    const y  = PV.cfg.row * PV.TILE + (li % lpp) * lth * PV.TILE;

    const boxes = [];
    let cx0 = x0;
    for(const w of line){
      const t = pvText(w.text), ww = advWidth(t);
      boxes.push({text:t, x:cx0, w:ww, noSweep: !!w._label, role:w.vocal_role||null});
      cx0 += ww + advWidth(" ");
    }
    geom.push({y, boxes, text:txt});

    const ls = flatSync.slice(si, si + line.length);
    si += line.length;
    if(ls.length){
      let next = ls[ls.length-1] + 45;
      if(si < flatSync.length) next = Math.min(next, flatSync[si]);
      ls.push(next);
      for(let k=0; k<line.length; k++){
        syl.push({li, si:k, s: ls[k]*3, e: ls[k+1]*3});
      }
    }
  }'''
newplan='''  const syl = [], geom = [];
  const flatWords=lines.flat().filter(Boolean);
  const fallbackEnd=(w)=>{
    if(w.end_time!==null && w.end_time!==undefined && w.end_time>w.start_time) return w.end_time;
    const idx=flatWords.indexOf(w), nxt=idx>=0?flatWords[idx+1]:null;
    if(nxt && nxt.start_time>w.start_time) return Math.min(nxt.start_time,w.start_time+0.45);
    return w.start_time+0.45;
  };
  for(let li=0; li<lines.length; li++){
    const line = lines[li];
    const txt = line.map(w => pvText(w.text)).join(" ");
    const lw = advWidth(txt);
    const x0 = Math.floor((PV.W - lw) / 2);
    const y  = PV.cfg.row * PV.TILE + (li % lpp) * lth * PV.TILE;
    const boxes = [];
    let cx0 = x0;
    for(const w of line){
      const t = pvText(w.text), ww = advWidth(t);
      boxes.push({text:t, x:cx0, w:ww, noSweep: !!w._label, role:w.vocal_role||null});
      cx0 += ww + advWidth(" ");
    }
    geom.push({y, boxes, text:txt});
    for(let k=0;k<line.length;k++){
      const w=line[k];
      if(w.start_time===null||w.start_time===undefined) continue;
      const st=cs(w.start_time)*3;
      const en=Math.max(st+3,cs(fallbackEnd(w))*3);
      syl.push({li,si:k,s:st,e:en});
    }
  }'''
if oldplan in e: e=e.replace(oldplan,newplan,1)

if "async function aiAlignSelectedBlock()" not in e:
    marker="function applyRoleToIndices(indices, role, quiet=false){"
    fn=r'''async function aiAlignSelectedBlock(){
  if(!PANEL_JOB_ID){ toast("Abre el trabajo desde el panel online.",1800); return; }
  const ids=currentRoleSelectionIndices();
  if(!ids.length){ toast("Selecciona el bloque que quieres sincronizar con IA.",2200); return; }
  for(let i=1;i<ids.length;i++){ if(ids[i]!==ids[i-1]+1){ toast("IA BLOQUE necesita una selección continua.",2400); return; } }
  const first=ids[0],last=ids[ids.length-1],selected=S.words.slice(first,last+1);
  const textWords=selected.map(w=>({id:w.id,text:w.text}));
  let clipStart=null,clipEnd=null;
  const already=selected.filter(w=>w.start_time!==null);
  if(already.length){
    clipStart=Math.max(0,Math.min(...already.map(w=>w.start_time))-1.0);
    clipEnd=Math.min(S.duration,Math.max(...already.map(w=>effectiveTimingEnd(S.words.indexOf(w))??w.start_time))+1.0);
  }else{
    let prev=null,next=null;
    for(let i=first-1;i>=0;i--){if(S.words[i].start_time!==null){prev=S.words[i];break;}}
    for(let i=last+1;i<S.words.length;i++){if(S.words[i].start_time!==null){next=S.words[i];break;}}
    if(prev) clipStart=Math.max(0,(prev.end_time??prev.start_time)+0.05);
    if(next) clipEnd=Math.min(S.duration,next.start_time-0.05);
  }
  if(clipStart===null||clipEnd===null||clipEnd<=clipStart+.25){
    toast("No pude encerrar el bloque entre timings conocidos. Marca al menos sus límites manualmente.",3200);return;
  }
  const btn=$("#btnAiBlock"),old=btn.textContent;btn.classList.add("busy");btn.textContent="✨ ALINEANDO…";
  setStatus("ElevenLabs alineando sólo el bloque…","work");
  try{
    await panelSaveProject(buildExport());
    const r=await fetch('/cdg-editor-ia/api/jobs/'+encodeURIComponent(PANEL_JOB_ID)+'/ai-align-block',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:PANEL_TOKEN,words:textWords,clip_start:clipStart,clip_end:clipEnd})
    });
    let j={};try{j=await r.json()}catch(_){}
    if(!r.ok||j.ok===false)throw new Error(j.error||('Error '+r.status));
    push(docSnapshot());
    const byId=new Map((j.updates||[]).map(x=>[x.id,x]));
    for(const w of selected){
      const u=byId.get(w.id);if(!u)continue;
      w.start_time=Number(u.start);w.end_time=Number(u.end);w.locked=false;w.provisional=false;
      w.ai_status="green";w.ai_match_type="forced_alignment_block";w.ai_loss=u.loss;
    }
    S.doc.ai=S.doc.ai||{};S.doc.ai.block_alignments=S.doc.ai.block_alignments||[];
    S.doc.ai.block_alignments.push({engine:"elevenlabs-forced-alignment",at:new Date().toISOString(),words:selected.length,clip_start:j.clip_start,clip_end:j.clip_end,loss:j.loss});
    const a=Number(j.clip_start),b=Number(j.clip_end);
    S.doc.ai.voice_gaps=(S.doc.ai.voice_gaps||[]).filter(g=>{
      const ov=Math.max(0,Math.min(b,Number(g.end))-Math.max(a,Number(g.start))),gd=Math.max(.01,Number(g.end)-Number(g.start));
      return ov/gd<0.45;
    });
    reindex();S.dirty=true;pvInvalidate();paintNow();paintLyrics();paintCounter();draw();scheduleSave();
    const ft=selected[0]?.start_time;if(ft!==null&&ft!==undefined){centerOn(ft);S.audio.currentTime=Math.max(0,ft-.5);}
    setStatus("Bloque sincronizado con ElevenLabs","good");
    toast("✓ "+selected.length+" palabras sincronizadas con IA · el resto no se tocó",3200);
  }catch(err){
    setStatus("No se pudo sincronizar el bloque","bad");toast((err.message||String(err)).slice(0,420),5000);
  }finally{
    btn.classList.remove("busy");btn.textContent=old;setTimeout(()=>setStatus(""),2600);
  }
}

'''
    if marker not in e: raise SystemExit('PATCH_FAIL:ai_block_marker')
    e=e.replace(marker,fn+marker,1)

wire='$("#btnSpoken2").onclick=()=>roleButtonPress("hablado");'
if '$("#btnAiBlock").onclick' not in e:
    e=must(e,wire,wire+'\n$("#btnAiBlock").onclick=()=>aiAlignSelectedBlock();','ai_block_wire')

EDITOR.write_text(e,encoding='utf-8')

# ---------------- normalize ----------------
n=NORMALIZE.read_text(encoding='utf-8')
if "def smart_page_breaks(" not in n:
    marker="\ndef center_stanza_pages("
    fn=r'''
def smart_page_breaks(visual: list[list[dict]], gap_seconds: float = 2.0) -> list[list[dict]]:
    out=[]; prev_line=None; explicit_break=False
    for line in visual:
        if not line:
            if out and out[-1]: out.append([])
            prev_line=None; explicit_break=True; continue
        if prev_line is not None and not explicit_break:
            prev_end=max(float(w.get("end_time") or w["start_time"]) for w in prev_line)
            start=float(line[0]["start_time"])
            if start-prev_end>=float(gap_seconds) and out and out[-1]: out.append([])
        out.append(line); prev_line=line; explicit_break=False
    while out and not out[-1]: out.pop()
    return out

'''
    if marker not in n: raise SystemExit('PATCH_FAIL:smart_break_marker')
    n=n.replace(marker,fn+marker,1)

oldwipe='''        if wi + 1 < len(visual[li]):
            end = visual[li][wi + 1]["start_time"]
        else:
            end = w["start_time"] + tail
            nxt = next((visual[l][i]["start_time"] for l, i in flat[k + 1:]), None)
            if nxt is not None:
                end = min(end, nxt)
        w["_wipe"] = max(0.01, end - w["start_time"])'''
newwipe='''        explicit_end = w.get("end_time")
        if explicit_end is not None and explicit_end > w["start_time"]:
            end = explicit_end
        elif wi + 1 < len(visual[li]):
            end = visual[li][wi + 1]["start_time"]
        else:
            end = w["start_time"] + tail
            nxt = next((visual[l][i]["start_time"] for l, i in flat[k + 1:]), None)
            if nxt is not None:
                end = min(end, nxt)
        w["_wipe"] = max(0.01, end - w["start_time"])'''
if oldwipe in n: n=n.replace(oldwipe,newwipe,1)

n=n.replace(
'''    visual = wrap_lines(doc, font, upper)
    visual = center_stanza_pages(visual, style["lines_per_page"])''',
'''    visual = wrap_lines(doc, font, upper)
    visual = smart_page_breaks(visual, float(style.get("smart_page_gap_seconds", 2.0)))
    visual = center_stanza_pages(visual, style["lines_per_page"])''',1)
n=n.replace(
'''    spoken_min = float(style.get("spoken_instrumental_min_seconds", 4.0))
    spoken_lead = float(style.get("spoken_instrumental_lead_seconds", 3.0))''',
'''    spoken_min = float(style.get("spoken_instrumental_min_seconds", 6.0))
    spoken_lead = float(style.get("spoken_instrumental_lead_seconds", 4.0))''',1)
NORMALIZE.write_text(n,encoding='utf-8')

# ---------------- style ----------------
d=json.loads(STYLE.read_text(encoding='utf-8'))
d["smart_page_gap_seconds"]=2.0
d["spoken_instrumental_min_seconds"]=6.0
d["spoken_instrumental_lead_seconds"]=4.0
STYLE.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding='utf-8')

print("PATCH_6B_8_9_10_11=OK")
