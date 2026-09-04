#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, sqlite3, sys
from pathlib import Path

PPS=300
W,H=300,216

class Decoder:
    def __init__(self):
        self.frame=bytearray(W*H); self.palette=[(0,0,0)]*16
        self.border=0; self.transparent=0; self.mem=0; self.hoff=0; self.voff=0
    def packet(self,p:bytes):
        if len(p)!=24 or (p[0]&63)!=9: return
        ins=p[1]&63; d=[p[4+i]&63 for i in range(16)]
        if ins==1:
            self.mem=d[0]&15; self.frame[:]=bytes([self.mem])*(W*H)
        elif ins==2: self.border=d[0]&15
        elif ins in (6,38):
            c0=d[0]&15; c1=d[1]&15; row=d[2]&31; col=d[3]&63
            y0=row*12; x0=col*6
            for rr in range(12):
                bits=d[4+rr]; y=y0+rr
                if y>=H: continue
                base=y*W
                for cc in range(6):
                    x=x0+cc
                    if x>=W: continue
                    v=c1 if bits&(1<<(5-cc)) else c0
                    i=base+x
                    self.frame[i]=(self.frame[i]^v) if ins==38 else v
        elif ins in (30,31):
            base=0 if ins==30 else 8
            pal=list(self.palette)
            for i in range(8):
                a,b=d[i*2],d[i*2+1]
                pal[base+i]=(((a&60)>>2)*17,((((a&3)<<2)|((b&48)>>4))*17),(b&15)*17)
            self.palette=pal
        elif ins==28: self.transparent=d[0]&15

def count_index(frame:bytearray,bbox:list[int],idx:int)->int:
    x0,y0,x1,y1=[max(0,int(x)) for x in bbox]
    x0=min(W,x0);x1=min(W,x1);y0=min(H,y0);y1=min(H,y1)
    if x1<=x0 or y1<=y0:return 0
    n=0
    for y in range(y0,y1):
        row=frame[y*W+x0:y*W+x1]
        n+=row.count(idx)
    return n

def flatten_project(project):
    out={}
    for seg in project.get("segments") or []:
        if not isinstance(seg,dict): continue
        for w in seg.get("words") or []:
            if isinstance(w,dict) and w.get("id"):
                out[str(w["id"])]=w
    return out

def choose_samples(compiled):
    n=len(compiled)
    if n<=8:return list(range(n))
    points=[0,1,2,n//4,n//2,(3*n)//4,n-2,n-1]
    seen=[];[seen.append(i) for i in points if i not in seen and 0<=i<n]
    return seen

def decode_samples(cdg:Path,compiled:list[dict],indices:list[int]):
    data=cdg.read_bytes(); total=len(data)//24
    wanted={}
    for i in indices:
        w=compiled[i]; st=int(w["cdg_start_frame"]); en=int(w["cdg_end_frame"])
        wanted[i]={"lo":max(0,st-30),"hi":min(total,en+90),"samples":[]}
    dec=Decoder()
    active=set()
    starts={v["lo"]:[] for v in wanted.values()}
    for i,v in wanted.items(): starts.setdefault(v["lo"],[]).append(i)
    ends={}
    for i,v in wanted.items(): ends.setdefault(v["hi"]+1,[]).append(i)
    for frame_no in range(total):
        for i in starts.get(frame_no,[]): active.add(i)
        p=data[frame_no*24:(frame_no+1)*24]; dec.packet(p)
        if active:
            for i in tuple(active):
                w=compiled[i]
                c=count_index(dec.frame,w["bbox"],int(w.get("active_fill_index",6)))
                wanted[i]["samples"].append((frame_no,c))
        for i in ends.get(frame_no+1,[]): active.discard(i)
    result={}
    for i,v in wanted.items():
        w=compiled[i]; st=int(w["cdg_start_frame"]); en=int(w["cdg_end_frame"]); ss=v["samples"]
        pre=[c for f,c in ss if f<st]
        baseline=max(pre) if pre else 0
        peak=max([c for _,c in ss] or [0])
        # Medimos paquetes reales, no un porcentaje de pixeles. El onset es el
        # primer incremento de color activo y el completion es el ULTIMO
        # incremento dentro de la ventana. Esto evita declarar una palabra
        # "terminada" antes de tiempo sólo porque ya alcanzó 98% de su tinta.
        changes=[]
        prev=None
        for f,c in ss:
            if prev is not None and c>prev:
                changes.append((f,c-prev))
            prev=c
        onset_candidates=[f for f,d in changes if f>=st-6]
        completion_candidates=[f for f,d in changes if f>=st-6 and f<=en+90]
        obs_start=onset_candidates[0] if onset_candidates else None
        obs_end=completion_candidates[-1] if completion_candidates else None
        result[i]={
            "baseline_active_pixels":baseline,"peak_active_pixels":peak,
            "active_pixel_growth":max(0,peak-baseline),
            "positive_packet_changes":len(changes),
            "decoded_start_frame":obs_start,"decoded_end_frame":obs_end,
            "decoded_start":round(obs_start/PPS,6) if obs_start is not None else None,
            "decoded_end":round(obs_end/PPS,6) if obs_end is not None else None,
        }
    return result

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",type=Path,required=True);ap.add_argument("--job",default="LET-0088")
    ap.add_argument("--out",type=Path,required=True);ap.add_argument("--engine-root",type=Path,required=True)
    a=ap.parse_args()
    sys.path.insert(0,str(a.engine_root))
    from engine_v2 import render_cdg
    con=sqlite3.connect(a.db);con.row_factory=sqlite3.Row
    row=con.execute("SELECT id,artist,title,project_json FROM jobs WHERE id=?",(a.job,)).fetchone()
    if not row: raise SystemExit("JOB_NOT_FOUND="+a.job)
    project=json.loads(row["project_json"] or "{}")
    if not project.get("segments"): raise SystemExit("PROJECT_JSON_EMPTY="+a.job)
    a.out.mkdir(parents=True,exist_ok=True)
    rr=render_cdg(project,a.out)
    timeline=rr["timeline"]
    diag=json.loads((a.out/"diagnostic_v2.json").read_text(encoding="utf-8"))
    compiled=diag["compiled_words"]
    source=flatten_project(project)
    canonical=timeline.get("words") or []
    exact=[]
    max_source_timeline=0.0
    for w in canonical:
        sw=source.get(str(w["id"]))
        if not sw: continue
        ds=abs(float(sw.get("start_time"))-float(w["start"]))
        de=abs(float(sw.get("end_time"))-float(w["end"]))
        max_source_timeline=max(max_source_timeline,ds,de)
        exact.append((w["id"],ds,de))
    max_timeline_compiled=0.0
    for c in compiled:
        max_timeline_compiled=max(max_timeline_compiled,abs(float(c["timeline_start"])-float(c["cdg_start"])),abs(float(c["timeline_end"])-float(c["cdg_end"])))
    inds=choose_samples(compiled); decoded=decode_samples(a.out/"output_v2.cdg",compiled,inds)
    rows=[]
    for i in inds:
        c=compiled[i]; d=decoded[i]; sw=source.get(str(c["word_id"])) or {}
        rows.append({
            "index":i,"word_id":c["word_id"],"text":c["text"],
            "source_start":sw.get("start_time"),"source_end":sw.get("end_time"),
            "timeline_start":c["timeline_start"],"timeline_end":c["timeline_end"],
            "compiled_cdg_start":c["cdg_start"],"compiled_cdg_end":c["cdg_end"],
            **d,
            "decoded_start_delta_ms":round((d["decoded_start"]-float(c["timeline_start"]))*1000,3) if d["decoded_start"] is not None else None,
            "decoded_end_delta_ms":round((d["decoded_end"]-float(c["timeline_end"]))*1000,3) if d["decoded_end"] is not None else None,
        })
    start_d=[abs(r["decoded_start_delta_ms"]) for r in rows if r["decoded_start_delta_ms"] is not None]
    end_d=[abs(r["decoded_end_delta_ms"]) for r in rows if r["decoded_end_delta_ms"] is not None]
    report={
        "job":a.job,"artist":row["artist"],"title":row["title"],"engine":timeline["engine"],
        "schema":timeline.get("schema"),"source_words":len(source),"canonical_words":len(canonical),
        "rendered_words":len(compiled),"rendered_lines":timeline["rendered_line_count"],
        "intro_delay_actual_frames":diag.get("intro_delay_actual_frames"),
        "sync_offset_frames":diag.get("sync_offset_frames"),
        "max_source_to_timeline_delta_ms":round(max_source_timeline*1000,3),
        "max_timeline_to_compiled_delta_ms":round(max_timeline_compiled*1000,3),
        "sample_max_abs_decoded_start_delta_ms":max(start_d) if start_d else None,
        "sample_max_abs_decoded_end_delta_ms":max(end_d) if end_d else None,
        "samples":rows,
        "warnings":timeline.get("warnings") or [],
    }
    (a.out/"equality_report_v2.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("JOB="+a.job+" "+str(row["artist"])+" - "+str(row["title"]))
    print("WORDS source/canonical/rendered="+f"{len(source)}/{len(canonical)}/{len(compiled)}")
    print("INTRO_DELAY_FRAMES="+str(report["intro_delay_actual_frames"]))
    print("SYNC_OFFSET_FRAMES="+str(report["sync_offset_frames"]))
    print("MAX_SOURCE_TIMELINE_MS="+str(report["max_source_to_timeline_delta_ms"]))
    print("MAX_TIMELINE_COMPILED_MS="+str(report["max_timeline_to_compiled_delta_ms"]))
    for r in rows:
        print("WORD",r["index"],repr(r["text"]),
              "SRC",r["source_start"],r["source_end"],
              "TL",r["timeline_start"],r["timeline_end"],
              "CDG_COMPILE",r["compiled_cdg_start"],r["compiled_cdg_end"],
              "DECODED",r["decoded_start"],r["decoded_end"],
              "DELTA_MS",r["decoded_start_delta_ms"],r["decoded_end_delta_ms"])
    print("REPORT="+str(a.out/"equality_report_v2.json"))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
