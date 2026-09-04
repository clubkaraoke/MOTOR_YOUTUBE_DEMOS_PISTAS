(function(){
"use strict";
const MQ=window.matchMedia("(max-width: 820px)");

function q(s,r){return (r||document).querySelector(s)}
function qa(s,r){return Array.from((r||document).querySelectorAll(s))}
function el(tag,cls,txt){const n=document.createElement(tag);if(cls)n.className=cls;if(txt!=null)n.textContent=txt;return n}
function isMobile(){return MQ.matches}

function injectCss(){
  if(q("#v2MobileResponsiveCss"))return;
  const st=el("style");st.id="v2MobileResponsiveCss";
  st.textContent=String.raw`
/* CDG V2 MOBILE RESPONSIVE — scoped by media query. Desktop remains unchanged. */
.v2m-only{display:none!important}
@media(max-width:820px){
  :root{--v2m-nav-h:64px;--v2m-transport-h:58px}
  html,body{max-width:100vw;overscroll-behavior:none}
  body{font-size:14px}
  .v2m-only{display:flex!important}

  /* ---------- PANEL /cdg-v2/ ---------- */
  body:not(.v2m-editor-page) .app{min-height:100dvh}
  body:not(.v2m-editor-page) .sidebar{display:none!important}
  body:not(.v2m-editor-page) .main{width:100%!important;min-width:0!important;margin:0!important;padding:0!important}
  body:not(.v2m-editor-page) .topbar{
    position:sticky!important;top:0!important;z-index:35!important;
    min-height:58px!important;height:auto!important;padding:9px 10px!important;gap:8px!important;
    background:rgba(18,20,26,.97)!important;backdrop-filter:blur(12px);
    border-bottom:1px solid var(--border,#2e3340)!important
  }
  body:not(.v2m-editor-page) .topbar .search{flex:1 1 auto!important;min-width:0!important}
  body:not(.v2m-editor-page) .topbar .search input{min-width:0!important;width:100%!important;font-size:14px!important}
  body:not(.v2m-editor-page) .topbar>.local-badge,
  body:not(.v2m-editor-page) .topbar>.session-badge,
  body:not(.v2m-editor-page) .topbar>#btnCerrarSesion{display:none!important}
  body:not(.v2m-editor-page) #btnNuevaCancion{
    flex:0 0 auto!important;min-height:42px!important;padding:0 12px!important;border-radius:9px!important;
    font-size:12px!important
  }
  body:not(.v2m-editor-page) .content:not(.v1-editor-mode){
    padding:16px 12px calc(84px + env(safe-area-inset-bottom))!important;
    overflow-y:auto!important;height:auto!important;min-height:calc(100dvh - 58px)!important
  }
  body:not(.v2m-editor-page).v1-editor-open .topbar{display:none!important}
  body:not(.v2m-editor-page).v1-editor-open .tabs-bottom{display:none!important}
  body:not(.v2m-editor-page).v1-editor-open .content.v1-editor-mode{
    position:fixed!important;inset:0!important;z-index:70!important;
    width:100vw!important;height:100dvh!important;min-height:100dvh!important;
    padding:0!important;margin:0!important;overflow:hidden!important;background:#12141A!important
  }
  body:not(.v2m-editor-page).v1-editor-open #view-editor,
  body:not(.v2m-editor-page).v1-editor-open #editorBody{
    display:block!important;width:100%!important;height:100%!important;min-height:0!important;
    padding:0!important;margin:0!important;overflow:hidden!important
  }
  body:not(.v2m-editor-page).v1-editor-open .v1-editor-frame{
    display:block!important;position:absolute!important;inset:0!important;
    width:100%!important;height:100dvh!important;min-height:100dvh!important;border:0!important
  }
  body:not(.v2m-editor-page) .tabs-bottom{
    display:flex!important;position:fixed!important;left:10px!important;right:10px!important;
    bottom:calc(8px + env(safe-area-inset-bottom))!important;z-index:80!important;
    min-height:58px!important;border:1px solid var(--border,#2e3340)!important;border-radius:16px!important;
    background:rgba(24,27,35,.97)!important;box-shadow:0 12px 35px rgba(0,0,0,.42)!important;
    overflow:hidden!important;padding:4px!important
  }
  body:not(.v2m-editor-page) .tabs-bottom .tb-item{min-height:48px!important;border-radius:11px!important;font-size:18px!important}
  body:not(.v2m-editor-page) .tabs-bottom .tb-item span{font-size:10px!important;margin-top:2px!important}
  body:not(.v2m-editor-page) .stat-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:9px!important}
  body:not(.v2m-editor-page) .stat-card{padding:13px!important;min-width:0!important}
  body:not(.v2m-editor-page) .quick{display:grid!important;grid-template-columns:1fr 1fr!important;gap:8px!important}
  body:not(.v2m-editor-page) .quick .btn{min-height:44px!important;justify-content:center!important}
  body:not(.v2m-editor-page) .productivity-strip{display:grid!important;grid-template-columns:repeat(3,1fr)!important;gap:6px!important}
  body:not(.v2m-editor-page) .mini-kpi{min-width:0!important;padding:10px 5px!important}
  body:not(.v2m-editor-page) .activity{overflow:hidden!important}
  body:not(.v2m-editor-page) .activity-row{
    display:grid!important;grid-template-columns:auto 1fr auto!important;gap:5px 8px!important;
    padding:10px 4px!important;align-items:center!important
  }
  body:not(.v2m-editor-page) .activity-row .when{grid-column:2/4!important;font-size:9px!important}
  body:not(.v2m-editor-page) .filters{
    display:flex!important;overflow-x:auto!important;gap:6px!important;padding:3px 0 8px!important;
    scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch
  }
  body:not(.v2m-editor-page) .filters>*{flex:0 0 auto!important;scroll-snap-align:start}
  body:not(.v2m-editor-page) .admin-list-tools{display:flex!important;flex-wrap:wrap!important;gap:7px!important}
  body:not(.v2m-editor-page) .v2m-advanced-details{
    border:1px solid var(--border,#2e3340);border-radius:10px;background:var(--bg-surface,#1b1e26);
    margin:8px 0 12px;overflow:hidden
  }
  body:not(.v2m-editor-page) .v2m-advanced-details>summary{
    cursor:pointer;list-style:none;padding:12px 13px;font-weight:700;color:var(--text-2,#9aa0ac)
  }
  body:not(.v2m-editor-page) .v2m-advanced-details>summary::-webkit-details-marker{display:none}
  body:not(.v2m-editor-page) .v2m-advanced-details>.admin-list-tools{padding:0 12px 12px}
  body:not(.v2m-editor-page) .modal-overlay{align-items:flex-end!important;padding:0!important}
  body:not(.v2m-editor-page) .modal-overlay .modal{
    width:100%!important;max-width:none!important;max-height:92dvh!important;overflow-y:auto!important;
    border-radius:18px 18px 0 0!important;padding:18px 16px calc(22px + env(safe-area-inset-bottom))!important;
    box-shadow:0 -18px 50px rgba(0,0,0,.45)!important
  }
  body:not(.v2m-editor-page) .modal-overlay .modal input,
  body:not(.v2m-editor-page) .modal-overlay .modal textarea,
  body:not(.v2m-editor-page) .modal-overlay .modal button{min-height:44px}
  .v2m-panel-more-btn{
    display:flex!important;align-items:center;justify-content:center;flex:0 0 42px;height:42px;border:1px solid var(--border,#2e3340);
    border-radius:9px;background:var(--bg-elevated,#232733);color:var(--text,#f1efea);font-size:20px
  }

  /* ---------- EDITOR iframe ---------- */
  body.v2m-editor-page{height:100dvh!important;overflow:hidden!important;padding-bottom:calc(var(--v2m-nav-h) + var(--v2m-transport-h) + env(safe-area-inset-bottom))!important}
  body.v2m-editor-page header{
    height:54px!important;flex:0 0 54px!important;padding:0 9px!important;gap:8px!important;
    position:relative!important;z-index:20!important
  }
  body.v2m-editor-page #songName{font-size:13px!important;max-width:46vw!important}
  body.v2m-editor-page #counter{font-size:9px!important}
  body.v2m-editor-page header>.hbtn:not(.go){display:none!important}
  body.v2m-editor-page header>.hbtn.go{padding:8px 10px!important;font-size:11px!important}
  body.v2m-editor-page #workspace{display:block!important;overflow:hidden!important;height:calc(100dvh - 54px - var(--v2m-nav-h) - var(--v2m-transport-h) - env(safe-area-inset-bottom))!important}
  body.v2m-editor-page #leftPane,
  body.v2m-editor-page #centerPane,
  body.v2m-editor-page #preview{
    display:none!important;width:100%!important;min-width:0!important;max-width:none!important;
    height:100%!important;flex:none!important;border:0!important
  }
  body.v2m-editor-page[data-v2m-pane="editor"] #workspace{
    display:grid!important;grid-template-rows:132px minmax(0,1fr)!important
  }
  body.v2m-editor-page[data-v2m-pane="editor"] #leftPane{
    display:flex!important;grid-row:1!important;height:132px!important;min-height:132px!important;
    border-bottom:1px solid var(--line)!important
  }
  body.v2m-editor-page[data-v2m-pane="editor"] #centerPane{
    display:flex!important;grid-row:2!important;height:auto!important;min-height:0!important
  }
  body.v2m-editor-page[data-v2m-pane="preview"] #preview,
  body.v2m-editor-page[data-v2m-pane="cdg"] #preview{display:flex!important}
  body.v2m-editor-page .split{display:none!important}
  body.v2m-editor-page #leftPane{padding:0!important}
  body.v2m-editor-page #waveWrap{min-height:0!important}
  body.v2m-editor-page #centerPane{overflow:hidden!important}
  body.v2m-editor-page #preview{padding:10px!important;overflow-y:auto!important;align-items:stretch!important}
  body.v2m-editor-page #phaseRailGlobal{
    display:flex!important;overflow-x:auto!important;gap:6px!important;padding:7px 8px!important;
    flex:0 0 auto!important;scrollbar-width:none!important
  }
  body.v2m-editor-page #phaseRailGlobal::-webkit-scrollbar{display:none}
  body.v2m-editor-page #phaseRailGlobal .phaseStep{flex:0 0 142px!important}
  body.v2m-editor-page #phaseRailGlobal .phaseArrow{display:none!important}
  body.v2m-editor-page #now{padding:11px 6px 24px!important;min-height:92px!important}
  body.v2m-editor-page #slots{gap:12px!important}
  body.v2m-editor-page .slot.cur{font-size:30px!important}
  body.v2m-editor-page .slot.prev,body.v2m-editor-page .slot.next{font-size:14px!important}
  body.v2m-editor-page #lyricsDrawer{overscroll-behavior:contain!important}
  body.v2m-editor-page #lyricsInner{padding:10px 10px 28px!important;font-size:13px!important;line-height:2.05!important}
  body.v2m-editor-page #vocalRoles{
    display:flex!important;overflow-x:auto!important;gap:5px!important;padding:8px!important;
    white-space:nowrap!important;scrollbar-width:none!important
  }
  body.v2m-editor-page #vocalRoles::-webkit-scrollbar{display:none}
  body.v2m-editor-page #vocalRoles .hbtn{display:inline-flex!important;flex:0 0 auto!important;min-height:38px!important;align-items:center!important}
  body.v2m-editor-page #transport{
    position:fixed!important;left:0!important;right:0!important;
    bottom:calc(var(--v2m-nav-h) + env(safe-area-inset-bottom))!important;
    z-index:74!important;height:var(--v2m-transport-h)!important;min-height:var(--v2m-transport-h)!important;
    padding:6px 8px!important;gap:5px!important;justify-content:flex-start!important;
    overflow-x:auto!important;background:rgba(27,30,38,.98)!important;backdrop-filter:blur(10px);
    scrollbar-width:none!important
  }
  body.v2m-editor-page #transport::-webkit-scrollbar{display:none}
  body.v2m-editor-page #transport .tbtn{min-width:62px!important;height:44px!important;padding:5px 7px!important;flex:0 0 auto!important}
  body.v2m-editor-page #transport .tbtn.play{min-width:86px!important}
  body.v2m-editor-page #transport .tbtn .lab{font-size:11px!important}
  body.v2m-editor-page #transport .tbtn .key{display:none!important}
  body.v2m-editor-page #transport .rates{flex:0 0 auto!important}
  body.v2m-editor-page #transport .rate{padding:8px!important}
  body.v2m-editor-page #transport #clock{min-width:122px!important;font-size:11px!important}
  body.v2m-editor-page .tsep{display:none!important}
  body.v2m-editor-page #settings{padding:0!important;align-items:flex-end!important}
  body.v2m-editor-page #settings .card{
    width:100%!important;height:94dvh!important;max-height:none!important;border-radius:18px 18px 0 0!important
  }
  body.v2m-editor-page .settingsHead{padding:14px!important}
  body.v2m-editor-page .settingsBody{padding:12px!important}
  body.v2m-editor-page .settingsFoot{padding:10px 12px calc(10px + env(safe-area-inset-bottom))!important}
  body.v2m-editor-page .setrow{align-items:flex-start!important;gap:8px!important}
  body.v2m-editor-page .setrow select,body.v2m-editor-page .setrow input[type=number]{min-height:42px!important}
  body.v2m-editor-page #v2box{width:100%!important;max-width:none!important;min-height:0!important;margin:0!important}
  body.v2m-editor-page #v2box .v2tabs{overflow-x:auto!important;scrollbar-width:none!important}
  body.v2m-editor-page #v2box .v2tabs::-webkit-scrollbar{display:none}
  body.v2m-editor-page #v2box .v2tab{flex:0 0 auto!important;min-width:92px!important;min-height:40px!important}
  body.v2m-editor-page #v2box .v2body{padding:0 4px 12px!important;gap:10px!important}
  body.v2m-editor-page #v2box .v2options{grid-template-columns:1fr!important;gap:8px!important}
  body.v2m-editor-page #v2box .v2checks{grid-template-columns:1fr!important;gap:8px!important}
  body.v2m-editor-page #v2box .v2check{min-height:34px!important}
  body.v2m-editor-page #v2box .v2screen{width:min(100%,520px)!important;margin:0 auto!important}
  body.v2m-editor-page #v2box .v2actions{position:sticky!important;bottom:0!important;background:#10141b!important;padding:7px 0!important;z-index:4!important}

  #v2mHeaderMore{
    display:flex!important;align-items:center!important;justify-content:center!important;
    width:40px!important;height:40px!important;flex:0 0 40px!important;
    border:1px solid #343b49!important;border-radius:10px!important;
    background:#202530!important;color:#f1efea!important;font-size:20px!important
  }

  /* Mobile editor bottom navigation */
  #v2mEditorNav{
    position:fixed;left:8px;right:8px;bottom:calc(5px + env(safe-area-inset-bottom));z-index:90;
    height:54px;padding:4px;display:grid!important;grid-template-columns:repeat(4,1fr);gap:3px;
    background:rgba(21,24,32,.98);border:1px solid #343b49;border-radius:15px;box-shadow:0 14px 35px rgba(0,0,0,.48)
  }
  #v2mEditorNav button{
    display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;
    min-width:0;border:0;border-radius:10px;color:#9aa0ac;background:transparent;font:700 10px Arial
  }
  #v2mEditorNav button .ico{font-size:17px;line-height:1}
  #v2mEditorNav button.on{background:rgba(139,92,246,.18);color:#d7c8ff}
  #v2mEditorNav button:active{transform:scale(.98)}

  /* Secondary-actions drawer */
  .v2m-drawer-backdrop{
    display:none;position:fixed;inset:0;z-index:98;background:rgba(0,0,0,.56);backdrop-filter:blur(2px)
  }
  .v2m-drawer-backdrop.open{display:block}
  .v2m-drawer{
    position:absolute;left:0;right:0;bottom:0;max-height:78dvh;overflow-y:auto;
    border-radius:20px 20px 0 0;background:#171b24;border:1px solid #343b49;
    padding:10px 12px calc(18px + env(safe-area-inset-bottom));box-shadow:0 -20px 55px rgba(0,0,0,.55)
  }
  .v2m-drawer-head{display:flex;align-items:center;justify-content:space-between;padding:5px 2px 12px;font-weight:800}
  .v2m-drawer-close{width:40px;height:40px;border-radius:10px!important;border:1px solid #343b49!important;font-size:20px!important}
  .v2m-drawer-list{display:grid;gap:7px}
  .v2m-drawer-action{
    display:flex!important;align-items:center!important;justify-content:space-between!important;width:100%!important;
    min-height:48px!important;padding:10px 12px!important;border:1px solid #303848!important;border-radius:10px!important;
    background:#202530!important;color:#f1efea!important;font:700 13px Arial!important;text-align:left!important
  }

  @media(max-width:390px){
    body:not(.v2m-editor-page) .stat-grid{grid-template-columns:1fr 1fr!important}
    body:not(.v2m-editor-page) .productivity-strip{grid-template-columns:1fr!important}
    body:not(.v2m-editor-page) .quick{grid-template-columns:1fr!important}
    body.v2m-editor-page #songName{max-width:40vw!important}
  }
}
`;
  document.head.appendChild(st);
}

function createDrawer(id,title){
  let back=q("#"+id);if(back)return back;
  back=el("div","v2m-drawer-backdrop");back.id=id;
  const dr=el("div","v2m-drawer");
  const hd=el("div","v2m-drawer-head");
  hd.append(el("strong","",title));
  const close=el("button","v2m-drawer-close","×");close.type="button";
  hd.append(close);dr.append(hd,el("div","v2m-drawer-list"));back.append(dr);document.body.append(back);
  function shut(){back.classList.remove("open")}
  close.onclick=shut;
  back.addEventListener("click",e=>{if(e.target===back)shut()});
  return back;
}
function proxyAction(list,src,label){
  if(!src||!list)return;
  const b=el("button","v2m-drawer-action");b.type="button";b.textContent=label||src.textContent.trim()||"Opción";
  b.onclick=()=>{src.click();const back=b.closest(".v2m-drawer-backdrop");if(back)back.classList.remove("open")};
  list.append(b);
}

function initEditor(){
  if(!q("#workspace")||!q("#leftPane")||!q("#centerPane")||!q("#preview"))return false;
  document.body.classList.add("v2m-editor-page");
  if(!document.body.dataset.v2mPane)document.body.dataset.v2mPane=sessionStorage.getItem("v2mPane")||"editor";
  if(q("#v2mEditorNav"))return true;

  const drawer=createDrawer("v2mEditorDrawer","Opciones del editor");
  const list=q(".v2m-drawer-list",drawer);
  const hiddenActions=qa("header .hbtn").filter(x=>!x.classList.contains("go"));
  hiddenActions.forEach(src=>proxyAction(list,src,src.textContent.trim()));
  const settings=q("#btnSettings");if(settings&&!hiddenActions.includes(settings))proxyAction(list,settings,"Configuración");
  const recalc=q("#v2refresh");if(recalc)proxyAction(list,recalc,"Recalcular Timeline V2");

  const head=q("header");
  if(head&&!q("#v2mHeaderMore")){
    const more=el("button","v2m-only","⋮");more.id="v2mHeaderMore";more.type="button";more.title="Más opciones";
    more.onclick=()=>drawer.classList.add("open");head.append(more);
  }

  const nav=el("nav","v2m-only");nav.id="v2mEditorNav";nav.setAttribute("aria-label","Navegación móvil del editor");
  const items=[
    ["home","⌂","Inicio"],
    ["editor","✎","Editor"],
    ["preview","▣","Preview"],
    ["cdg","◉","CDG V2"]
  ];
  const buttons={};
  items.forEach(([key,ico,label])=>{
    const b=el("button");b.type="button";b.dataset.pane=key;
    const i=el("span","ico",ico),t=el("span","",label);b.append(i,t);nav.append(b);buttons[key]=b;
  });
  document.body.append(nav);

  function goHome(){
    try{
      if(window.parent&&window.parent!==window&&typeof window.parent.mostrarVista==="function"){
        window.parent.mostrarVista("dashboard");return;
      }
    }catch(_){}
    try{window.parent.location.href="/cdg-v2/"}catch(_){location.href="/cdg-v2/"}
  }
  function select(key){
    if(key==="home"){goHome();return}
    document.body.dataset.v2mPane=key;sessionStorage.setItem("v2mPane",key);
    Object.entries(buttons).forEach(([k,b])=>b.classList.toggle("on",k===key));
    if(key==="cdg"){
      setTimeout(()=>{const t=q('.v2tab[data-v2="cdg"]');if(t)t.click()},40);
    }else if(key==="preview"){
      setTimeout(()=>{const t=q('.v2tab[data-v2="karaoke"]');if(t)t.click()},40);
    }
    window.dispatchEvent(new Event("resize"));
    setTimeout(()=>window.dispatchEvent(new Event("resize")),120);
  }
  Object.entries(buttons).forEach(([k,b])=>b.onclick=()=>select(k));
  const first=["editor","preview","cdg"].includes(document.body.dataset.v2mPane)?document.body.dataset.v2mPane:"editor";
  select(first);
  return true;
}

function collapsePanelAdvanced(){
  const tools=q(".admin-list-tools");
  if(!tools||tools.closest(".v2m-advanced-details"))return;
  const d=el("details","v2m-advanced-details"),s=el("summary","","Herramientas avanzadas");
  tools.parentNode.insertBefore(d,tools);d.append(s,tools);
}
function initPanel(){
  if(!q(".app")||!q(".sidebar")||!q(".main"))return false;
  if(q("#workspace"))return false;
  if(!q("#v2mPanelMore")){
    const top=q(".topbar");
    if(top){
      const b=el("button","v2m-panel-more-btn","⋮");b.id="v2mPanelMore";b.type="button";top.append(b);
      const drawer=createDrawer("v2mPanelDrawer","Cuenta y opciones");
      const list=q(".v2m-drawer-list",drawer);
      ["#btnCerrarSesion"].forEach(sel=>{const src=q(sel);if(src)proxyAction(list,src,src.textContent.trim())});
      qa(".sidebar .role-btn").forEach(src=>proxyAction(list,src,"Ver como "+src.textContent.trim()));
      b.onclick=()=>drawer.classList.add("open");
    }
  }
  collapsePanelAdvanced();
  return true;
}

let scheduled=false;
function apply(){
  injectCss();
  if(!isMobile())return;
  initEditor()||initPanel();
}
function schedule(){
  if(scheduled)return;scheduled=true;
  requestAnimationFrame(()=>{scheduled=false;apply()});
}

if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",apply,{once:true});else apply();
MQ.addEventListener?.("change",()=>{apply();window.dispatchEvent(new Event("resize"))});
const mo=new MutationObserver(schedule);mo.observe(document.documentElement,{childList:true,subtree:true});
})();