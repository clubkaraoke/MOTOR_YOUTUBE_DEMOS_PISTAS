#!/usr/bin/env python3
# deploy trigger: final render logic v1
from __future__ import annotations

import argparse
from pathlib import Path

MARK_EDITOR = "DJGABO_PHRASE_AWARE_LAYOUT_V1"
MARK_COMPOSER = "DJGABO_RENDER_QUEUE_FIX_V1"
MARK_BURST = "DJGABO_FAST_HIGHLIGHT_BURST_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: esperaba 1 coincidencia y encontré {n}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, new: str, label: str) -> str:
    i = text.find(start)
    if i < 0:
        raise RuntimeError(f"{label}: no encontré inicio")
    j = text.find(end, i)
    if j < 0:
        raise RuntimeError(f"{label}: no encontré fin")
    return text[:i] + new + text[j:]


def patch_editor(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    if MARK_EDITOR in s:
        print("EDITOR=ALREADY_PRESENT")
        return

    start = "function pvWrap(){"
    end = "\n\n/* El bloque de instrumental son LÍNEAS DE LETRA"
    new = r'''function pvWrap(){
  /* DJGABO_PHRASE_AWARE_LAYOUT_V1
     Regla visual:
     - linesPerPage es CAPACIDAD máxima, no obligación de llenar.
     - cada segmento de letra es una frase musical/semántica;
     - se envuelve por ancho, pero se intenta mantener la frase junta;
     - si una frase completa ya no cabe en la pantalla actual, empieza arriba
       en la siguiente pantalla;
     - sólo una frase que por sí sola excede N líneas se parte en varias pantallas;
     - un BREAK explícito o un INSTRUMENTAL fuerza frontera de pantalla;
     - las pantallas incompletas empiezan ARRIBA, nunca centradas verticalmente.
  */
  const lpp=Math.max(2,Math.min(8,Number(PV.cfg.linesPerPage||6)));
  const instStarts=new Set(
    _diagInstrumentalDecisions()
      .filter(x=>x.renderer_inserted&&x.next&&x.next.id)
      .map(x=>x.next.id)
  );

  const groups=[];
  let hardBefore=false;
  let phraseSerial=0;

  const wrapSlice=(words,phraseKey,forceBefore)=>{
    if(!words.length)return;
    const lines=[];
    let cur=[];
    const pushCur=()=>{
      if(!cur.length)return;
      cur._phraseKey=phraseKey;
      lines.push(cur);
      cur=[];
    };
    for(const w of words){
      const probe=cur.concat([w]);
      const txt=probe.map(x=>pvText(x.text)).join(" ");
      if(cur.length&&advWidth(txt)>PV.WRAP){
        pushCur();
        cur=[w];
      }else{
        cur=probe;
      }
    }
    pushCur();
    if(lines.length){
      groups.push({lines,hardBefore:!!forceBefore,phraseKey});
    }
  };

  for(const seg of S.doc.segments){
    if(seg.kind==="break"){
      hardBefore=true;
      continue;
    }
    const renderWords=(seg.words||[]).filter(w=>!w.spoken);
    if(!renderWords.length)continue;
    if(renderWords.some(w=>w.start_time===null)){
      hardBefore=true;
      continue;
    }

    let slice=[];
    let sliceHard=hardBefore;
    const flushSlice=()=>{
      if(!slice.length)return;
      const key=(seg.id||("seg"+phraseSerial))+":"+phraseSerial++;
      wrapSlice(slice,key,sliceHard);
      slice=[];
      sliceHard=false;
    };

    for(const w of renderWords){
      if(instStarts.has(w.id)){
        if(slice.length)flushSlice();
        sliceHard=true;
      }
      slice.push(w);
    }
    flushSlice();
    hardBefore=false;
  }

  const out=[];
  let page=[];

  const flushPage=()=>{
    if(!page.length)return;
    out.push(...page);
    while(out.length%lpp)out.push([]);
    page=[];
  };

  for(const g of groups){
    if(g.hardBefore)flushPage();
    const lines=g.lines||[];
    if(!lines.length)continue;

    if(lines.length>lpp){
      flushPage();
      for(let i=0;i<lines.length;i+=lpp){
        const chunk=lines.slice(i,i+lpp);
        out.push(...chunk);
        if(i+lpp<lines.length){
          while(out.length%lpp)out.push([]);
        }
      }
      continue;
    }

    if(page.length && page.length+lines.length>lpp){
      flushPage();
    }
    page.push(...lines);
  }
  flushPage();

  while(out.length&&!out[out.length-1].length)out.pop();
  return out;
}'''
    s = replace_between(s, start, end, new, "editor phrase-aware pvWrap")

    s = replace_once(
        s,
        '''      word_ids:ids,
      text:line.map(w=>pvText(w.text)).join(" "),
      sweep_start:+st.toFixed(3),''',
        '''      word_ids:ids,
      phrase_id:line._phraseKey||null,
      page_index:Math.floor(li/lpp)+1,
      text:line.map(w=>pvText(w.text)).join(" "),
      sweep_start:+st.toFixed(3),''',
        "editor render-plan phrase metadata",
    )

    s = replace_once(
        s,
        '''      renderer_may_preroll_packets_before_display_at:true
    },''',
        '''      renderer_may_preroll_packets_before_display_at:true,
      phrase_aware_layout:true,
      phrase_keep_together_when_possible:true,
      partial_pages_start_at_top:true
    },''',
        "editor render-plan policy",
    )

    s = s.replace("</body>", f"<!-- {MARK_EDITOR} -->\n</body>", 1)
    path.write_text(s, encoding="utf-8")
    print("EDITOR=PATCHED")


def patch_composer(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    if MARK_COMPOSER in s and MARK_BURST in s:
        print("COMPOSER=ALREADY_PRESENT")
        return

    s = replace_once(
        s,
        '''class LyricState:
    line_draw: int
    line_erase: int
    syllable_line: int
    syllable_index: int
    draw_queue: deque[CDGPacket]
    highlight_queue: deque[list[CDGPacket]]
''',
        '''class LyricState:
    line_draw: int
    line_erase: int
    syllable_line: int
    syllable_index: int
    # DJGABO_RENDER_QUEUE_FIX_V1
    # Los borrados tienen cola propia y prioridad sobre dibujos futuros.
    erase_queue: deque[CDGPacket]
    draw_queue: deque[CDGPacket]
    highlight_queue: deque[list[CDGPacket]]
    # Número de grupos de highlight que deben usar también el ancho de banda
    # reservado al dibujo para alcanzar el mínimo físico del CD+G.
    highlight_burst: int = 0
''',
        "composer LyricState queues",
    )

    s = replace_once(
        s,
        '''                        syllable_line=0,
                        syllable_index=0,
                        draw_queue=deque(),
                        highlight_queue=deque(),
''',
        '''                        syllable_line=0,
                        syllable_index=0,
                        erase_queue=deque(),
                        draw_queue=deque(),
                        highlight_queue=deque(),
                        highlight_burst=0,
''',
        "composer LyricState init",
    )

    s = replace_once(
        s,
        '''                or state.draw_queue
                or state.highlight_queue
''',
        '''                or state.erase_queue
                or state.draw_queue
                or state.highlight_queue
''',
        "composer main while erase queue",
    )

    s = replace_once(
        s,
        '''                for st in lyric_states:
                    st.highlight_queue.clear()
                    st.draw_queue.clear()
''',
        '''                for st in lyric_states:
                    st.highlight_queue.clear()
                    st.highlight_burst=0
                    st.erase_queue.clear()
                    st.draw_queue.clear()
''',
        "composer explicit clear queues",
    )

    s = replace_once(
        s,
        '''                state.draw_queue.extend(
                    line_image_to_packets(
                        line_erase_info.image,
                        xy=(line_erase_info.x, line_erase_info.y),
                        background=self.BACKGROUND,
                        erase=True,
                    )
                )
''',
        '''                # DJGABO_RENDER_QUEUE_FIX_V1
                # Un line_erase vencido no puede quedar detrás de decenas de
                # dibujos futuros. Se agenda en una cola prioritaria.
                state.erase_queue.extend(
                    line_image_to_packets(
                        line_erase_info.image,
                        xy=(line_erase_info.x, line_erase_info.y),
                        background=self.BACKGROUND,
                        erase=True,
                    )
                )
''',
        "composer erase queue target",
    )

    old_purge = '''            self.logger.debug("_compose_lyric: Purging all highlight/draw queues")
            for st in lyric_states:
                if instrumental.wait:
                    if st.highlight_queue:
                        self.logger.warning("_compose_lyric: Unexpected items in highlight queue when instrumental waited")
                    if st.draw_queue:
                        if st == state:
                            self.logger.debug("_compose_lyric: Queueing remaining draw packets for current state")
                        else:
                            self.logger.warning("_compose_lyric: Unexpected items in draw queue for non-current state")
                        self.writer.queue_packets(st.draw_queue)

                # Purge highlight/draw queues
                st.highlight_queue.clear()
                st.draw_queue.clear()
'''
    new_purge = '''            self.logger.debug("_compose_lyric: Purging all highlight/erase/draw queues")
            for st in lyric_states:
                if instrumental.wait:
                    if st.highlight_queue:
                        self.logger.warning("_compose_lyric: Unexpected items in highlight queue when instrumental waited")
                    if st.erase_queue:
                        # Respeta primero borrados ya vencidos antes de entrar
                        # al instrumental cuando éste está esperando.
                        self.writer.queue_packets(st.erase_queue)
                    if st.draw_queue:
                        if st == state:
                            self.logger.debug("_compose_lyric: Queueing remaining draw packets for current state")
                        else:
                            self.logger.warning("_compose_lyric: Unexpected items in draw queue for non-current state")
                        self.writer.queue_packets(st.draw_queue)

                # Purge highlight/erase/draw queues
                st.highlight_queue.clear()
                st.highlight_burst=0
                st.erase_queue.clear()
                st.draw_queue.clear()
'''
    s = replace_once(s, old_purge, new_purge, "composer instrumental purge queues")

    old_highlight_call = '''                state.highlight_queue.extend(
                    self._compose_highlight(
                        lyric=lyric,
                        syllable=syllable_info,
                        current_time=current_time,
                    )
                )
'''
    new_highlight_call = '''                highlight_groups, urgent = self._compose_highlight(
                    lyric=lyric,
                    syllable=syllable_info,
                    current_time=current_time,
                )
                state.highlight_queue.extend(highlight_groups)
                if urgent:
                    # DJGABO_FAST_HIGHLIGHT_BURST_V1
                    # Durante una palabra físicamente demasiado corta,
                    # el highlight toma temporalmente también el bandwidth de
                    # dibujo. No agrega paquetes: sólo elimina la espera
                    # artificial entre grupos para acercarse al mínimo físico.
                    state.highlight_burst=max(state.highlight_burst,len(state.highlight_queue))
'''
    s = replace_once(s, old_highlight_call, new_highlight_call, "composer urgent highlight caller")

    sched_start = '''        composer_state.just_cleared = False
        # Create groups of packets for highlights and draws, with None
'''
    sched_end = '''    def _compose_highlight(
'''
    new_sched = '''        composer_state.just_cleared = False
        # DJGABO_RENDER_QUEUE_FIX_V1 / DJGABO_FAST_HIGHLIGHT_BURST_V1
        # Prioridades de ancho de banda:
        #   1) highlight musical;
        #   2) borrado ya vencido;
        #   3) dibujo futuro (tiene read-ahead).
        # Si un highlight fue declarado "urgent", toma temporalmente el slot
        # de draw_bandwidth. El total de paquetes por ciclo NO aumenta.
        burst=bool(state.highlight_burst>0 and state.highlight_queue)
        highlight_slots=self.config.highlight_bandwidth + (self.config.draw_bandwidth if burst else 0)
        draw_slots=0 if burst else self.config.draw_bandwidth

        highlight_groups: list[list[CDGPacket | None]] = []
        popped_groups=0
        for _ in range(highlight_slots):
            group=[]
            if state.highlight_queue:
                group=state.highlight_queue.popleft()
                popped_groups+=1
            highlight_groups.append(list(pad(group,self.max_tile_height)))

        if burst:
            state.highlight_burst=max(0,state.highlight_burst-popped_groups)
        if not state.highlight_queue:
            state.highlight_burst=0

        draw_groups: list[list[CDGPacket | None]] = [
            [None] * self.max_tile_height for _ in range(draw_slots)
        ]

        self.lyric_packet_indices.update(
            range(
                self.writer.packets_queued,
                self.writer.packets_queued + len(list(it.chain(*highlight_groups,*draw_groups))),
            )
        )

        for group in intersperse(highlight_groups,draw_groups):
            for item in group:
                if item is not None:
                    self.writer.queue_packet(item)
                    continue

                # 1) Borrado vencido del estado actual.
                if state.erase_queue:
                    self.writer.queue_packet(state.erase_queue.popleft())
                    continue

                # 2) Borrado vencido de cualquier otro set de letra.
                other_erase=next((st for st in lyric_states if st.erase_queue),None)
                if other_erase is not None:
                    self.writer.queue_packet(other_erase.erase_queue.popleft())
                    continue

                # 3) Dibujo futuro del estado actual.
                if state.draw_queue:
                    self.writer.queue_packet(state.draw_queue.popleft())
                    continue

                # 4) Dibujo futuro de cualquier otro set.
                other_draw=next((st for st in lyric_states if st.draw_queue),None)
                if other_draw is not None:
                    self.writer.queue_packet(other_draw.draw_queue.popleft())
                    continue

                self.writer.queue_packet(no_instruction())

'''
    s = replace_between(s, sched_start, sched_end, new_sched, "composer packet scheduler")

    s = replace_once(
        s,
        '''    def _compose_highlight(
        self,
        lyric: LyricInfo,
        syllable: SyllableInfo,
        current_time: int,
    ) -> list[list[CDGPacket]]:
''',
        '''    def _compose_highlight(
        self,
        lyric: LyricInfo,
        syllable: SyllableInfo,
        current_time: int,
    ) -> tuple[list[list[CDGPacket]], bool]:
''',
        "composer highlight return type",
    )

    s = replace_once(
        s,
        '''        highlight_progress = [tile_index * CDG_TILE_WIDTH for tile_index in range(left_tile + 1, right_tile + 1)]
        # If there aren't too many tile boundaries for the number of
        # column updates
        if columns - 1 >= len(highlight_progress):
''',
        '''        highlight_progress = [tile_index * CDG_TILE_WIDTH for tile_index in range(left_tile + 1, right_tile + 1)]
        # DJGABO_FAST_HIGHLIGHT_BURST_V1
        # "urgent" significa que ni siquiera caben cómodamente los límites
        # geométricos obligatorios de tiles dentro de la ventana musical.
        # No movemos START/END y no inventamos más pasos; el scheduler sólo
        # presta temporalmente draw_bandwidth al barrido.
        urgent = columns - 1 < len(highlight_progress)
        # If there aren't too many tile boundaries for the number of
        # column updates
        if not urgent:
''',
        "composer urgent highlight detection",
    )

    s = replace_once(
        s,
        '''        return [
            line_mask_to_packets(syllable.mask, (x, y), edges, highlight=highlight_xor) for edges in it.pairwise([left_edge] + highlight_progress + [right_edge])
        ]
''',
        '''        groups=[
            line_mask_to_packets(syllable.mask,(x,y),edges,highlight=highlight_xor)
            for edges in it.pairwise([left_edge]+highlight_progress+[right_edge])
        ]
        return groups,urgent
''',
        "composer highlight tuple return",
    )

    path.write_text(s, encoding="utf-8")
    print("COMPOSER=PATCHED")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--composer", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    editor = root / "editor_v1" / "index.html"
    composer = Path(args.composer)

    for p in (editor, composer):
        if not p.is_file():
            raise SystemExit("MISSING:" + str(p))

    patch_editor(editor)
    patch_composer(composer)

    print("PATCH=OK")
    print("MARK_EDITOR=" + MARK_EDITOR)
    print("MARK_COMPOSER=" + MARK_COMPOSER)
    print("MARK_BURST=" + MARK_BURST)


if __name__ == "__main__":
    main()
