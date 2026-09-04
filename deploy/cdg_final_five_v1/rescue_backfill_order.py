#!/usr/bin/env python3
from pathlib import Path
import sys

p=Path(sys.argv[1])
t=p.read_text(encoding="utf-8")

early="""def _timings_local_path(jid): return JOBS/str(jid)/'proyecto.timings.json'

_backfill_master_fingerprints()

def backup_voice_to_drive"""
fixed="""def _timings_local_path(jid): return JOBS/str(jid)/'proyecto.timings.json'

def backup_voice_to_drive"""
if early in t:
    t=t.replace(early,fixed,1)

anchor="""    except Exception as e:
        app.logger.warning('backfill huellas JSON: %s',e)

def recover_interrupted_renders():"""
replacement="""    except Exception as e:
        app.logger.warning('backfill huellas JSON: %s',e)

# La función y _timings_local_path ya existen en este punto; DB ya fue migrada por init_db().
_backfill_master_fingerprints()

def recover_interrupted_renders():"""
placed="""_backfill_master_fingerprints()

def recover_interrupted_renders():"""
if placed not in t:
    if anchor not in t:
        raise RuntimeError("No encuentro ancla segura para backfill")
    t=t.replace(anchor,replacement,1)

p.write_text(t,encoding="utf-8")
print("RESCUE_BACKFILL_ORDER=OK")
