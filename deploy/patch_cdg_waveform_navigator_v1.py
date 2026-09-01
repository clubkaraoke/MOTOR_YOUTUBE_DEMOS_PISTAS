#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import shutil

ROOT = Path("/opt/djgabo-cdg")
EDITOR = ROOT / "editor_v1" / "index.html"

MARKER = "DJGABO_WAVEFORM_NAVIGATOR_V1"
REQUIRED_ZOOM_MARKER = "DJGABO_WAVEFORM_ZOOM_V1"
EXPECTED_SHA256 = "3d367e07901c63394e549a0276ee4b6fceea1f97b8fa142b1546057916186d65"

OLD_LEFT_OPEN = '''<div id="workspace">
  <div id="leftPane">
    <div id="waveWrap">'''

NEW_LEFT_OPEN = '''<div id="workspace">
  <div id="leftPane">
    <div id="waveNavStage">
      <div id="waveWrap">'''

OLD_AFTER_WAVE = '''      <div id="zoomHud" title="Ctrl + rueda del mouse = zoom centrado en el cursor"></div>
    </div>

    <div id="waveZoomBar" aria-label="Zoom de onda">'''

NEW_AFTER_WAVE = '''      <div id="zoomHud" title="Ctrl + rueda del mouse = zoom centrado en el cursor"></div>
      </div>

      <aside id="waveNavigator" aria-label="Navegador vertical de la onda">
        <div id="waveNavTrack" title="Clic = saltar la ventana visible">
          <div id="waveNavThumb" title="Arrastrar = subir o bajar por el audio">
            <button id="waveNavTop" class="waveNavHandle top" type="button" aria-label="Estirar ventana desde arriba" title="Arrastra para agrandar o achicar la ventana"></button>
            <span class="waveNavGrip" aria-hidden="true"></span>
            <button id="waveNavBottom" class="waveNavHandle bottom" type="button" aria-label="Estirar ventana desde abajo" title="Arrastra para agrandar o achicar la ventana"></button>
          </div>
        </div>
      </aside>
    </div>

    <div id="waveZoomBar" aria-label="Zoom de onda">'''

OLD_PRESETS = '''      <div id="zoomQuick" class="waveZoomPresets" aria-label="Ventanas rápidas">
        <button class="hbtn zq" type="button" data-z="20">20s</button>
        <button class="hbtn zq" type="button" data-z="10">10s</button>
        <button class="hbtn zq" type="button" data-z="5">5s</button>
        <button class="hbtn zq" type="button" data-z="2">2s</button>
        <button class="hbtn zq" type="button" data-z="1">1s</button>
        <button class="hbtn zq" type="button" data-z="0.5">0.5s</button>
      </div>'''

NEW_PRESETS = '''      <div id="zoomQuick" class="waveZoomPresets" aria-label="Ventanas rápidas">
        <button class="hbtn zq" type="button" data-z="999999">FULL</button>
        <button class="hbtn zq" type="button" data-z="120">120s</button>
        <button class="hbtn zq" type="button" data-z="60">60s</button>
        <button class="hbtn zq" type="button" data-z="20">20s</button>
        <button class="hbtn zq" type="button" data-z="10">10s</button>
        <button class="hbtn zq" type="button" data-z="5">5s</button>
        <button class="hbtn zq" type="button" data-z="2">2s</button>
        <button class="hbtn zq" type="button" data-z="1">1s</button>
        <button class="hbtn zq" type="button" data-z="0.5">0.5s</button>
        <button class="hbtn zq" type="button" data-z="0.1">0.1s</button>
      </div>'''

NAV_CSS = r'''
/* =========================================================
   DJGABO_WAVEFORM_NAVIGATOR_V1
   Navegador vertical derecho: mover viewport + estirar zoom.
   ========================================================= */
#waveNavStage{
  flex:1 1 auto;min-height:0;min-width:0;
  display:flex;align-items:stretch;overflow:hidden;
  background:var(--bg-base);
}
#waveNavStage #waveWrap{flex:1 1 auto;min-width:0;min-height:0}
#waveNavigator{
  flex:0 0 34px;position:relative;min-height:0;
  border-left:1px solid var(--line);background:#11161c;
  user-select:none;touch-action:none;
}
#waveNavTrack{
  position:absolute;left:7px;right:7px;top:8px;bottom:8px;
  border:1px solid #37414e;border-radius:999px;
  background:#202630;cursor:pointer;touch-action:none;
}
#waveNavThumb{
  position:absolute;left:2px;right:2px;top:0;min-height:38px;
  border:1px solid #9aa3af;border-radius:999px;
  background:linear-gradient(#7d8795,#68727f);
  box-shadow:0 0 0 1px rgba(0,0,0,.22);
  cursor:grab;touch-action:none;
}
#waveNavThumb.dragging{
  cursor:grabbing;background:linear-gradient(#929ba7,#727c89);
}
.waveNavGrip{
  position:absolute;left:4px;right:4px;top:50%;height:2px;
  transform:translateY(-50%);background:#c5cbd2;opacity:.78;
  box-shadow:0 -4px 0 #c5cbd2,0 4px 0 #c5cbd2;
  pointer-events:none;
}
.waveNavHandle{
  position:absolute;left:-5px;width:24px;height:16px;
  padding:0;border:1px solid #d5d9de;border-radius:7px;
  background:#b6bec8;cursor:ns-resize;touch-action:none;
}
.waveNavHandle.top{top:-7px}
.waveNavHandle.bottom{bottom:-7px}
.waveNavHandle:hover{background:#d0d5dc}
.waveNavHandle:active{background:#fff}
.waveZoomPresets{grid-template-columns:repeat(5,minmax(0,1fr))!important}
@media(max-width:420px){
  #waveNavigator{flex-basis:32px}
  #waveNavTrack{left:6px;right:6px}
}
'''

NAV_JS = r'''
<script id="djgabo-waveform-navigator-v1">
(() => {
  const nav = document.getElementById("waveNavigator");
  const track = document.getElementById("waveNavTrack");
  const thumb = document.getElementById("waveNavThumb");
  const topHandle = document.getElementById("waveNavTop");
  const bottomHandle = document.getElementById("waveNavBottom");
  if (!nav || !track || !thumb || !topHandle || !bottomHandle || typeof S === "undefined" || typeof draw !== "function") return;

  const MIN_DUR = 0.1;
  let drag = null;
  let lastSig = "";

  const maxDuration = () => Math.max(MIN_DUR, Number(S.duration || 0));
  const maxT0For = dur => Math.max(0, maxDuration() - dur);

  function normalizeView() {
    const total = maxDuration();
    const dur = clamp(Number(S.view.dur || total), MIN_DUR, total);
    const t0 = clamp(Number(S.view.t0 || 0), 0, Math.max(0, total - dur));
    return {total, dur, t0};
  }

  function sliderValueFromDuration(dur) {
    const maxDur = maxDuration();
    const d = clamp(Number(dur) || maxDur, MIN_DUR, maxDur);
    const den = Math.log(MIN_DUR / maxDur);
    if (!Number.isFinite(den) || den === 0) return 0;
    return clamp(Math.round(Math.log(d / maxDur) / den * 1000), 0, 1000);
  }

  function syncZoomBar(dur) {
    const out = document.getElementById("waveZoomValue");
    const range = document.getElementById("waveZoomRange");
    if (out) out.value = (dur < 10 ? dur.toFixed(dur < 1 ? 2 : 1) : dur.toFixed(0)) + " s";
    if (range) range.value = sliderValueFromDuration(dur);
    document.querySelectorAll("#zoomQuick .zq").forEach(btn => {
      const raw = Number(btn.dataset.z);
      const z = raw > maxDuration() ? maxDuration() : raw;
      btn.classList.toggle("on", Math.abs(z - dur) < 0.001);
    });
  }

  function renderNavigator(force = false) {
    const {total, dur, t0} = normalizeView();
    const r = track.getBoundingClientRect();
    const h = Math.max(1, r.height);
    const thumbH = Math.min(h, Math.max(38, h * (dur / total)));
    const travelPx = Math.max(0, h - thumbH);
    const travelTime = Math.max(0, total - dur);
    const y = travelTime > 0 && travelPx > 0 ? (t0 / travelTime) * travelPx : 0;
    const sig = [Math.round(h), total.toFixed(3), dur.toFixed(4), t0.toFixed(4), Math.round(y), Math.round(thumbH)].join("|");
    if (force || sig !== lastSig) {
      lastSig = sig;
      thumb.style.top = y + "px";
      thumb.style.height = thumbH + "px";
      thumb.setAttribute("aria-valuetext", "Vista " + dur.toFixed(dur < 1 ? 2 : 1) + " s");
      syncZoomBar(dur);
    }
  }

  function setViewport(t0, dur) {
    const total = maxDuration();
    const d = clamp(Number(dur), MIN_DUR, total);
    const start = clamp(Number(t0), 0, Math.max(0, total - d));
    S.view.dur = d;
    S.view.t0 = start;
    draw();
    renderNavigator(true);
  }

  function beginDrag(kind, e) {
    if (!S.peaks || maxDuration() <= MIN_DUR) return;
    e.preventDefault();
    e.stopPropagation();
    const v = normalizeView();
    const tr = track.getBoundingClientRect();
    const thumbRect = thumb.getBoundingClientRect();
    drag = {
      kind,
      pointerId: e.pointerId,
      y0: e.clientY,
      trackH: Math.max(1, tr.height),
      thumbH: Math.max(1, thumbRect.height),
      t0: v.t0,
      dur: v.dur,
      total: v.total,
      bottom: v.t0 + v.dur
    };
    thumb.classList.add("dragging");
    try { thumb.setPointerCapture(e.pointerId); } catch (_) {}
  }

  thumb.addEventListener("pointerdown", e => {
    if (e.target === topHandle || e.target === bottomHandle) return;
    beginDrag("move", e);
  });

  topHandle.addEventListener("pointerdown", e => beginDrag("top", e));
  bottomHandle.addEventListener("pointerdown", e => beginDrag("bottom", e));

  document.addEventListener("pointermove", e => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    e.preventDefault();
    const dy = e.clientY - drag.y0;

    if (drag.kind === "move") {
      const travelPx = Math.max(1, drag.trackH - drag.thumbH);
      const travelTime = Math.max(0, drag.total - drag.dur);
      const dt = (dy / travelPx) * travelTime;
      setViewport(drag.t0 + dt, drag.dur);
      return;
    }

    const dir = drag.kind === "top" ? -1 : 1;
    const factor = Math.exp((dy / drag.trackH) * 3.2 * dir);
    const nextDur = clamp(drag.dur * factor, MIN_DUR, drag.total);

    if (drag.kind === "top") {
      setViewport(drag.bottom - nextDur, nextDur);
    } else {
      setViewport(drag.t0, nextDur);
    }
  }, {passive:false});

  function finishDrag(e) {
    if (!drag || (e.pointerId !== undefined && e.pointerId !== drag.pointerId)) return;
    try {
      if (thumb.hasPointerCapture && thumb.hasPointerCapture(drag.pointerId)) thumb.releasePointerCapture(drag.pointerId);
    } catch (_) {}
    drag = null;
    thumb.classList.remove("dragging");
    renderNavigator(true);
  }
  document.addEventListener("pointerup", finishDrag, true);
  document.addEventListener("pointercancel", finishDrag, true);

  track.addEventListener("pointerdown", e => {
    if (e.target !== track || !S.peaks) return;
    e.preventDefault();
    const v = normalizeView();
    const r = track.getBoundingClientRect();
    const ratio = clamp((e.clientY - r.top) / Math.max(1, r.height), 0, 1);
    const center = ratio * v.total;
    setViewport(center - v.dur / 2, v.dur);
  });

  // Sincroniza la barra con scroll, zoom, reproducción y cualquier cambio del motor.
  function navLoop() {
    renderNavigator(false);
    requestAnimationFrame(navLoop);
  }
  new ResizeObserver(() => renderNavigator(true)).observe(nav);
  renderNavigator(true);
  requestAnimationFrame(navLoop);
})();
</script>
'''

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def backup_file() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = ROOT / "deploy-backups" / f"{stamp}-waveform-navigator-v1"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EDITOR, dest / "editor_v1-index.html")
    return dest

def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: se esperaba 1 coincidencia exacta y hay {count}. Se aborta.")
    return text.replace(old, new, 1)

def patch() -> tuple[bool, str]:
    if not EDITOR.is_file():
        raise RuntimeError(f"No existe el editor esperado: {EDITOR}")

    text = EDITOR.read_text(encoding="utf-8")
    current_sha = sha256_text(text)

    if MARKER in text:
        return False, "already-patched"

    if REQUIRED_ZOOM_MARKER not in text:
        raise RuntimeError("No está instalada la mejora de zoom V1 requerida. Se aborta.")

    if current_sha != EXPECTED_SHA256:
        raise RuntimeError(
            "El editor cambió desde la última verificación. "
            f"SHA esperado={EXPECTED_SHA256} actual={current_sha}. "
            "Se aborta sin modificar producción."
        )

    backup = backup_file()

    text = one(text, OLD_LEFT_OPEN, NEW_LEFT_OPEN, "abrir waveNavStage")
    text = one(text, OLD_AFTER_WAVE, NEW_AFTER_WAVE, "insertar navegador derecho")
    text = one(text, OLD_PRESETS, NEW_PRESETS, "ampliar presets")

    # Extender el rango de zoom aprobado: 0.1 s hasta canción completa.
    text = one(text, "const MIN_DUR = 0.4;", "const MIN_DUR = 0.1;", "mínimo zoom controles")
    text = one(
        text,
        "const newDur = clamp(S.view.dur * factor, 0.4, Math.max(4, S.duration || 60));",
        "const newDur = clamp(S.view.dur * factor, 0.1, Math.max(4, S.duration || 60));",
        "mínimo zoom Ctrl+rueda"
    )
    text = one(
        text,
        "S.view.dur = clamp(pinch.dur * pinch.d / Math.max(1,d), 0.4, S.duration||60);",
        "S.view.dur = clamp(pinch.dur * pinch.d / Math.max(1,d), 0.1, S.duration||60);",
        "mínimo zoom pellizco"
    )

    text = one(
        text,
        "display:grid!important;grid-template-columns:repeat(6,minmax(0,1fr));",
        "display:grid!important;grid-template-columns:repeat(5,minmax(0,1fr));",
        "grid presets"
    )

    if "</style>" not in text or "</body>" not in text:
        raise RuntimeError("No se encontraron puntos seguros de inserción.")

    text = text.replace("</style>", NAV_CSS + "\n</style>", 1)
    before, after = text.rsplit("</body>", 1)
    text = before + NAV_JS + "\n</body>" + after

    EDITOR.write_text(text, encoding="utf-8")
    verify()
    return True, str(backup)

def verify() -> None:
    text = EDITOR.read_text(encoding="utf-8")
    required = [
        MARKER,
        REQUIRED_ZOOM_MARKER,
        'id="waveNavStage"',
        'id="waveNavigator"',
        'id="waveNavTrack"',
        'id="waveNavThumb"',
        'id="waveNavTop"',
        'id="waveNavBottom"',
        'id="djgabo-waveform-navigator-v1"',
        'data-z="999999">FULL',
        'data-z="120"',
        'data-z="60"',
        'data-z="0.1"',
        "const MIN_DUR = 0.1;",
        "waveNavHandle",
        "renderNavigator",
    ]
    for token in required:
        if token not in text:
            raise RuntimeError(f"Falta verificación: {token}")

    if text.count('id="waveNavigator"') != 1:
        raise RuntimeError("Debe existir un único navegador vertical.")

if __name__ == "__main__":
    changed, backup = patch()
    print("CDG_WAVEFORM_NAVIGATOR_V1_OK", {"changed": changed, "backup": backup})
