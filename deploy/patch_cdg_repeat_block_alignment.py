#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path('/opt/djgabo-cdg-ia-test')
SERVER=ROOT/'server.py'
EDITOR=ROOT/'editor_v1'/'index.html'

def replace_once(text, old, new, label):
    if old not in text:
        if new in text:
            print(label+'=ALREADY_PATCHED')
            return text
        raise SystemExit('PATCH_FAIL:'+label)
    return text.replace(old,new,1)

stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
for p in (SERVER,EDITOR):
    if not p.is_file():
        raise SystemExit('MISSING:'+str(p))
    shutil.copy2(p,p.with_name(p.name+'.bak_repeat_align_'+stamp))

s=SERVER.read_text(encoding='utf-8')
if 'import wave' not in s.split('\n',20)[:20]:
    s=s.replace('import requests\n','import requests\nimport wave\n',1)

route_start=s.find("@app.post('/api/jobs/<jid>/ai-align-block')")
route_end=s.find("\n\ndef _render_set(",route_start)
if route_start<0 or route_end<0:
    raise SystemExit('PATCH_FAIL:route_bounds')

new_route=r'''
def _ai_norm_repeat_token(text):
    txt=unicodedata.normalize('NFKD',str(text or '').lower())
    txt=''.join(ch for ch in txt if not unicodedata.combining(ch))
    return re.sub(r'[^a-z0-9]+','',txt)


def _ai_repeat_profile(clean):
    toks=[_ai_norm_repeat_token(x.get('text')) for x in clean]
    toks=[x for x in toks if x]
    n=len(toks)
    if n<6:
        return {'repetitive':False,'unique_ratio':1.0,'dominant_ratio':0.0,'adjacent_ratio':0.0}
    counts={}
    for t in toks: counts[t]=counts.get(t,0)+1
    unique_ratio=len(counts)/max(1,n)
    dominant_ratio=max(counts.values())/max(1,n)
    adjacent=sum(1 for i in range(1,n) if toks[i]==toks[i-1])/max(1,n-1)
    repetitive=(n>=8 and (unique_ratio<=0.45 or dominant_ratio>=0.34 or adjacent>=0.28))
    return {
        'repetitive':bool(repetitive),
        'unique_ratio':round(unique_ratio,4),
        'dominant_ratio':round(dominant_ratio,4),
        'adjacent_ratio':round(adjacent,4),
    }


def _ai_alignment_quality(aligned):
    n=len(aligned or [])
    if not n:
        return {'ok':False,'reasons':['empty'],'tiny':0,'huge':0,'collapsed':0,'median_duration':0.0}
    vals=[]; starts=[]; backward=0
    for aw in aligned:
        try:
            a=float(aw.get('start')); b=float(aw.get('end'))
        except Exception:
            return {'ok':False,'reasons':['invalid_time'],'tiny':0,'huge':0,'collapsed':0,'median_duration':0.0}
        if starts and a+0.005<starts[-1]: backward+=1
        starts.append(a); vals.append(max(0.0,b-a))
    pos=sorted(v for v in vals if v>0)
    med=pos[len(pos)//2] if pos else 0.0
    tiny=sum(1 for v in vals if v<=0.025)
    huge_limit=max(2.0,med*6.0)
    huge=sum(1 for v in vals if v>=huge_limit)
    collapsed=1; run=1
    for i in range(1,n):
        if abs(starts[i]-starts[i-1])<=0.015:
            run+=1; collapsed=max(collapsed,run)
        else:
            run=1
    reasons=[]
    if backward: reasons.append('backward')
    if tiny>=max(3,int(math.ceil(n*.10))): reasons.append('tiny_cluster')
    if huge>=max(2,int(math.ceil(n*.08))): reasons.append('stretched_words')
    if collapsed>=max(3,int(math.ceil(n*.10))): reasons.append('same_timestamp_cluster')
    return {
        'ok':not reasons,
        'reasons':reasons,
        'tiny':tiny,
        'huge':huge,
        'collapsed':collapsed,
        'median_duration':round(med,4),
        'huge_limit':round(huge_limit,4),
    }


def _ai_voice_active_bounds(wav_path):
    """Devuelve el tramo vocal útil dentro del clip, ignorando padding silencioso."""
    try:
        with wave.open(str(wav_path),'rb') as wf:
            sr=wf.getframerate(); nch=wf.getnchannels(); sw=wf.getsampwidth()
            raw=wf.readframes(wf.getnframes())
        if sw!=2:
            return (0.0,0.0)
        pcm=array('h'); pcm.frombytes(raw)
        if sys.byteorder!='little': pcm.byteswap()
        if nch>1:
            mono=array('h',(pcm[i] for i in range(0,len(pcm),nch)))
        else:
            mono=pcm
        if not mono or sr<=0: return (0.0,0.0)
        frame=max(1,int(sr*.020))
        db=[]
        for i in range(0,len(mono)-frame+1,frame):
            fr=mono[i:i+frame]
            rms=math.sqrt(sum(float(x)*float(x) for x in fr)/len(fr))/32768.0
            db.append(20.0*math.log10(max(rms,1e-7)))
        if not db: return (0.0,len(mono)/sr)
        sd=sorted(db)
        p35=sd[int((len(sd)-1)*.35)]
        p80=sd[int((len(sd)-1)*.80)]
        thr=max(-48.0,min(-25.0,p35+max(4.0,min(9.0,(p80-p35)*.22))))
        # Suavizado corto; 3 de 5 frames sobre umbral.
        active=[]
        for i in range(len(db)):
            lo=max(0,i-2); hi=min(len(db),i+3)
            votes=sum(1 for x in db[lo:hi] if x>=thr)
            if votes>=3: active.append(i)
        if not active: return (0.0,len(mono)/sr)
        first=active[0]*.020
        last=(active[-1]+1)*.020
        total=len(mono)/sr
        # El frontend suele añadir ~1 s de margen. Evitamos comernos una voz vecina.
        first=max(0.0,min(first,total))
        last=max(first+.10,min(last,total))
        return (round(first,4),round(last,4))
    except Exception as e:
        app.logger.warning('active bounds failed: %s',e)
        return (0.0,0.0)


def _ai_forced_align_worker(audio_path,text):
    with Path(audio_path).open('rb') as fh:
        rr=requests.post(
            'http://127.0.0.1:8097/api/elevenlabs/forced-align',
            files={'audio':(Path(audio_path).name,fh,'audio/wav')},
            data={'text':text},
            timeout=(30,1200)
        )
    if not rr.ok:
        try: detail=rr.json().get('detail') or rr.text[:800]
        except Exception: detail=rr.text[:800]
        raise ValueError('ElevenLabs no pudo alinear el bloque: '+str(detail))
    return rr.json()


def _ai_align_repeat_chunks(clip, clean, td, active_start, active_end, max_words=8):
    """Fallback para LA-LA / repeticiones: alinea subbloques cortos contra audio real."""
    n=len(clean)
    if n<2: raise ValueError('Bloque repetitivo demasiado corto.')
    if active_end<=active_start+.25:
        active_start=0.0
        # clip es WAV PCM creado por ffmpeg.
        try:
            with wave.open(str(clip),'rb') as wf: active_end=wf.getnframes()/wf.getframerate()
        except Exception: active_end=0.0
    if active_end<=active_start+.25:
        raise ValueError('No pude detectar el tramo vocal del bloque repetitivo.')

    results=[]; calls=0
    ranges=[]
    for lo in range(0,n,max_words):
        hi=min(n,lo+max_words)
        core_a=active_start+(active_end-active_start)*(lo/n)
        core_b=active_start+(active_end-active_start)*(hi/n)
        ranges.append((lo,hi,core_a,core_b))

    def run_piece(lo,hi,core_a,core_b,depth=0):
        nonlocal calls
        count=hi-lo
        span=max(.35,core_b-core_a)
        pad=min(.55,max(.20,span*.18))
        sub_a=max(0.0,core_a-pad)
        sub_b=core_b+pad
        part=Path(td)/f'repeat_{lo}_{hi}_{depth}.wav'
        proc=subprocess.run([
            'ffmpeg','-hide_banner','-loglevel','error','-ss',f'{sub_a:.6f}','-to',f'{sub_b:.6f}',
            '-i',str(clip),'-vn','-ac','1','-ar','44100','-c:a','pcm_s16le',str(part)
        ],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=180)
        if proc.returncode!=0 or not part.is_file() or part.stat().st_size<500:
            raise ValueError('No pude preparar un subbloque repetitivo.')
        payload=_ai_forced_align_worker(part,' '.join(clean[i]['text'] for i in range(lo,hi)))
        calls+=1
        aligned=payload.get('words') or []
        if len(aligned)!=count:
            if count>3 and depth<2:
                mid=lo+count//2
                mid_t=core_a+(core_b-core_a)*((mid-lo)/count)
                return run_piece(lo,mid,core_a,mid_t,depth+1)+run_piece(mid,hi,mid_t,core_b,depth+1)
            raise ValueError(f'IA devolvió {len(aligned)} palabras para {count} en un subbloque repetitivo.')
        quality=_ai_alignment_quality(aligned)
        if not quality['ok'] and count>4 and depth<2:
            mid=lo+count//2
            mid_t=core_a+(core_b-core_a)*((mid-lo)/count)
            return run_piece(lo,mid,core_a,mid_t,depth+1)+run_piece(mid,hi,mid_t,core_b,depth+1)

        piece=[]
        for off,(srcw,aw) in enumerate(zip(clean[lo:hi],aligned)):
            a=float(aw.get('start'))+sub_a
            b=float(aw.get('end'))+sub_a
            if b<=a: b=a+.03
            # Mantener cada resultado cerca de su subbloque sin cortar ataques naturales.
            a=max(active_start-.05,min(a,active_end+.05))
            b=max(a+.03,min(b,active_end+.08))
            piece.append({'idx':lo+off,'id':srcw['id'],'text':srcw['text'],'start':a,'end':b,'loss':aw.get('loss')})
        return piece

    for lo,hi,ca,cb in ranges:
        results.extend(run_piece(lo,hi,ca,cb,0))

    results.sort(key=lambda x:x['idx'])
    # Monotonía final: sólo corrige solapes claros entre subbloques.
    prev_end=None
    for item in results:
        if prev_end is not None and item['start']<prev_end-.08:
            item['start']=prev_end
            if item['end']<=item['start']: item['end']=item['start']+.03
        prev_end=max(item['start'],item['end'])
    quality=_ai_alignment_quality(results)
    return results,quality,calls,len(ranges)


@app.post('/api/jobs/<jid>/ai-align-block')
def ai_align_block(jid):
    if not TEST_MODE:
        return jsonify(ok=False,error='Alineación IA de bloque disponible sólo en el clon TEST.'),403
    d=request.get_json() or {}
    try:
        # R10: disponible para cualquier sesión válida del editor, incluida CORRECTORA.
        session(d.get('token'))
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

        profile=_ai_repeat_profile(clean)
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
            payload=_ai_forced_align_worker(clip,text_block)
            aligned=payload.get('words') or []
            if len(aligned)!=len(clean):
                raise ValueError(
                    f'ElevenLabs devolvió {len(aligned)} palabras para una selección de {len(clean)}. '
                    'No aplicaré timings ambiguos; ajusta el texto del bloque y vuelve a intentar.'
                )

            quality=_ai_alignment_quality(aligned)
            strategy='full'
            engine='elevenlabs-forced-alignment'
            calls=1; chunks=1
            if profile['repetitive'] and not quality['ok']:
                active_a,active_b=_ai_voice_active_bounds(clip)
                refined,refined_quality,extra_calls,chunks=_ai_align_repeat_chunks(
                    clip,clean,td,active_a,active_b,max_words=8
                )
                calls+=extra_calls
                if not refined_quality['ok']:
                    raise ValueError(
                        'La IA detectó que el bloque repetitivo sigue ambiguo después del segundo pase. '
                        'No aplicaré una sincronización colapsada; reduce la selección a 6–10 repeticiones y vuelve a intentar.'
                    )
                aligned=[{
                    'start':x['start'],'end':x['end'],'loss':x.get('loss')
                } for x in refined]
                quality=refined_quality
                strategy='repeat_chunks'
                engine='elevenlabs-forced-alignment+repeat-chunks'

            updates=[]
            if strategy=='repeat_chunks':
                # refined ya está en segundos relativos al clip.
                for srcw,aw in zip(clean,aligned):
                    a=float(aw.get('start'))+clip_start
                    b=float(aw.get('end'))+clip_start
                    if b<=a: b=a+.03
                    updates.append({'id':srcw['id'],'text':srcw['text'],'start':round(a,6),'end':round(b,6),'loss':aw.get('loss')})
            else:
                for srcw,aw in zip(clean,aligned):
                    a=float(aw.get('start'))+clip_start
                    b=float(aw.get('end'))+clip_start
                    if b<=a: b=a+.03
                    updates.append({'id':srcw['id'],'text':srcw['text'],'start':round(a,6),'end':round(b,6),'loss':aw.get('loss')})

            with db() as c:
                log(c,jid,'ELEVENLABS · ALINEAR BLOQUE',
                    f"{len(updates)} palabras · {clip_start:.2f}-{clip_end:.2f} · {strategy} · calls={calls}")
            return jsonify(
                ok=True,engine=engine,strategy=strategy,updates=updates,
                clip_start=clip_start,clip_end=clip_end,loss=payload.get('loss'),
                elapsed_s=payload.get('elapsed_s'),quality=quality,
                repetition=profile,calls=calls,chunks=chunks
            )
    except PermissionError as e: return jsonify(ok=False,error=str(e)),401
    except ValueError as e: return jsonify(ok=False,error=str(e)),400
    except Exception as e:
        app.logger.exception('ai-align-block %s',jid)
        return jsonify(ok=False,error='No se pudo alinear el bloque: '+str(e)),500
'''
s=s[:route_start]+new_route+s[route_end:]
SERVER.write_text(s,encoding='utf-8')

# Frontend: record the real strategy and tell the operator when the repeat fallback was used.
e=EDITOR.read_text(encoding='utf-8')
old="""    S.doc.ai.block_alignments.push({engine:"elevenlabs-forced-alignment",at:new Date().toISOString(),words:selected.length,clip_start:j.clip_start,clip_end:j.clip_end,loss:j.loss});"""
new="""    S.doc.ai.block_alignments.push({engine:j.engine||"elevenlabs-forced-alignment",strategy:j.strategy||"full",quality:j.quality||null,repetition:j.repetition||null,calls:j.calls||1,chunks:j.chunks||1,at:new Date().toISOString(),words:selected.length,clip_start:j.clip_start,clip_end:j.clip_end,loss:j.loss});"""
e=replace_once(e,old,new,'frontend_alignment_history')

old_toast='''    toast("✓ "+selected.length+" palabras sincronizadas con IA · el resto no se tocó",3200);'''
new_toast='''    const repeatMsg=j.strategy==="repeat_chunks"?" · repetición recalculada por subbloques de audio":"";
    toast("✓ "+selected.length+" palabras sincronizadas con IA"+repeatMsg+" · el resto no se tocó",4200);'''
e=replace_once(e,old_toast,new_toast,'frontend_repeat_toast')
EDITOR.write_text(e,encoding='utf-8')

print('PATCH_REPEAT_BLOCK_ALIGNMENT=OK')
