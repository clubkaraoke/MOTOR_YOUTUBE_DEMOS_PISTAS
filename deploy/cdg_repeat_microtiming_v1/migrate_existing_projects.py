#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re,sqlite3,unicodedata
from pathlib import Path

VERSION="REPEAT_MICROTIMING_V1"

def norm(text):
    txt=unicodedata.normalize("NFKD",str(text or "").lower())
    txt="".join(ch for ch in txt if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+","",txt)

def repair_project(project,min_run=3,tiny_seconds=.060):
    words=[
        w for seg in (project.get("segments") or [])
        for w in (seg.get("words") or [])
    ]
    repairs=[]
    i=0
    while i<len(words):
        t=norm(words[i].get("text"))
        if not t:
            i+=1;continue
        j=i+1
        while j<len(words) and norm(words[j].get("text"))==t:
            j+=1
        count=j-i
        if count<min_run:
            i=j;continue
        run=words[i:j]
        # No migramos bloques hablados, bloqueados o ya editados por otra IA/manual.
        if any(bool(w.get("spoken")) or bool(w.get("locked")) for w in run):
            i=j;continue
        if any(str(w.get("ai_match_type") or "") not in ("","scribe_raw","scribe_repeat_repaired") for w in run):
            i=j;continue
        parsed=[];valid=True
        for w in run:
            try:a=float(w.get("start_time"));b=float(w.get("end_time"))
            except Exception:valid=False;break
            if b<a:valid=False;break
            parsed.append((a,b))
        if not valid:
            i=j;continue
        durations=[max(0,b-a) for a,b in parsed]
        starts=[a for a,_ in parsed]
        tiny=any(d<=tiny_seconds for d in durations)
        collapsed=any((starts[k]-starts[k-1])<=.030 for k in range(1,count))
        if not (tiny or collapsed):
            i=j;continue

        first_start=starts[0];last_start=starts[-1];last_end=parsed[-1][1]
        onset_span=last_start-first_start;min_step=.090
        if onset_span>=min_step*(count-1):
            step=onset_span/(count-1)
            new_starts=[first_start+step*k for k in range(count)]
            method="repeat_locked_first_last_start"
        else:
            usable_end=last_end
            if j<len(words):
                try:
                    next_start=float(words[j].get("start_time"))
                    if next_start>first_start+.10:
                        usable_end=min(usable_end,next_start-.020) if usable_end>first_start else next_start-.020
                except Exception:pass
            span=usable_end-first_start
            if span<min_step*count:
                i=j;continue
            step=span/count
            new_starts=[first_start+step*k for k in range(count)]
            method="repeat_locked_outer_bounds"

        new_times=[]
        for k,w in enumerate(run):
            ns=new_starts[k]
            if k<count-1:
                cap=new_starts[k+1]-.010
                local_step=max(.001,new_starts[k+1]-ns)
                desired_min=min(.20,max(.10,local_step*.70))
                ne=min(cap,max(parsed[k][1],ns+desired_min))
            else:
                ne=last_end
                if ne<=ns+.060:
                    ext_cap=None
                    if j<len(words):
                        try:
                            ext=float(words[j].get("start_time"))
                            if ext>ns+.08:ext_cap=ext-.020
                        except Exception:pass
                    target=ns+min(.20,max(.10,step*.70))
                    ne=min(ext_cap,target) if ext_cap is not None else target
            if ne<=ns+.050:
                new_times=[];break
            new_times.append((ns,ne))
        if not new_times:
            i=j;continue

        original=[]
        for k,w in enumerate(run):
            oa,ob=parsed[k];ns,ne=new_times[k]
            original.append({"id":w.get("id"),"text":w.get("text"),"start":round(oa,6),"end":round(ob,6)})
            w["ai_original_start"]=round(oa,6)
            w["ai_original_end"]=round(ob,6)
            w["start_time"]=round(ns,6)
            w["end_time"]=round(ne,6)
            w["ai_timing_repaired"]=True
            w["ai_timing_repair"]="repeat_microtiming_v1"
            w["ai_timing_repair_token"]=t
            if str(w.get("ai_match_type") or "") in ("","scribe_raw"):
                w["ai_match_type"]="scribe_repeat_repaired"
        repairs.append({
            "version":VERSION,"token":t,"count":count,
            "start_index":i,"end_index":j-1,"method":method,
            "trigger":"tiny_or_collapsed","original":original,
            "repaired":[
                {"id":run[k].get("id"),"start":round(a,6),"end":round(b,6)}
                for k,(a,b) in enumerate(new_times)
            ],
        })
        i=j

    if repairs:
        ai=project.setdefault("ai",{})
        existing=ai.get("repeat_microtiming_repairs")
        if not isinstance(existing,list):existing=[]
        # Dedup por ids/timing para que el migrador sea idempotente.
        ai["repeat_microtiming_version"]=VERSION
        ai["repeat_microtiming_repairs"]=existing+repairs
    return repairs

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",required=True)
    ap.add_argument("--dry-run",action="store_true")
    a=ap.parse_args()
    db=Path(a.db)
    if not db.is_file():raise SystemExit("MISSING_DB:"+str(db))
    con=sqlite3.connect(str(db));con.row_factory=sqlite3.Row
    rows=con.execute("SELECT id,project_json FROM jobs WHERE COALESCE(project_json,'')<>'' ORDER BY id").fetchall()
    changed=[]
    for row in rows:
        try:project=json.loads(row["project_json"])
        except Exception:continue
        repairs=repair_project(project)
        if not repairs:continue
        changed.append((str(row["id"]),repairs))
        if not a.dry_run:
            con.execute("UPDATE jobs SET project_json=? WHERE id=?",(json.dumps(project,ensure_ascii=False),row["id"]))
    if not a.dry_run:con.commit()
    con.close()
    print("MODE="+("DRY_RUN" if a.dry_run else "APPLY"))
    print("JOBS_CHANGED="+str(len(changed)))
    print("RUNS_REPAIRED="+str(sum(len(x[1]) for x in changed)))
    for jid,reps in changed:
        print("JOB="+jid+" RUNS="+str(len(reps)))
        for r in reps:
            print("  TOKEN="+r["token"]+" COUNT="+str(r["count"])+" METHOD="+r["method"])
            print("  ORIGINAL="+json.dumps(r["original"],ensure_ascii=False,separators=(",",":")))
            print("  REPAIRED="+json.dumps(r["repaired"],ensure_ascii=False,separators=(",",":")))

if __name__=="__main__":
    main()
