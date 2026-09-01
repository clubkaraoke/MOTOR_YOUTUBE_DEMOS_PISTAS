#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

ROOT = Path("/opt/djgabo-cdg")
EDITOR = ROOT / "editor_v1" / "index.html"
PANEL = ROOT / "panel.html"

EDITOR_MARKER = "DJGABO_MOBILE_EDITOR_V2"
PARENT_MARKER = "DJGABO_MOBILE_EDITOR_PARENT_V2"

EDITOR_CSS = r'''
/* =========================================================
   DJGABO_MOBILE_EDITOR_V2
   Móvil/tablet vertical: una vista legible por vez.
   Escritorio y tablet horizontal conservan los 3 paneles.
   ========================================================= */
@media (max-width:820px){
  html,body{width:100%;max-width:100%;overflow:hidden}
  body{font-size:14px}

  header{
    height:auto;min-height:0;flex:0 0 auto;
    display:grid;grid-template-columns:repeat(6,minmax(0,1fr));
    gap:6px;padding:7px 8px;background:var(--panel);
  }
  #songName{
    grid-column:1/-1;min-width:0;max-width:100%;
    font-size:12px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  }
  header .spacer{display:none}
  #counter{grid-column:1/4;align-self:center;font-size:10.5px}
  #status{
    grid-column:4/7;align-self:center;justify-self:end;
    max-width:100%;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  }
  #btnPreview{grid-column:1/3}
  #btnLyrics{grid-column:3/5}
  #btnSettings{grid-column:5/7}
  #btnSave{grid-column:1/4}
  #btnCdg{grid-column:4/7}
  header .hbtn{
    width:100%;min-width:0;min-height:38px;padding:7px 7px;
    font-size:11.5px;justify-content:center;text-align:center;border-radius:8px;
  }
  body.mobile-view-screen #btnPreview,
  body.mobile-view-lyrics #btnLyrics{
    color:#fff!important;border-color:var(--mark)!important;background:var(--amber-dim)!important;
    box-shadow:inset 0 0 0 1px rgba(139,92,246,.18);
  }

  #workspace{
    position:relative;display:block;width:100%;min-width:0;min-height:0;
    overflow:hidden;background:var(--ink);
  }
  #splitLeft,#splitRight{display:none!important}
  #leftPane,#centerPane,#preview{
    min-width:0!important;width:100%!important;max-width:none!important;flex:none!important;
  }

  /* LETRA: todo el ancho para corregir/sincronizar, sin columna cortada. */
  body.mobile-view-lyrics #workspace{overflow:hidden}
  body.mobile-view-lyrics #leftPane,
  body.mobile-view-lyrics #preview{display:none!important}
  body.mobile-view-lyrics #centerPane{
    display:flex!important;width:100%!important;height:100%!important;min-height:0!important;
  }
  body.mobile-view-lyrics #lyricsDrawer{
    display:block!important;min-width:0;width:100%;overflow:auto;
  }
  body.mobile-view-lyrics #lyricsInner{
    width:100%;max-width:none;padding:10px 12px;
    font-size:13px;line-height:1.72;overflow-wrap:anywhere;
  }
  body.mobile-view-lyrics .liveLyricsEditShell{max-width:none;width:100%}
  body.mobile-view-lyrics .liveLyricsEditor{width:100%;max-width:none;overflow-wrap:anywhere}
  body.mobile-view-lyrics .lrow{white-space:normal;overflow-wrap:anywhere}

  #editHint{
    padding:7px 9px!important;font-size:10px!important;line-height:1.35!important;
    white-space:normal!important;overflow-wrap:anywhere;
  }
  #editHint>span{min-width:0}
  #vocalRoles{
    width:100%;max-width:100%;overflow-x:auto;overflow-y:hidden;
    flex-wrap:nowrap!important;scrollbar-width:none;padding:6px 8px!important;
  }
  #vocalRoles::-webkit-scrollbar{display:none}
  #vocalRoles>.hbtn{flex:0 0 auto;min-height:34px;padding:6px 9px;font-size:10.5px}
  #vocalRoles>span{display:none!important}

  #phaseRailGlobal,.phaseRail{
    display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;
    gap:6px!important;margin:6px 8px 9px!important;
  }
  #phaseRailGlobal .phaseArrow,.phaseRail .phaseArrow{display:none!important}
  .phaseStep{padding:8px 7px!important;border-radius:8px}
  .phaseStep b{font-size:9px}
  .phaseStep span{font-size:10.5px;line-height:1.25}

  /* PANTALLA: onda y preview, ambos a ancho completo y apilados.
     Se desplaza verticalmente dentro del área de trabajo si hace falta. */
  body.mobile-view-screen #workspace{
    overflow-x:hidden;overflow-y:auto;-webkit-overflow-scrolling:touch;
  }
  body.mobile-view-screen #centerPane{display:none!important}
  body.mobile-view-screen #leftPane{
    display:flex!important;width:100%!important;height:max(52vh,350px)!important;min-height:350px!important;
    border-bottom:1px solid var(--line);
  }
  body.mobile-view-screen #preview{
    display:flex!important;width:100%!important;height:auto!important;min-height:600px!important;
    padding:10px 12px;border-top:0;overflow:visible!important;
  }
  body.mobile-view-screen #pvBox{min-height:220px}
  body.mobile-view-screen #pvDesigner{min-height:260px;overflow:visible}

  #zoomQuick{right:6px!important;top:6px!important;gap:3px!important}
  #zoomQuick .hbtn{min-width:42px;padding:6px 7px;font-size:10.5px}

  /* El transporte sigue disponible: scroll horizontal controlado, no corte. */
  #transport{
    max-width:100%;overflow-x:auto;overflow-y:hidden;gap:5px!important;
    padding-left:8px!important;padding-right:8px!important;scrollbar-width:none;
  }
  #transport::-webkit-scrollbar{display:none}
  #transport .tbtn{flex:0 0 auto;min-width:58px}
  #transport .rates{flex:0 0 auto}
  #clock{flex:0 0 auto;font-size:10px}

  /* Fase CARGAR / letra libre */
  .phase1Card{width:min(100%,100vw)!important;max-width:100%!important;border-radius:0!important}
  .phase1Body{min-width:0}
  .lyricsFree{
    width:100%;max-width:100%;min-height:46vh;height:calc(100dvh - 330px);
    font-size:16px;line-height:1.6;padding:14px;
  }
  .draftPlayer{
    grid-template-columns:auto 1fr auto!important;gap:6px!important;
  }
  .draftPlayer .draftRate{grid-column:1/-1;overflow-x:auto}
}

@media (max-width:420px){
  header{gap:5px;padding:6px}
  header .hbtn{font-size:11px;padding:6px 5px}
  #songName{font-size:11px}
  body.mobile-view-screen #leftPane{height:max(50vh,330px)!important;min-height:330px!important}
  body.mobile-view-screen #preview{min-height:560px!important}
}
'''

MOBILE_JS = r'''
<script id="djgabo-mobile-editor-v2">
(() => {
  const mq = window.matchMedia("(max-width: 820px)");
  const btnScreen = document.getElementById("btnPreview");
  const btnLyrics = document.getElementById("btnLyrics");
  const preview = document.getElementById("preview");
  const splitRight = document.getElementById("splitRight");
  const drawer = document.getElementById("lyricsDrawer");

  if (!btnScreen || !btnLyrics || !preview || !drawer) return;

  const desktopScreenHandler = btnScreen.onclick;
  const desktopLyricsHandler = btnLyrics.onclick;
  let mobileView = "lyrics";
  let desktopState = null;

  function redrawAll() {
    requestAnimationFrame(() => {
      try { if (typeof resize === "function") resize(); } catch (_) {}
      try { if (typeof draw === "function") draw(); } catch (_) {}
      try {
        if (typeof PV !== "undefined" && PV.on) {
          if (typeof pvResize === "function") pvResize();
          if (typeof pvDraw === "function") pvDraw();
        }
      } catch (_) {}
      try { if (typeof paintLyrics === "function") paintLyrics(); } catch (_) {}
    });
  }

  function setMobileView(next) {
    if (!mq.matches) return false;
    mobileView = next === "screen" ? "screen" : "lyrics";
    document.body.classList.toggle("mobile-view-screen", mobileView === "screen");
    document.body.classList.toggle("mobile-view-lyrics", mobileView === "lyrics");

    if (mobileView === "lyrics") {
      drawer.classList.remove("hidden");
    } else {
      try {
        if (typeof PV !== "undefined") PV.on = true;
      } catch (_) {}
      preview.hidden = false;
      if (splitRight) splitRight.hidden = true;
    }

    redrawAll();
    return true;
  }

  btnScreen.onclick = (event) => {
    if (setMobileView("screen")) {
      if (event) event.preventDefault();
      return;
    }
    if (typeof desktopScreenHandler === "function") {
      desktopScreenHandler.call(btnScreen, event);
    }
  };

  btnLyrics.onclick = (event) => {
    if (setMobileView("lyrics")) {
      if (event) event.preventDefault();
      return;
    }
    if (typeof desktopLyricsHandler === "function") {
      desktopLyricsHandler.call(btnLyrics, event);
    }
  };

  function applyBreakpoint() {
    if (mq.matches) {
      if (!desktopState) {
        desktopState = {
          previewHidden: preview.hidden,
          splitRightHidden: splitRight ? splitRight.hidden : false,
          lyricsHidden: drawer.classList.contains("hidden"),
          pvOn: (() => {
            try { return typeof PV !== "undefined" ? !!PV.on : null; }
            catch (_) { return null; }
          })()
        };
      }
      setMobileView(mobileView);
      return;
    }

    document.body.classList.remove("mobile-view-screen", "mobile-view-lyrics");
    if (desktopState) {
      preview.hidden = desktopState.previewHidden;
      if (splitRight) splitRight.hidden = desktopState.splitRightHidden;
      drawer.classList.toggle("hidden", desktopState.lyricsHidden);
      try {
        if (desktopState.pvOn !== null && typeof PV !== "undefined") {
          PV.on = desktopState.pvOn;
        }
      } catch (_) {}
      desktopState = null;
    }
    redrawAll();
  }

  if (typeof mq.addEventListener === "function") {
    mq.addEventListener("change", applyBreakpoint);
  } else if (typeof mq.addListener === "function") {
    mq.addListener(applyBreakpoint);
  }

  applyBreakpoint();
})();
</script>
'''

PARENT_CSS = r'''
/* =========================================================
   DJGABO_MOBILE_EDITOR_PARENT_V2
   Reserva el espacio del nav inferior mientras el iframe V1 está abierto.
   ========================================================= */
@media(max-width:880px){
  body.v1-editor-open{overflow:hidden!important}
  body.v1-editor-open .content.v1-editor-mode{
    padding:0!important;min-height:0!important;
    height:calc(100dvh - 68px)!important;
    max-height:calc(100dvh - 68px)!important;
    overflow:hidden!important;
  }
  body.v1-editor-open .v1-editor-frame{
    display:block;width:100%;height:100%!important;min-height:0!important;border:0;
  }
}
'''


def backup_files() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = ROOT / "deploy-backups" / f"{stamp}-mobile-editor-v2"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EDITOR, dest / "editor_v1-index.html")
    shutil.copy2(PANEL, dest / "panel.html")
    return dest


def patch_editor() -> bool:
    text = EDITOR.read_text(encoding="utf-8")
    if EDITOR_MARKER in text:
        return False
    if "</style>" not in text or "</body>" not in text:
        raise RuntimeError("No se encontraron los puntos seguros de inserción en editor_v1/index.html")
    text = text.replace("</style>", EDITOR_CSS + "\n</style>", 1)
    text = text.replace("</body>", MOBILE_JS + "\n</body>", 1)
    EDITOR.write_text(text, encoding="utf-8")
    return True


def patch_parent() -> bool:
    text = PANEL.read_text(encoding="utf-8")
    if PARENT_MARKER in text:
        return False
    if "</style>" not in text:
        raise RuntimeError("No se encontró </style> en panel.html")
    text = text.replace("</style>", PARENT_CSS + "\n</style>", 1)
    PANEL.write_text(text, encoding="utf-8")
    return True


def verify() -> None:
    editor = EDITOR.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")
    required_editor = [
        EDITOR_MARKER,
        'id="djgabo-mobile-editor-v2"',
        "mobile-view-screen",
        "mobile-view-lyrics",
        "@media (max-width:820px)",
    ]
    required_panel = [PARENT_MARKER, "height:calc(100dvh - 68px)"]
    for token in required_editor:
        if token not in editor:
            raise RuntimeError(f"Falta marcador de editor: {token}")
    for token in required_panel:
        if token not in panel:
            raise RuntimeError(f"Falta marcador de panel: {token}")


if __name__ == "__main__":
    if not EDITOR.is_file() or not PANEL.is_file():
        raise SystemExit("No se encontraron los archivos de producción esperados.")

    needs_change = (
        EDITOR_MARKER not in EDITOR.read_text(encoding="utf-8")
        or PARENT_MARKER not in PANEL.read_text(encoding="utf-8")
    )

    backup = None
    if needs_change:
        backup = backup_files()

    changed_editor = patch_editor()
    changed_parent = patch_parent()
    verify()

    print(
        "CDG_MOBILE_EDITOR_V2_OK",
        {
            "editor_changed": changed_editor,
            "parent_changed": changed_parent,
            "backup": str(backup) if backup else "already-patched",
        },
    )
