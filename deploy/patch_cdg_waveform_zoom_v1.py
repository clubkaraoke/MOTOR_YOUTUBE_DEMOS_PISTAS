#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import shutil

ROOT = Path("/opt/djgabo-cdg")
EDITOR = ROOT / "editor_v1" / "index.html"

MARKER = "DJGABO_WAVEFORM_ZOOM_V1"
EXPECTED_SHA256 = "2e37adf75dc29e7219ca22ba1ae984edfaa3dc420d24dc0acfcfc94131b7abbf"

OLD_ZOOM_HTML = '''      <div id="zoomHud" title="Ctrl + rueda del mouse = zoom centrado en el cursor"></div><div id="zoomQuick" style="position:absolute;right:8px;top:8px;display:flex;gap:4px;z-index:5"><button class="hbtn zq" data-z="10">10s</button><button class="hbtn zq" data-z="5">5s</button><button class="hbtn zq" data-z="2">2s</button><button class="hbtn zq" data-z="1">1s</button></div>
    </div>
  </div>'''

NEW_ZOOM_HTML = '''      <div id="zoomHud" title="Ctrl + rueda del mouse = zoom centrado en el cursor"></div>
    </div>

    <div id="waveZoomBar" aria-label="Zoom de onda">
      <div class="waveZoomTop">
        <span>ZOOM / VENTANA</span>
        <output id="waveZoomValue">—</output>
      </div>
      <div class="waveZoomFine">
        <button id="waveZoomOut" class="waveZoomStep" type="button" title="Alejar onda (tecla −)" aria-label="Alejar onda">−</button>
        <input id="waveZoomRange" type="range" min="0" max="1000" value="500" aria-label="Zoom fino de onda">
        <button id="waveZoomIn" class="waveZoomStep" type="button" title="Acercar onda (tecla +)" aria-label="Acercar onda">+</button>
      </div>
      <div id="zoomQuick" class="waveZoomPresets" aria-label="Ventanas rápidas">
        <button class="hbtn zq" type="button" data-z="20">20s</button>
        <button class="hbtn zq" type="button" data-z="10">10s</button>
        <button class="hbtn zq" type="button" data-z="5">5s</button>
        <button class="hbtn zq" type="button" data-z="2">2s</button>
        <button class="hbtn zq" type="button" data-z="1">1s</button>
        <button class="hbtn zq" type="button" data-z="0.5">0.5s</button>
      </div>
    </div>
  </div>'''

WAVE_CSS = r'''
/* =========================================================
   DJGABO_WAVEFORM_ZOOM_V1
   Controles fuera del canvas + zoom progresivo anclado al playhead.
   ========================================================= */
#zoomHud{display:none!important}
#waveZoomBar{
  flex:0 0 auto;
  padding:8px 10px 9px;
  border-top:1px solid var(--line);
  background:var(--bg-surface);
}
.waveZoomTop{
  display:flex;align-items:center;justify-content:space-between;gap:10px;
  font:700 9.5px/1.2 var(--mono);letter-spacing:.08em;color:var(--dimmer);
}
#waveZoomValue{
  font:700 10.5px/1.2 var(--mono);letter-spacing:0;color:var(--text);
}
.waveZoomFine{
  display:grid;grid-template-columns:38px minmax(70px,1fr) 38px;
  align-items:center;gap:7px;margin-top:6px;
}
.waveZoomStep{
  height:34px;border:1px solid var(--line);border-radius:7px;
  background:var(--bg-base);color:var(--text);
  font:800 18px/1 var(--sans);cursor:pointer;
}
.waveZoomStep:hover{border-color:var(--dimmer);background:var(--bg-elevated)}
.waveZoomStep:active{border-color:var(--mark);background:var(--amber-dim)}
#waveZoomRange{width:100%;min-width:0;accent-color:var(--mark);cursor:pointer}
.waveZoomPresets{
  position:static!important;right:auto!important;top:auto!important;z-index:auto!important;
  display:grid!important;grid-template-columns:repeat(6,minmax(0,1fr));
  gap:4px!important;margin-top:6px;
}
.waveZoomPresets .hbtn{
  min-width:0!important;width:100%;height:30px;padding:4px 3px!important;
  border-radius:6px;font:700 10px/1 var(--mono);
}
.waveZoomPresets .hbtn.on{
  color:#fff;border-color:var(--mark);background:var(--amber-dim);
}
@media(max-width:420px){
  #waveZoomBar{padding:7px 8px 8px}
  .waveZoomFine{grid-template-columns:36px minmax(60px,1fr) 36px;gap:6px}
  .waveZoomPresets{gap:3px!important}
  .waveZoomPresets .hbtn{font-size:9px!important;padding:4px 1px!important}
}
'''

WAVE_JS = r'''
<script id="djgabo-waveform-zoom-v1">
(() => {
  const bar = document.getElementById("waveZoomBar");
  const range = document.getElementById("waveZoomRange");
  const out = document.getElementById("waveZoomValue");
  const zoomIn = document.getElementById("waveZoomIn");
  const zoomOut = document.getElementById("waveZoomOut");
  if (!bar || !range || !out || !zoomIn || !zoomOut || typeof cv === "undefined") return;

  const MIN_DUR = 0.4;
  const zoomMaxDur = () => Math.max(4, S.duration || 60);

  function currentAnchor() {
    const t = Number(S.audio.currentTime || 0);
    const dur = Math.max(0.0001, Number(S.view.dur || 1));
    let ratio = (t - S.view.t0) / dur;
    if (!Number.isFinite(ratio) || ratio < 0 || ratio > 1) ratio = 0.4;
    return {t, ratio};
  }

  function applyZoomDuration(nextDur, anchor = currentAnchor()) {
    if (!S.peaks) return;
    const maxDur = zoomMaxDur();
    const d = clamp(Number(nextDur) || S.view.dur, MIN_DUR, maxDur);
    S.view.dur = d;
    S.view.t0 = clamp(
      anchor.t - anchor.ratio * d,
      -1,
      Math.max(0, S.duration - d)
    );
    draw();
    syncWaveZoomUi();
  }

  function zoomFactor(factor) {
    applyZoomDuration(S.view.dur * factor);
  }

  function durationFromSlider(value) {
    const maxDur = zoomMaxDur();
    const x = clamp(Number(value) / 1000, 0, 1);
    return maxDur * Math.pow(MIN_DUR / maxDur, x);
  }

  function sliderFromDuration(dur) {
    const maxDur = zoomMaxDur();
    const d = clamp(Number(dur) || maxDur, MIN_DUR, maxDur);
    const den = Math.log(MIN_DUR / maxDur);
    if (!Number.isFinite(den) || den === 0) return 0;
    return clamp(Math.round(Math.log(d / maxDur) / den * 1000), 0, 1000);
  }

  function syncWaveZoomUi() {
    const d = Number(S.view.dur || 0);
    out.value = (d < 10 ? d.toFixed(d < 1 ? 2 : 1) : d.toFixed(0)) + " s";
    range.value = sliderFromDuration(d);
    document.querySelectorAll("#zoomQuick .zq").forEach(btn => {
      const z = Number(btn.dataset.z);
      btn.classList.toggle("on", Math.abs(z - d) < 0.001);
    });
  }

  zoomIn.onclick = () => zoomFactor(0.88);
  zoomOut.onclick = () => zoomFactor(1 / 0.88);

  range.addEventListener("input", () => {
    applyZoomDuration(durationFromSlider(range.value));
  });

  // Reemplaza los saltos rápidos originales: mantienen la raya blanca
  // exactamente en su altura visual cuando sea posible.
  document.querySelectorAll("#zoomQuick .zq").forEach(btn => {
    btn.onclick = () => applyZoomDuration(Number(btn.dataset.z));
  });

  // Teclado de laptop: + acerca, − aleja. No interfiere con inputs,
  // Ctrl+Z/Ctrl+S, roles 0–4 ni velocidades 1–6.
  document.addEventListener("keydown", e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName || "") || !!e.target?.isContentEditable;
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.code === "Equal" || e.code === "NumpadAdd") {
      e.preventDefault();
      zoomFactor(0.88);
    } else if (e.code === "Minus" || e.code === "NumpadSubtract") {
      e.preventDefault();
      zoomFactor(1 / 0.88);
    }
  }, true);

  // Ctrl + arrastrar verticalmente sobre la onda = zoom continuo.
  // Se captura antes del SEEK/timing drag del motor V1, sólo cuando Ctrl/Meta
  // está presionado al iniciar el gesto.
  let ctrlDrag = null;

  cv.addEventListener("pointerdown", e => {
    if (!S.peaks || !(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const r = cv.getBoundingClientRect();
    ctrlDrag = {
      id: e.pointerId,
      y0: e.clientY,
      h: Math.max(1, r.height),
      dur0: S.view.dur,
      anchor: currentAnchor()
    };
    try { cv.setPointerCapture(e.pointerId); } catch (_) {}
    cv.style.cursor = "ns-resize";
  }, true);

  cv.addEventListener("pointermove", e => {
    if (!ctrlDrag || e.pointerId !== ctrlDrag.id) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const dy = e.clientY - ctrlDrag.y0;
    const factor = Math.exp((dy / ctrlDrag.h) * 2.35);
    applyZoomDuration(ctrlDrag.dur0 * factor, ctrlDrag.anchor);
  }, true);

  function finishCtrlDrag(e) {
    if (!ctrlDrag || e.pointerId !== ctrlDrag.id) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    try { if (cv.hasPointerCapture(e.pointerId)) cv.releasePointerCapture(e.pointerId); } catch (_) {}
    ctrlDrag = null;
    cv.style.cursor = "crosshair";
    syncWaveZoomUi();
  }
  cv.addEventListener("pointerup", finishCtrlDrag, true);
  cv.addEventListener("pointercancel", finishCtrlDrag, true);

  // Touch real de dos dedos: conserva también la posición de la raya blanca.
  // En trackpads de Chrome el pinch suele llegar como Ctrl+wheel y seguirá
  // usando el comportamiento nativo existente.
  let touchPinch = null;
  cv.addEventListener("touchstart", e => {
    if (!S.peaks || e.touches.length !== 2) return;
    const [a,b] = e.touches;
    touchPinch = {
      dist: Math.max(1, Math.hypot(a.clientX-b.clientX, a.clientY-b.clientY)),
      dur: S.view.dur,
      anchor: currentAnchor()
    };
    e.preventDefault();
    e.stopImmediatePropagation();
  }, {capture:true, passive:false});

  cv.addEventListener("touchmove", e => {
    if (!touchPinch || e.touches.length !== 2) return;
    const [a,b] = e.touches;
    const dist = Math.max(1, Math.hypot(a.clientX-b.clientX, a.clientY-b.clientY));
    e.preventDefault();
    e.stopImmediatePropagation();
    applyZoomDuration(touchPinch.dur * touchPinch.dist / dist, touchPinch.anchor);
  }, {capture:true, passive:false});

  const finishTouchPinch = () => { touchPinch = null; syncWaveZoomUi(); };
  cv.addEventListener("touchend", finishTouchPinch, true);
  cv.addEventListener("touchcancel", finishTouchPinch, true);

  // El Ctrl+rueda original queda intacto. Sólo sincronizamos el control visual
  // después de que el motor V1 haya actualizado S.view.
  cv.addEventListener("wheel", () => requestAnimationFrame(syncWaveZoomUi), {passive:true});

  // También refleja cambios de zoom hechos por carga de proyecto o resize.
  const originalResizeObserverTarget = document.getElementById("waveWrap");
  if (originalResizeObserverTarget) {
    new ResizeObserver(() => requestAnimationFrame(syncWaveZoomUi)).observe(originalResizeObserverTarget);
  }

  syncWaveZoomUi();
})();
</script>
'''

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def backup_file() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = ROOT / "deploy-backups" / f"{stamp}-waveform-zoom-v1"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EDITOR, dest / "editor_v1-index.html")
    return dest

def patch() -> tuple[bool, str]:
    if not EDITOR.is_file():
        raise RuntimeError(f"No existe el editor esperado: {EDITOR}")

    text = EDITOR.read_text(encoding="utf-8")
    current_sha = sha256_text(text)

    if MARKER in text:
        return False, "already-patched"

    if current_sha != EXPECTED_SHA256:
        raise RuntimeError(
            "El editor cambió desde la inspección. "
            f"SHA esperado={EXPECTED_SHA256} actual={current_sha}. "
            "Se aborta sin modificar producción."
        )

    if OLD_ZOOM_HTML not in text:
        raise RuntimeError("No se encontró el bloque exacto de zoom inspeccionado. Se aborta.")

    if "</style>" not in text or "</body>" not in text:
        raise RuntimeError("No se encontraron puntos seguros de inserción.")

    backup = backup_file()

    text = text.replace(OLD_ZOOM_HTML, NEW_ZOOM_HTML, 1)
    text = text.replace("</style>", WAVE_CSS + "\n</style>", 1)
    text = text.rsplit("</body>", 1)[0] + WAVE_JS + "\n</body>" + text.rsplit("</body>", 1)[1]

    EDITOR.write_text(text, encoding="utf-8")
    verify()
    return True, str(backup)

def verify() -> None:
    text = EDITOR.read_text(encoding="utf-8")
    required = [
        MARKER,
        'id="waveZoomBar"',
        'id="waveZoomRange"',
        'id="waveZoomIn"',
        'id="waveZoomOut"',
        'data-z="20"',
        'data-z="0.5"',
        'id="djgabo-waveform-zoom-v1"',
        'e.code === "Equal"',
        'e.code === "Minus"',
        'Ctrl + arrastrar verticalmente',
        'requestAnimationFrame(syncWaveZoomUi)',
    ]
    for token in required:
        if token not in text:
            raise RuntimeError(f"Falta verificación: {token}")

    if 'id="zoomQuick" style="position:absolute' in text:
        raise RuntimeError("El bloque flotante antiguo de zoom sigue presente.")

if __name__ == "__main__":
    changed, backup = patch()
    print("CDG_WAVEFORM_ZOOM_V1_OK", {"changed": changed, "backup": backup})
