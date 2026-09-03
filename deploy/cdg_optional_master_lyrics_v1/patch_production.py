#!/usr/bin/env python3
from pathlib import Path
import argparse

MARKER="DJGABO_OPTIONAL_MASTER_LYRICS_V1"

def replace_once(text,old,new,label):
    n=text.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontré {n}")
    return text.replace(old,new,1)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True)
    args=ap.parse_args()
    p=Path(args.root)/"server.py"
    text=p.read_text(encoding="utf-8")
    if MARKER in text:
        print("PATCH=ALREADY_PRESENT")
        return

    old='''def master_reserve(jid, artist, title, voice_name, lyrics, size_bytes=0, duration=0):
    """Reserva el LET-XXXX en el Sheet antes de confirmar el alta en OVH.

    Si Google no confirma la fila, el backend no crea un registro huérfano.
    """
    return drive_bridge_call('master_reserve',{
        'id':str(jid),'artist':str(artist),'title':str(title),
        'voiceName':Path(str(voice_name)).name,'lyrics':str(lyrics),
        'sizeBytes':int(size_bytes or 0),'duration':float(duration or 0)
    },timeout=120)
'''
    new='''def master_reserve(jid, artist, title, voice_name, lyrics, size_bytes=0, duration=0):
    """Reserva el LET-XXXX en el Sheet antes de confirmar el alta en OVH.

    La LETRA MAESTRA es opcional en el panel. El Web App histórico todavía
    valida el campo lyrics como dato no vacío al reservar una fila; por eso,
    cuando aún no hay letra, enviamos un marcador temporal SOLO al puente.
    El trabajo local conserva lyrics_moises="" y master_sync/IA reemplaza este
    marcador con el valor real cuando corresponda.
    """
    # DJGABO_OPTIONAL_MASTER_LYRICS_V1
    artist=str(artist or '').strip()
    title=str(title or '').strip()
    voice_name=Path(str(voice_name or '')).name.strip()
    if not artist or not title or not voice_name:
        raise ValueError('Faltan Artista, Título o archivo de Voz para registrar el trabajo maestro.')
    reserve_lyrics=str(lyrics or '').strip() or '[PENDIENTE IA]'
    return drive_bridge_call('master_reserve',{
        'id':str(jid),'artist':artist,'title':title,
        'voiceName':voice_name,'lyrics':reserve_lyrics,
        'sizeBytes':int(size_bytes or 0),'duration':float(duration or 0)
    },timeout=120)
'''
    text=replace_once(text,old,new,"master_reserve optional lyrics")
    p.write_text(text,encoding="utf-8")
    print("PATCH=OK")
    print("MARKER="+MARKER)

if __name__=="__main__":
    main()
