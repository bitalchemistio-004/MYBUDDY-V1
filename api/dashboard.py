"""Manual browser dashboard for MBUDDY hardware and companion feature testing."""
from __future__ import annotations

from aiohttp import web


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MBUDDY — Control Center</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;500;600;700&family=Exo+2:wght@300;400;600;800&display=swap" rel="stylesheet">
  <style>
    /* ── TOKENS ─────────────────────────────────────────────── */
    :root {
      --bg:        #050709;
      --bg2:       #080c0f;
      --panel:     rgba(10,15,20,0.88);
      --panel2:    rgba(14,20,27,0.96);
      --border:    rgba(0,210,180,0.12);
      --border2:   rgba(0,210,180,0.28);
      --glow:      #00d2b4;
      --glow2:     #00aaff;
      --glow3:     #ff4d6d;
      --amber:     #f5b843;
      --text:      #e8f0f8;
      --muted:     #6b8090;
      --muted2:    #3d5060;
      --mono:      'Share Tech Mono', monospace;
      --sans:      'Exo 2', sans-serif;
      --ui:        'Rajdhani', sans-serif;
      --r:         6px;
      color-scheme: dark;
    }
    * { box-sizing:border-box; margin:0; padding:0; }
    html { scroll-behavior:smooth; }
    body {
      min-height:100vh;
      background: var(--bg);
      color: var(--text);
      font-family: var(--ui);
      font-size: 14px;
      overflow-x: hidden;
    }

    /* ── BACKGROUND GRID ─────────────────────────────────────── */
    body::before {
      content:'';
      position:fixed; inset:0; z-index:0; pointer-events:none;
      background-image:
        linear-gradient(rgba(0,210,180,0.028) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0,210,180,0.028) 1px, transparent 1px);
      background-size: 40px 40px;
      mask-image: radial-gradient(ellipse 120% 80% at 50% 50%, black 40%, transparent 100%);
    }
    body::after {
      content:'';
      position:fixed; inset:0; z-index:0; pointer-events:none;
      background:
        radial-gradient(ellipse 60% 40% at 20% 0%,  rgba(0,210,180,0.07) 0%, transparent 60%),
        radial-gradient(ellipse 50% 35% at 80% 10%, rgba(0,170,255,0.06) 0%, transparent 55%),
        radial-gradient(ellipse 40% 30% at 50% 95%, rgba(0,210,180,0.04) 0%, transparent 50%);
    }

    /* ── SCAN LINE FLICKER ───────────────────────────────────── */
    @keyframes scanline {
      0%   { transform:translateY(-100%); }
      100% { transform:translateY(100vh); }
    }
    .scanline {
      position:fixed; top:0; left:0; right:0; height:2px; z-index:9999; pointer-events:none;
      background: linear-gradient(transparent, rgba(0,210,180,0.06), transparent);
      animation: scanline 6s linear infinite;
    }

    /* ── LAYOUT ──────────────────────────────────────────────── */
    #app { position:relative; z-index:1; display:flex; flex-direction:column; min-height:100vh; }

    /* ── TOPBAR ──────────────────────────────────────────────── */
    #topbar {
      position:sticky; top:0; z-index:100;
      display:flex; align-items:center; gap:16px; flex-wrap:wrap;
      padding:10px 20px;
      background: rgba(5,7,9,0.92);
      backdrop-filter:blur(18px);
      border-bottom:1px solid var(--border);
      box-shadow: 0 0 40px rgba(0,210,180,0.04);
    }
    .logo {
      display:flex; align-items:center; gap:10px;
    }
    .logo-badge {
      width:36px; height:36px;
      border:1px solid var(--glow);
      border-radius:4px;
      display:grid; place-items:center;
      font-family:var(--mono);
      font-size:11px;
      color:var(--glow);
      background: rgba(0,210,180,0.06);
      box-shadow: 0 0 16px rgba(0,210,180,0.18), inset 0 0 8px rgba(0,210,180,0.05);
      position:relative;
      overflow:hidden;
    }
    .logo-badge::before {
      content:'';
      position:absolute; inset:0;
      background:linear-gradient(135deg,rgba(0,210,180,0.15),transparent 50%);
    }
    .logo-text {
      display:flex; flex-direction:column; gap:1px;
    }
    .logo-name {
      font-family:var(--sans); font-size:17px; font-weight:800;
      letter-spacing:2px; color:var(--text); line-height:1;
      text-transform:uppercase;
    }
    .logo-name span { color:var(--glow); }
    .logo-sub {
      font-family:var(--mono); font-size:9px; color:var(--muted);
      letter-spacing:3px; text-transform:uppercase;
    }
    .topbar-spacer { flex:1; }
    .nav-links {
      display:flex; gap:4px; align-items:center;
    }
    .nav-links a {
      font-family:var(--mono); font-size:11px; letter-spacing:1.5px;
      color:var(--muted); text-decoration:none; text-transform:uppercase;
      padding:6px 10px; border-radius:4px;
      border:1px solid transparent;
      transition:all 160ms;
    }
    .nav-links a:hover { color:var(--glow); border-color:var(--border2); background:rgba(0,210,180,0.05); }
    .status-pill {
      display:flex; align-items:center; gap:7px;
      padding:6px 12px;
      border:1px solid var(--border);
      border-radius:20px;
      background:rgba(0,0,0,0.4);
      font-family:var(--mono); font-size:11px; color:var(--muted);
      white-space:nowrap;
    }
    .status-pill .dot {
      width:7px; height:7px; border-radius:50%;
      background:var(--glow3);
      transition:background 400ms, box-shadow 400ms;
    }
    .status-pill .dot.on {
      background:var(--glow);
      box-shadow:0 0 8px var(--glow), 0 0 16px rgba(0,210,180,0.4);
    }

    /* ── HERO ────────────────────────────────────────────────── */
    #hero {
      display:grid;
      grid-template-columns: 1fr minmax(0,520px) 1fr;
      grid-template-rows: auto;
      align-items:start;
      gap:0;
      padding:24px 20px 0;
    }
    #hero-left  { grid-column:1; padding-right:16px; display:flex; flex-direction:column; gap:12px; align-items:flex-end; }
    #hero-center{ grid-column:2; display:flex; flex-direction:column; align-items:center; gap:12px; }
    #hero-right { grid-column:3; padding-left:16px; display:flex; flex-direction:column; gap:12px; }

    /* OLED Display */
    .oled-frame {
      width:100%;
      max-width:520px;
      padding:16px;
      border:1px solid var(--border2);
      border-radius:10px;
      background: rgba(4,8,10,0.95);
      box-shadow:
        0 0 0 1px rgba(0,210,180,0.06),
        0 0 60px rgba(0,210,180,0.1),
        0 0 120px rgba(0,210,180,0.04),
        inset 0 0 30px rgba(0,0,0,0.6);
      position:relative;
    }
    .oled-frame::before {
      content:'OLED PREVIEW // 128×64 1-BIT';
      position:absolute; top:-10px; left:50%;
      transform:translateX(-50%);
      font-family:var(--mono); font-size:9px; letter-spacing:2px;
      color:var(--muted2); background:var(--bg); padding:0 10px;
      white-space:nowrap;
    }
    .oled-scanlines {
      position:absolute; inset:16px; pointer-events:none; z-index:2;
      background:repeating-linear-gradient(transparent,transparent 3px,rgba(0,0,0,0.18) 3px,rgba(0,0,0,0.18) 4px);
      border-radius:4px;
    }
    canvas#oled {
      display:block;
      width:100%; aspect-ratio:2/1;
      image-rendering:pixelated;
      border:1px solid rgba(0,210,180,0.2);
      border-radius:4px;
      background:#020405;
      box-shadow:0 0 20px rgba(0,210,180,0.15), inset 0 0 10px rgba(0,0,0,0.8);
      position:relative; z-index:1;
    }
    .oled-status-bar {
      display:flex; justify-content:space-between; align-items:center;
      margin-top:10px;
      padding-top:10px;
      border-top:1px solid var(--border);
    }
    .oled-label {
      font-family:var(--mono); font-size:10px; color:var(--muted);
      letter-spacing:1px;
    }
    .oled-label span { color:var(--glow); }
    .oled-corners {
      position:absolute; inset:0; pointer-events:none; z-index:3;
    }
    .oled-corners::before, .oled-corners::after,
    .oled-frame-inner::before, .oled-frame-inner::after {
      content:''; position:absolute; width:10px; height:10px;
      border-color:var(--glow); border-style:solid;
    }
    .oled-corners::before { top:8px; left:8px; border-width:1px 0 0 1px; }
    .oled-corners::after  { top:8px; right:8px; border-width:1px 1px 0 0; }
    .oled-frame-inner { position:absolute; inset:0; pointer-events:none; z-index:3; }
    .oled-frame-inner::before { bottom:8px; left:8px; border-width:0 0 1px 1px; }
    .oled-frame-inner::after  { bottom:8px; right:8px; border-width:0 1px 1px 0; }

    /* Hero metrics strip */
    .metric-strip {
      width:100%;
      display:grid; grid-template-columns: repeat(3,1fr); gap:8px;
    }
    .metric-cell {
      padding:9px 10px;
      border:1px solid var(--border);
      border-radius:var(--r);
      background:var(--panel);
      display:flex; flex-direction:column; gap:3px;
    }
    .metric-cell:hover { border-color:var(--border2); }
    .metric-cell .mc-label {
      font-family:var(--mono); font-size:9px; letter-spacing:2px;
      color:var(--muted); text-transform:uppercase;
    }
    .metric-cell .mc-val {
      font-family:var(--mono); font-size:13px; color:var(--text);
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    }
    .mc-val.good { color:var(--glow); }
    .mc-val.warn { color:var(--amber); }
    .mc-val.bad  { color:var(--glow3); }

    /* Side panels in hero */
    .side-panel {
      width:100%;
      padding:14px;
      border:1px solid var(--border);
      border-radius:var(--r);
      background:var(--panel);
      backdrop-filter:blur(10px);
    }
    .side-panel:hover { border-color:var(--border2); }
    .panel-title {
      font-family:var(--mono); font-size:9px; letter-spacing:3px;
      text-transform:uppercase; color:var(--muted);
      margin-bottom:10px;
      display:flex; align-items:center; gap:8px;
    }
    .panel-title::after {
      content:''; flex:1; height:1px; background:var(--border);
    }

    /* ── MAIN CONTENT GRID ───────────────────────────────────── */
    #main {
      padding:20px;
      display:grid;
      grid-template-columns: 300px 1fr 300px;
      gap:16px;
      align-items:start;
    }
    #col-left  { display:flex; flex-direction:column; gap:12px; }
    #col-mid   { display:flex; flex-direction:column; gap:12px; }
    #col-right { display:flex; flex-direction:column; gap:12px; }

    /* ── PANEL BASE ──────────────────────────────────────────── */
    .panel {
      border:1px solid var(--border);
      border-radius:var(--r);
      background:var(--panel);
      backdrop-filter:blur(12px);
      overflow:hidden;
      transition:border-color 200ms;
    }
    .panel:hover { border-color:var(--border2); }
    .panel-head {
      display:flex; align-items:center; justify-content:space-between;
      padding:10px 14px;
      border-bottom:1px solid var(--border);
      background:rgba(0,0,0,0.3);
    }
    .panel-head h2 {
      font-family:var(--mono); font-size:9px; letter-spacing:3px;
      text-transform:uppercase; color:var(--muted);
      display:flex; align-items:center; gap:6px;
    }
    .panel-head h2 .ph-accent {
      width:4px; height:4px; border-radius:50%;
      background:var(--glow);
      box-shadow:0 0 6px var(--glow);
    }
    .panel-body { padding:12px 14px; }

    /* ── INPUTS ──────────────────────────────────────────────── */
    label.field {
      display:flex; flex-direction:column; gap:5px;
    }
    .field-label {
      font-family:var(--mono); font-size:9px; letter-spacing:2px;
      color:var(--muted); text-transform:uppercase;
    }
    input, select, textarea {
      width:100%;
      height:34px;
      padding:0 10px;
      border:1px solid var(--border);
      border-radius:4px;
      background:rgba(0,0,0,0.5);
      color:var(--text);
      font-family:var(--mono); font-size:12px;
      outline:none;
      transition:border-color 150ms, box-shadow 150ms;
    }
    input:focus, select:focus, textarea:focus {
      border-color:var(--glow);
      box-shadow:0 0 0 2px rgba(0,210,180,0.1);
    }
    textarea { height:70px; padding:8px 10px; resize:vertical; }

    /* ── BUTTONS ─────────────────────────────────────────────── */
    button {
      display:inline-flex; align-items:center; justify-content:center;
      height:34px; padding:0 14px;
      border:1px solid var(--border);
      border-radius:4px;
      background:rgba(0,0,0,0.4);
      color:var(--text);
      font-family:var(--mono); font-size:11px; letter-spacing:1px;
      cursor:pointer; white-space:nowrap;
      transition:all 140ms;
      position:relative; overflow:hidden;
    }
    button::before {
      content:''; position:absolute; inset:0;
      background:linear-gradient(135deg,rgba(255,255,255,0.03),transparent);
    }
    button:hover  { border-color:var(--glow); color:var(--glow); box-shadow:0 0 12px rgba(0,210,180,0.15); }
    button:active { transform:scale(0.97); }
    button:disabled { opacity:0.35; cursor:not-allowed; }

    button.btn-primary {
      background:rgba(0,210,180,0.08);
      border-color:rgba(0,210,180,0.4);
      color:var(--glow);
    }
    button.btn-primary:hover { background:rgba(0,210,180,0.16); box-shadow:0 0 20px rgba(0,210,180,0.2); }

    button.btn-secondary {
      background:rgba(0,170,255,0.07);
      border-color:rgba(0,170,255,0.3);
      color:var(--glow2);
    }
    button.btn-secondary:hover { background:rgba(0,170,255,0.14); box-shadow:0 0 20px rgba(0,170,255,0.18); }

    button.btn-warn {
      background:rgba(245,184,67,0.07);
      border-color:rgba(245,184,67,0.3);
      color:var(--amber);
    }
    button.btn-danger {
      background:rgba(255,77,109,0.08);
      border-color:rgba(255,77,109,0.3);
      color:var(--glow3);
    }
    button.btn-danger:hover { box-shadow:0 0 16px rgba(255,77,109,0.2); }

    .btn-row { display:flex; gap:8px; flex-wrap:wrap; }
    .btn-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:8px; }
    .btn-grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:8px; }

    /* ── ACTION CARDS ────────────────────────────────────────── */
    #actions-grid {
      display:grid;
      grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
      gap:10px;
    }
    .action-card {
      padding:12px;
      border:1px solid var(--border);
      border-radius:var(--r);
      background:rgba(6,10,14,0.8);
      cursor:pointer;
      transition:all 160ms;
      display:flex; flex-direction:column; gap:6px;
    }
    .action-card:hover {
      border-color:rgba(0,210,180,0.35);
      background:rgba(0,210,180,0.04);
      box-shadow:0 0 20px rgba(0,210,180,0.06);
    }
    .action-card.active {
      border-color:var(--glow);
      background:rgba(0,210,180,0.08);
      box-shadow:0 0 0 1px rgba(0,210,180,0.2), 0 0 30px rgba(0,210,180,0.1);
    }
    .ac-name {
      font-family:var(--sans); font-size:13px; font-weight:600; color:var(--text);
      line-height:1.2;
    }
    .ac-tag  { font-family:var(--mono); font-size:9px; color:var(--muted); letter-spacing:1px; }
    .ac-desc { font-size:11px; color:var(--muted); line-height:1.4; flex:1; }
    .ac-sfx  { font-family:var(--mono); font-size:9px; color:var(--glow2); }
    .ac-btns { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-top:4px; }
    .ac-btns button { height:28px; font-size:10px; padding:0 8px; }

    /* ── STATUS METRICS ──────────────────────────────────────── */
    .metrics-grid {
      display:grid; grid-template-columns:1fr 1fr; gap:8px;
    }
    .metric {
      padding:9px 10px;
      border:1px solid var(--border);
      border-radius:4px;
      background:rgba(0,0,0,0.3);
    }
    .metric .m-label {
      font-family:var(--mono); font-size:9px; letter-spacing:2px;
      color:var(--muted2); text-transform:uppercase; margin-bottom:3px;
    }
    .metric .m-val {
      font-family:var(--mono); font-size:12px; color:var(--text);
      overflow-wrap:anywhere; line-height:1.3;
    }

    /* ── LOG / PRE ───────────────────────────────────────────── */
    pre {
      font-family:var(--mono); font-size:11px; color:#7fa89a;
      line-height:1.5;
      padding:10px;
      background:rgba(0,0,0,0.5);
      border:1px solid var(--border);
      border-radius:4px;
      overflow:auto;
      max-height:200px;
      min-height:80px;
      white-space:pre-wrap;
      word-break:break-all;
    }
    pre.log-big { max-height:260px; }

    /* ── TOOLBAR STRIP ───────────────────────────────────────── */
    .toolbar {
      display:flex; align-items:flex-end; gap:10px; flex-wrap:wrap;
    }
    .toolbar label.field { min-width:130px; }
    .toolbar button { align-self:flex-end; }
    .toolbar .spacer { flex:1; }

    /* ── SECTION DIVIDERS (full-width in main col) ───────────── */
    .divider {
      height:1px; background:var(--border); margin:4px 0;
    }

    /* ── ENERGY BAR ──────────────────────────────────────────── */
    .energy-bar-wrap {
      height:4px; border-radius:2px;
      background:rgba(0,210,180,0.08);
      overflow:hidden; margin-top:4px;
    }
    .energy-bar-fill {
      height:100%; border-radius:2px;
      background:linear-gradient(90deg,var(--glow),var(--glow2));
      box-shadow:0 0 6px var(--glow);
      transition:width 600ms cubic-bezier(.4,0,.2,1);
    }

    /* ── ANIMATIONS ──────────────────────────────────────────── */
    @keyframes fadeIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
    .panel { animation:fadeIn 300ms ease both; }

    /* ── SCROLLBAR ───────────────────────────────────────────── */
    ::-webkit-scrollbar { width:4px; height:4px; }
    ::-webkit-scrollbar-track { background:transparent; }
    ::-webkit-scrollbar-thumb { background:var(--muted2); border-radius:2px; }

    /* ── RESPONSIVE ──────────────────────────────────────────── */
    @media(max-width:1300px) {
      #main { grid-template-columns:260px 1fr; }
      #col-right { grid-column:1/-1; display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    }
    @media(max-width:1000px) {
      #hero { grid-template-columns:1fr; }
      #hero-left, #hero-right { display:none; }
      #hero-center { grid-column:1; }
      #main { grid-template-columns:1fr; padding:12px; }
      #col-right { grid-template-columns:1fr; }
      .metric-strip { grid-template-columns:repeat(3,1fr); }
    }
    @media(max-width:600px) {
      #topbar { padding:10px 12px; gap:10px; }
      .logo-name { font-size:14px; }
      .logo-sub { display:none; }
      .nav-links { display:none; }
      #hero { padding:12px 12px 0; }
      .metric-strip { grid-template-columns:1fr 1fr; }
      #actions-grid { grid-template-columns:1fr 1fr; }
    }
  </style>
</head>
<body>
<div class="scanline"></div>
<div id="app">

  <!-- ── TOPBAR ─────────────────────────────────────────── -->
  <header id="topbar">
    <div class="logo">
      <div class="logo-badge">MB</div>
      <div class="logo-text">
        <div class="logo-name">M<span>BUDDY</span></div>
        <div class="logo-sub">Control Center v4</div>
      </div>
    </div>
    <div class="topbar-spacer"></div>
    <nav class="nav-links">
      <a href="#actions-section">ACTIONS</a>
      <a href="#hardware-section">HW LINK</a>
      <a href="#tunnel-section">TUNNEL</a>
      <a href="#speech-section">SPEECH</a>
      <a href="#logs-section">LOGS</a>
    </nav>
    <div class="status-pill">
      <span class="dot" id="connDot"></span>
      <span id="connText" style="font-size:10px;">checking</span>
    </div>
  </header>

  <!-- ── HERO ───────────────────────────────────────────── -->
  <section id="hero">

    <!-- LEFT: quick status metrics -->
    <div id="hero-left">
      <div class="side-panel">
        <div class="panel-title">System Status</div>
        <div style="display:flex;flex-direction:column;gap:8px;">
          <div class="metric"><div class="m-label">Mode</div><div class="m-val" id="mode">—</div></div>
          <div class="metric"><div class="m-label">Remote</div><div class="m-val" id="remote">—</div></div>
          <div class="metric"><div class="m-label">Emotion</div><div class="m-val" id="emotion">—</div></div>
          <div class="metric">
            <div class="m-label">Energy</div>
            <div class="m-val" id="energy">—</div>
            <div class="energy-bar-wrap"><div class="energy-bar-fill" id="energyBar" style="width:0%"></div></div>
          </div>
        </div>
      </div>
      <div class="side-panel">
        <div class="panel-title">Last Interaction</div>
        <div style="display:flex;flex-direction:column;gap:8px;">
          <div class="metric"><div class="m-label">Heard</div><div class="m-val" id="heard">—</div></div>
          <div class="metric"><div class="m-label">Reply</div><div class="m-val" id="reply">—</div></div>
          <div class="metric"><div class="m-label">Action</div><div class="m-val" id="action">—</div></div>
          <div class="metric"><div class="m-label">SFX</div><div class="m-val" id="sfx">—</div></div>
        </div>
      </div>
    </div>

    <!-- CENTER: OLED hero display -->
    <div id="hero-center">
      <div class="oled-frame">
        <div class="oled-corners"></div>
        <div class="oled-frame-inner"></div>
        <div class="oled-scanlines"></div>
        <canvas id="oled" width="128" height="64"></canvas>
        <div class="oled-status-bar">
          <div class="oled-label">ANIM: <span id="previewName">IDLE</span></div>
          <div class="oled-label">SRC: <span id="targetState">auto</span></div>
          <div class="oled-label">128×64 · 1-BIT · MONO</div>
        </div>
      </div>
      <!-- Hero metric strip below canvas -->
      <div class="metric-strip">
        <div class="metric-cell"><div class="mc-label">Serial</div><div class="mc-val" id="serial">—</div></div>
        <div class="metric-cell"><div class="mc-label">Needs</div><div class="mc-val" id="needs">—</div></div>
        <div class="metric-cell"><div class="mc-label">Thought</div><div class="mc-val" id="actionThought">—</div></div>
      </div>
    </div>

    <!-- RIGHT: quick controls -->
    <div id="hero-right">
      <div class="side-panel">
        <div class="panel-title">Quick Controls</div>
        <div style="display:flex;flex-direction:column;gap:8px;">
          <div class="field-label">Transport</div>
          <select id="target">
            <option value="auto">auto</option>
            <option value="wireless">wireless</option>
            <option value="serial">serial</option>
            <option value="both">both</option>
          </select>
          <div class="field-label" style="margin-top:4px;">Duration (ms)</div>
          <input id="duration" type="number" value="2200" min="250" max="10000" step="250">
          <button class="btn-primary" style="width:100%;margin-top:4px;" onclick="fireAction(selectedAction())">▶ SEND SELECTED</button>
          <button class="btn-warn" style="width:100%;" onclick="clearAction()">✕ CLEAR ACTION</button>
          <button class="btn-secondary" style="width:100%;" id="autoVisualsBtn" onclick="toggleAutoVisuals()">⏸ PAUSE AUTO VISUALS</button>
        </div>
      </div>
      <div class="side-panel">
        <div class="panel-title">Protocol</div>
        <div class="btn-grid-2" style="gap:8px;">
          <button onclick="command('thinking')">THINKING</button>
          <button onclick="command('heard')">HEARD</button>
          <button onclick="command('quiet')">QUIET</button>
          <button class="btn-danger" onclick="command('stop')">STOP</button>
        </div>
        <div style="display:flex;gap:8px;margin-top:10px;">
          <input id="rawTag" value="[ACT:clear]" style="flex:1;">
          <button class="btn-secondary" onclick="sendRawTag()" style="white-space:nowrap;">SEND</button>
        </div>
      </div>
    </div>
  </section>

  <!-- ── MAIN 3-COL GRID ────────────────────────────────── -->
  <div id="main">

    <!-- LEFT COL -->
    <div id="col-left">

      <!-- State & Energy -->
      <div class="panel" id="state-section">
        <div class="panel-head"><h2><span class="ph-accent"></span>State &amp; Energy</h2></div>
        <div class="panel-body" style="display:flex;flex-direction:column;gap:10px;">
          <label class="field">
            <span class="field-label">State</span>
            <select id="stateSelect"></select>
          </label>
          <button class="btn-primary" style="width:100%;" onclick="setState()">APPLY STATE</button>
          <label class="field">
            <span class="field-label">Energy Level</span>
            <input id="energyInput" type="number" value="65" min="0" max="100" step="1">
          </label>
          <button class="btn-secondary" style="width:100%;" onclick="setEnergy()">SYNC ENERGY</button>
        </div>
      </div>

      <!-- Hardware -->
      <div class="panel" id="hardware-section">
        <div class="panel-head"><h2><span class="ph-accent"></span>Hardware Link</h2></div>
        <div class="panel-body" style="display:flex;flex-direction:column;gap:8px;">
          <div class="btn-grid">
            <button class="btn-secondary" onclick="refreshPorts()">SCAN USB</button>
            <button class="btn-warn" onclick="disconnectSerial()">DISCONNECT</button>
            <button onclick="command('heard')" style="grid-column:1/-1;">PING HARDWARE</button>
          </div>
          <div id="ports" style="display:flex;flex-direction:column;gap:6px;"></div>
          <pre id="serialLog" style="min-height:70px;max-height:130px;"></pre>
        </div>
      </div>

      <!-- Tunnel -->
      <div class="panel" id="tunnel-section">
        <div class="panel-head"><h2><span class="ph-accent"></span>Tunnel</h2></div>
        <div class="panel-body" style="display:flex;flex-direction:column;gap:8px;">
          <div class="btn-grid">
            <button class="btn-primary" onclick="startTunnel()">START</button>
            <button class="btn-warn" onclick="stopTunnel()">STOP</button>
            <button class="btn-secondary" onclick="copyTunnel()" style="grid-column:1/-1;">COPY URL</button>
          </div>
          <div class="metric"><div class="m-label">Public URL</div><div class="m-val" id="tunnelUrl" style="font-size:10px;color:var(--glow2);">—</div></div>
          <pre id="tunnelLog" style="min-height:60px;max-height:120px;"></pre>
        </div>
      </div>

    </div>

    <!-- MID COL -->
    <div id="col-mid">

      <!-- Actions -->
      <div class="panel" id="actions-section">
        <div class="panel-head">
          <h2><span class="ph-accent"></span>Action Modes</h2>
          <div class="btn-row">
            <button class="btn-warn" style="height:28px;font-size:10px;" onclick="clearAction()">CLEAR</button>
            <button class="btn-primary" style="height:28px;font-size:10px;" onclick="fireAction(selectedAction())">FIRE SELECTED</button>
          </div>
        </div>
        <div class="panel-body">
          <div id="actions-grid"></div>
        </div>
      </div>

      <!-- Direct SFX -->
      <div class="panel">
        <div class="panel-head"><h2><span class="ph-accent"></span>Direct SFX</h2></div>
        <div class="panel-body">
          <div class="btn-grid" id="sfxButtons"></div>
        </div>
      </div>

    </div>

    <!-- RIGHT COL -->
    <div id="col-right">

      <!-- Speech -->
      <div class="panel" id="speech-section">
        <div class="panel-head"><h2><span class="ph-accent"></span>Speech</h2></div>
        <div class="panel-body" style="display:flex;flex-direction:column;gap:10px;">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <label class="field"><span class="field-label">Voice State</span><select id="sayState"></select></label>
            <label class="field"><span class="field-label">Language</span>
              <select id="lang"><option value="en">EN</option><option value="ar">AR</option></select>
            </label>
          </div>
          <label class="field"><span class="field-label">Text</span>
            <textarea id="sayText">manual test line from dashboard</textarea>
          </label>
          <button class="btn-primary" style="width:100%;" onclick="sayText()">▶ SPEAK</button>
        </div>
      </div>

      <!-- Emotion Authority -->
      <div class="panel">
        <div class="panel-head"><h2><span class="ph-accent"></span>Emotion Authority</h2></div>
        <div class="panel-body" style="display:flex;flex-direction:column;gap:8px;">
          <div class="metric"><div class="m-label">Cognitive</div><div class="m-val" id="cogEmotion">---</div></div>
          <div class="metric"><div class="m-label">LLM Suggested</div><div class="m-val" id="llmEmotion">---</div></div>
          <div class="metric"><div class="m-label">Final</div><div class="m-val" id="finalEmotion">---</div></div>
          <div class="metric"><div class="m-label">Authority</div><div class="m-val" id="emotionAuthority">---</div></div>
          <div class="metric"><div class="m-label">Decision</div><div class="m-val" id="emotionAccepted">---</div></div>
          <div class="metric"><div class="m-label">Rejected Reason</div><div class="m-val" id="emotionReject">---</div></div>
        </div>
      </div>

      <!-- World Model -->
      <div class="panel">
        <div class="panel-head"><h2><span class="ph-accent"></span>World Model</h2></div>
        <div class="panel-body" style="display:flex;flex-direction:column;gap:8px;">
          <div class="metric"><div class="m-label">Runtime Time</div><div class="m-val" id="worldTime">---</div></div>
          <div class="metric"><div class="m-label">Period</div><div class="m-val" id="worldPeriod">---</div></div>
          <div class="metric"><div class="m-label">Internet</div><div class="m-val" id="worldInternet">---</div></div>
          <div class="metric"><div class="m-label">WiFi</div><div class="m-val" id="worldWifi">---</div></div>
          <div class="metric"><div class="m-label">Active Goal</div><div class="m-val" id="worldGoal">---</div></div>
          <div class="metric"><div class="m-label">Disconnected</div><div class="m-val" id="worldDisconnected">---</div></div>
        </div>
      </div>

      <!-- Raw Status -->
      <div class="panel">
        <div class="panel-head"><h2><span class="ph-accent"></span>Raw Status</h2></div>
        <div class="panel-body"><pre id="raw" style="max-height:220px;">{}</pre></div>
      </div>

      <!-- Logs -->
      <div class="panel" id="logs-section">
        <div class="panel-head">
          <h2><span class="ph-accent"></span>Event Log</h2>
          <div class="btn-row">
            <button class="btn-secondary" style="height:26px;font-size:10px;" onclick="exportLogs()">EXPORT</button>
            <button class="btn-warn" style="height:26px;font-size:10px;" onclick="clearLogs()">CLEAR</button>
          </div>
        </div>
        <div class="panel-body"><pre id="log" class="log-big" style="max-height:240px;min-height:100px;"></pre></div>
      </div>

    </div>
  </div>
</div>

<script>
  /* ── DATA ─────────────────────────────────────────── */
  const ACTION_META = [
    ["joystick","Stickman Duet","Reference-style stickman duet with center fire motif.","laser_pew"],
    ["interactive_arena","Interactive Arena","Joystick command panel with routing arrows and two robot sprites.","data_blip"],
    ["dragon_fire","Dragon Fire","Full-screen dragon swoop with dithered fire-breath cleanse.","dragon_breath"],
    ["data_weaver","Data Weaver","Full-matrix bus tracks and packets guarded around the eye bounds.","data_blip"],
    ["pixel_cascade","Pixel Cascade","Full-screen matrix waterfall with variable stream speeds.","cascade_arp"],
    ["heartbeat","Heartbeat Monitor","Lower-band ECG waveform with QRS spike timing.","ecg_beep"],
    ["thought_bubble","Thought Bubble","Left compressed eyes with bouncing target in a comic bubble.","bubble_pop"],
    ["block_break","Block Break","Autonomous brick wall, ball, and AI paddle simulation.","brick_hit"],
    ["starlight","Starlight Dance","Behind-eye 4-point cross stars with staggered twinkle.","star_tinkle"],
    ["clock_timer","Clock Timer","Bottom analog dial with accelerated minute and hour hands.","clock_tick"],
    ["wifi_search","Wi-Fi Search","Centered transmitter with expanding upper arcs.","sonar_ping"],
    ["battery_charge","Battery Charge","Large battery frame with segmented fill and full-flash state.","charge_hum"],
    ["error_glitch","Error Glitch","Reference-style noisy circuit-board failure screen.","glitch_screech"],
    ["scanning_targeting","Scanning Targeting","Grid targeting board with animated crosshair lock.","data_blip"],
    ["dreaming","Dreaming","Sleeping robot head with moon, drifting stars, and dream bubbles.","sleep_dream"],
    ["celebration","Celebration","Star-eyed robot head jump with confetti after successful tasks.","success_chime"],
    ["listening_animation","Listening","Focused robot head receiving audio waves from both sides.","listen_ping"]
  ];
  const META_BY_NAME = Object.fromEntries(ACTION_META.map(m=>[m[0],m]));
  let manifest = { actions:[], sfx:[], states:[] };
  let lastAction = "";
  let previewTick = 0;
  let eventLog = [];
  let latestStatus = {};
  let autoVisuals = true;
  let busyCount = 0;
  const $ = id => document.getElementById(id);

  /* ── LOG ─────────────────────────────────────────── */
  function log(line) {
    const entry = { time: new Date().toISOString(), line };
    eventLog.unshift(entry);
    eventLog = eventLog.slice(0, 500);
    $("log").textContent = eventLog.map(e=>`[${new Date(e.time).toLocaleTimeString()}] ${e.line}`).join("\n");
  }

  /* ── BUSY ────────────────────────────────────────── */
  function setBusy(active) {
    busyCount = Math.max(0, busyCount + (active ? 1 : -1));
    document.querySelectorAll("button").forEach(b => { b.disabled = busyCount > 0; });
  }

  /* ── POST ────────────────────────────────────────── */
  async function post(url, body = {}) {
    setBusy(true);
    try {
      const res = await fetch(url, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify(body)
      });
      const text = await res.text();
      if (!res.ok) throw new Error(text || res.statusText);
      try { return JSON.parse(text); } catch { return { ok:true, text }; }
    } finally { setBusy(false); }
  }

  /* ── ACTIONS ─────────────────────────────────────── */
  function selectedAction()  { return lastAction; }
  function selectedTarget()  { return $("target") ? $("target").value : "auto"; }

  async function fireAction(name) {
    if (!name) { log("select an action first"); return; }
    lastAction = name; syncSelection();
    const duration_ms = Number($("duration").value || 2200);
    const data = await post("/control/action", { name, duration_ms, target: selectedTarget() });
    log(`action ${name} ${duration_ms}ms${data.thought ? ` — ${data.thought}` : ""}`);
    await refreshStatus();
  }
  async function clearAction() {
    await post("/control/action/clear", { target: selectedTarget() });
    lastAction = ""; syncSelection(); log("action cleared"); await refreshStatus();
  }
  async function toggleAutoVisuals() {
    const next = !autoVisuals;
    const data = await post("/control/auto_visuals", { enabled:next, target:selectedTarget() });
    autoVisuals = Boolean(data.auto_visuals); renderAutoVisuals();
    log(`auto visuals ${autoVisuals?"enabled":"paused"}`); await refreshStatus();
  }
  async function fireSfx(name) {
    await post("/control/sfx", { name, target:selectedTarget() });
    log(`sfx ${name}`); await refreshStatus();
  }
  async function fireModePair(name) {
    await fireAction(name);
    const sfx = (META_BY_NAME[name]||[])[3];
    if (sfx) await fireSfx(sfx);
  }
  async function setState() {
    const state = $("stateSelect").value;
    await post("/control/state", { state, target:selectedTarget() });
    log(`state ${state}`); await refreshStatus();
  }
  async function setEnergy() {
    const level = Number($("energyInput").value || 50);
    await post("/control/energy", { level, target:selectedTarget() });
    log(`energy ${level}`); await refreshStatus();
  }
  async function sayText() {
    const text=$("sayText").value, state=$("sayState").value, lang=$("lang").value;
    await post("/control/say", { text, state, lang, target:selectedTarget() });
    log(`say ${state}/${lang}: ${text}`); await refreshStatus();
  }
  async function command(name) {
    await post(`/control/${name}`, { target:selectedTarget() });
    log(`command ${name}`); await refreshStatus();
  }
  async function sendRawTag() {
    const tag = $("rawTag").value;
    await post("/control/tag", { tag, target:selectedTarget() });
    log(`tag ${tag}`); await refreshStatus();
  }

  /* ── SYNC UI ─────────────────────────────────────── */
  function syncSelection() {
    $("previewName").textContent = (lastAction || "IDLE").toUpperCase();
    $("rawTag").value = lastAction ? `[ACT:${lastAction}:${Number($("duration").value||2200)}]` : "[ACT:clear]";
    $("targetState").textContent = selectedTarget();
    document.querySelectorAll("[data-action]").forEach(el => {
      el.classList.toggle("active", el.dataset.action === lastAction);
    });
  }
  function renderAutoVisuals() {
    const btn = $("autoVisualsBtn");
    if (!btn) return;
    btn.textContent = autoVisuals ? "⏸ PAUSE AUTO VISUALS" : "▶ RESUME AUTO VISUALS";
    btn.className = autoVisuals ? "btn-secondary" : "btn-primary";
    btn.style.width = "100%";
  }

  /* ── MANIFEST / RENDER ───────────────────────────── */
  function renderManifest() {
    const actions = manifest.actions.length ? manifest.actions : ACTION_META.map(m=>m[0]);
    $("actions-grid").innerHTML = actions.map(name => {
      const meta = META_BY_NAME[name] || [name,name,"Custom action",""];
      return `<div class="action-card" data-action="${name}" onclick="lastAction='${name}'; syncSelection();">
        <div class="ac-name">${meta[1]}</div>
        <div class="ac-tag">${name}</div>
        <div class="ac-desc">${meta[2]}</div>
        <div class="ac-sfx">SFX: ${meta[3]||"—"}</div>
        <div class="ac-btns">
          <button class="btn-secondary" onclick="event.stopPropagation();fireAction('${name}')">VISUAL</button>
          <button class="btn-primary" onclick="event.stopPropagation();fireModePair('${name}')">+SFX</button>
        </div>
      </div>`;
    }).join("");
    $("sfxButtons").innerHTML = manifest.sfx.map(name =>
      `<button onclick="fireSfx('${name}')">${name}</button>`
    ).join("");
    const opts = manifest.states.map(s=>`<option value="${s}">${s}</option>`).join("");
    $("stateSelect").innerHTML = opts;
    $("sayState").innerHTML = opts;
    syncSelection();
  }

  /* ── STATUS ──────────────────────────────────────── */
  async function refreshStatus() {
    const res = await fetch("/status");
    const status = await res.json();
    latestStatus = status;
    autoVisuals = Boolean(status.auto_visuals ?? autoVisuals);
    renderAutoVisuals();
    $("connDot").className = status.device_connected ? "dot on" : "dot";
    $("connText").textContent = status.device_connected ? "device connected" : "device offline";
    $("mode").textContent = `${status.mode} / ${status.mode_reason}`;
    $("remote").textContent = status.remote || "—";
    $("emotion").textContent = status.emotion || "—";
    const eng = Math.round(status.energy ?? 0);
    $("energy").textContent = eng;
    $("energyBar").style.width = eng + "%";
    $("action").textContent = status.current_action || "—";
    $("actionThought").textContent = status.current_action_thought || "—";
    $("sfx").textContent = status.last_sfx || "—";
    const needs = status.needs || {};
    $("needs").textContent = Object.keys(needs).length
      ? Object.entries(needs).map(([k,v])=>`${k}:${Math.round(v)}`).join(" ") : "—";
    renderSerial(status.serial);
    renderTunnel(status.tunnel);
    $("heard").textContent = status.last_heard || "—";
    $("reply").textContent = status.last_reply || "—";
    const ed = status.emotion_decision || {};
    $("cogEmotion").textContent = ed.cognitive_emotion || "---";
    $("llmEmotion").textContent = ed.llm_suggested_emotion || "---";
    $("finalEmotion").textContent = ed.final_emotion || "---";
    $("emotionAuthority").textContent = ed.authority_source || "---";
    $("emotionAccepted").textContent = Object.keys(ed).length
      ? (ed.accepted ? "accepted" : "rejected")
      : "---";
    $("emotionReject").textContent = ed.rejection_reason
      || ((status.emotion_disagreements || [])[0] || {}).rejection_reason
      || "---";
    const wm = status.world_model || {};
    $("worldTime").textContent = wm.current_time
      ? `${wm.day_of_week || ""} ${wm.current_date || ""} ${wm.current_time || ""}`.trim()
      : "---";
    $("worldPeriod").textContent = wm.day_period || "---";
    $("worldInternet").textContent = Object.keys(wm).length ? (wm.internet_available ? "available" : "unavailable") : "---";
    $("worldWifi").textContent = Object.keys(wm).length ? (wm.wifi_connected ? "connected" : "disconnected") : "---";
    $("worldGoal").textContent = (status.behavior_decision || {}).goal || wm.active_goal || "---";
    $("worldDisconnected").textContent = (status.disconnected_features || []).length
      ? status.disconnected_features.map(f=>f.name).join(", ")
      : "---";
    $("raw").textContent = JSON.stringify(status, null, 2);
  }

  /* ── SERIAL ──────────────────────────────────────── */
  async function refreshPorts() {
    const data = await (await fetch("/control/serial/ports")).json();
    $("ports").innerHTML = data.ports.length ? data.ports.map(p =>
      `<button class="btn-secondary" onclick="connectSerial('${p.device}')">${p.device} — ${p.description}</button>`
    ).join("") : `<button disabled>No USB serial ports</button>`;
    renderSerial(data.serial); log("serial ports refreshed");
  }
  async function connectSerial(port) {
    const data = await post("/control/serial/connect", { port, baud:115200 });
    renderSerial(data.serial); log(`serial connected ${port}`); await refreshStatus();
  }
  async function disconnectSerial() {
    const data = await post("/control/serial/disconnect", {});
    renderSerial(data.serial); log("serial disconnected"); await refreshStatus();
  }
  function renderSerial(serial) {
    if (!serial) return;
    $("serial").textContent = serial.connected ? `${serial.port} @ ${serial.baud}` : "offline";
    $("serialLog").textContent = (serial.logs||[]).join("\n") || serial.last_error || "—";
  }

  /* ── TUNNEL ──────────────────────────────────────── */
  async function startTunnel() {
    const data = await post("/control/tunnel/start", { url:"http://127.0.0.1:5000" });
    renderTunnel(data.tunnel); log("tunnel start requested");
  }
  async function stopTunnel() {
    const data = await post("/control/tunnel/stop", {});
    renderTunnel(data.tunnel); log("tunnel stopped");
  }
  async function copyTunnel() {
    const url = $("tunnelUrl").textContent;
    if (url && url !== "—") await navigator.clipboard.writeText(url);
    log(`copied tunnel ${url}`);
  }
  function renderTunnel(tunnel) {
    if (!tunnel) return;
    $("tunnelUrl").textContent = tunnel.url || (tunnel.running ? "starting..." : "—");
    $("tunnelLog").textContent = (tunnel.logs||[]).join("\n") || tunnel.last_error || "—";
  }

  /* ── LOGS ────────────────────────────────────────── */
  function exportLogs() {
    const payload = { exported_at:new Date().toISOString(), event_log:eventLog, status:latestStatus,
      serial_log:($("serialLog").textContent||"").split("\n"), tunnel_log:($("tunnelLog").textContent||"").split("\n") };
    const blob = new Blob([JSON.stringify(payload,null,2)], {type:"application/json"});
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `mbuddy-logs-${new Date().toISOString().replace(/[:.]/g,"-")}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    log("logs exported");
  }
  function clearLogs() { eventLog = []; $("log").textContent = ""; }

  /* ── OLED PREVIEW ────────────────────────────────── */
  function px(ctx,x,y,w=1,h=1) { ctx.fillRect(x,y,w,h); }
  function drawLine(ctx,x0,y0,x1,y1) {
    const dx=Math.abs(x1-x0),sx=x0<x1?1:-1,dy=-Math.abs(y1-y0),sy=y0<y1?1:-1;
    let err=dx+dy;
    while(true) {
      px(ctx,x0,y0); if(x0===x1&&y0===y1) break;
      const e2=2*err; if(e2>=dy){err+=dy;x0+=sx;} if(e2<=dx){err+=dx;y0+=sy;}
    }
  }
  function drawEyes(ctx) {
    ctx.strokeStyle="#67d6c0"; ctx.lineWidth=1; ctx.fillStyle="#67d6c0";
    ctx.strokeRect(35,8,22,16); ctx.strokeRect(71,8,22,16);
    px(ctx,44,14,4,4); px(ctx,80,14,4,4);
  }
  function robotHead(ctx,x,y,mood=0) {
    px(ctx,x+8,y,1,3); px(ctx,x+7,y); px(ctx,x+9,y);
    px(ctx,x,y+8,2,5); px(ctx,x+14,y+8,2,5);
    ctx.strokeRect(x+2,y+3,12,12);
    if(mood===1){px(ctx,x+4,y+8,3,1);px(ctx,x+9,y+8,3,1);px(ctx,x+5,y+12);px(ctx,x+10,y+12);}
    else if(mood===2){[[x+5,y+8],[x+10,y+8]].forEach(s=>{px(ctx,s[0],s[1]);px(ctx,s[0]-1,s[1]);px(ctx,s[0]+1,s[1]);px(ctx,s[0],s[1]-1);px(ctx,s[0],s[1]+1);});px(ctx,x+5,y+12,6,1);}
    else{px(ctx,x+4,y+7,3,3);px(ctx,x+9,y+7,3,3);px(ctx,x+5,y+12,6,1);}
  }
  function tri(t,lo,hi) { const span=hi-lo; let p=t%(span*2); if(p>span) p=span*2-p; return lo+p; }

  function preview() {
    const c=$("oled"), ctx=c.getContext("2d");
    previewTick++;
    ctx.fillStyle="#020405"; ctx.fillRect(0,0,128,64);
    ctx.fillStyle="#67d6c0"; ctx.strokeStyle="#67d6c0"; ctx.lineWidth=1;
    const a=selectedAction();
    if(!a){ drawEyes(ctx); requestAnimationFrame(preview); return; }
    if(!["joystick","interactive_arena","dragon_fire","data_weaver","pixel_cascade","block_break","wifi_search","battery_charge","error_glitch","thought_bubble","starlight","clock_timer","heartbeat","scanning_targeting","dreaming","celebration","listening_animation"].includes(a)) drawEyes(ctx);
    if(a==="joystick"){px(ctx,30,19,7,7);drawLine(ctx,34,26,34,45);drawLine(ctx,34,34,25,47);drawLine(ctx,34,34,43,24);drawLine(ctx,34,45,26,56);drawLine(ctx,34,45,42,56);px(ctx,96,19,7,7);drawLine(ctx,100,26,100,45);drawLine(ctx,100,34,91,47);drawLine(ctx,100,34,109,47);drawLine(ctx,100,45,92,56);drawLine(ctx,100,45,108,56);ctx.beginPath();ctx.arc(64,43,8,0,Math.PI*2);ctx.stroke();}
    else if(a==="interactive_arena"){ctx.strokeRect(11,35,22,14);drawLine(ctx,22,35,26,20);ctx.beginPath();ctx.arc(26,19,4,0,Math.PI*2);ctx.stroke();robotHead(ctx,76,8);robotHead(ctx,76,40);drawLine(ctx,68,18,56,18);drawLine(ctx,56,18,56,31);drawLine(ctx,56,31,68,31);}
    else if(a==="dragon_fire"){const hx=70;drawLine(ctx,hx+3,23,hx+20,29);drawLine(ctx,hx+5,39,hx+22,34);drawLine(ctx,hx+8,30,hx+23,34);px(ctx,hx+14,27);for(let x=0;x<70;x++)for(let y=24;y<40;y++)if((x+y+previewTick)%2===0)px(ctx,x,y);}
    else if(a==="data_weaver"){px(ctx,47,27,12,10);px(ctx,69,27,12,10);for(let r=10;r<44;r+=8){ctx.beginPath();ctx.arc(64,32,r,0,Math.PI*2);ctx.stroke();}}
    else if(a==="pixel_cascade"){for(let i=0;i<8;i++){const x=(i*17+11)%128,y=(previewTick*(i%3+1)+i*9)%64;px(ctx,x,y,1,3);for(let t=5;t<12;t+=2)px(ctx,x,(y-t+64)%64);}}
    else if(a==="heartbeat"){robotHead(ctx,56,5);px(ctx,60,26,14,12);px(ctx,8,54,112,1);const x=(previewTick*2)%128;drawLine(ctx,x,54,x+4,51);drawLine(ctx,x+4,51,x+7,58);drawLine(ctx,x+7,58,x+10,34);drawLine(ctx,x+10,34,x+13,62);drawLine(ctx,x+13,62,x+22,54);}
    else if(a==="thought_bubble"){robotHead(ctx,6,43);px(ctx,38,42);px(ctx,42,34);px(ctx,46,26);ctx.strokeRect(50,2,77,49);px(ctx,tri(previewTick*2,52,124),tri(previewTick+9,4,48),2,2);}
    else if(a==="block_break"){for(let r=0;r<2;r++)for(let col=0;col<18;col++)if(col+r*18>previewTick/8)px(ctx,1+col*7,2+r*4,6,2);const bx=tri(previewTick*2,1,126);px(ctx,bx,tri(previewTick*2,10,59));px(ctx,Math.max(0,Math.min(118,bx-5)),60,10,2);}
    else if(a==="starlight"){robotHead(ctx,56,42,1);[[18,15],[33,47],[51,18],[76,50],[96,20],[110,42],[42,31],[86,32]].forEach((s,i)=>{px(ctx,s[0],s[1]);if((previewTick+i*7)%45<5){px(ctx,s[0]-1,s[1]);px(ctx,s[0]+1,s[1]);px(ctx,s[0],s[1]-1);px(ctx,s[0],s[1]+1);}});}
    else if(a==="clock_timer"){ctx.beginPath();ctx.arc(64,48,12,0,Math.PI*2);ctx.stroke();const ang=(previewTick*6-90)*Math.PI/180;drawLine(ctx,64,48,64+Math.round(Math.cos(ang)*9),48+Math.round(Math.sin(ang)*9));}
    else if(a==="wifi_search"){robotHead(ctx,8,40);px(ctx,63,57,3,3);for(let r of[6,14,22]){if((previewTick%45)>r+10){ctx.beginPath();ctx.arc(64,58,r,Math.PI,Math.PI*2);ctx.stroke();}}}
    else if(a==="battery_charge"){robotHead(ctx,6,38,previewTick%55>45?2:0);ctx.strokeRect(42,22,52,22);px(ctx,94,29,4,8);for(let b=0;b<Math.min(4,Math.floor((previewTick%55)/12)+1);b++)px(ctx,46+b*10,26,7,16);}
    else if(a==="error_glitch"){for(let i=0;i<420;i++)if(Math.random()<0.4)px(ctx,Math.random()*128|0,Math.random()*64|0);drawLine(ctx,64,12,44,52);drawLine(ctx,64,12,84,52);drawLine(ctx,44,52,84,52);px(ctx,63,26,3,16);px(ctx,63,46,3,3);}
    else if(a==="scanning_targeting"){for(let x=8;x<124;x+=12)px(ctx,x,4,1,56);for(let y=8;y<60;y+=10)px(ctx,4,y,120,1);ctx.beginPath();ctx.arc(80,33,18,0,Math.PI*2);ctx.stroke();px(ctx,56,33,48,1);px(ctx,80,9,1,48);}
    else if(a==="dreaming"){robotHead(ctx,13,42,1);ctx.beginPath();ctx.arc(92,16,10,0,Math.PI*2);ctx.stroke();[[62,18],[77,35],[108,44]].forEach(s=>{px(ctx,s[0],s[1]);px(ctx,s[0]+1,s[1]);});px(ctx,36,31,3,3);px(ctx,44,24,5,5);}
    else if(a==="celebration"){robotHead(ctx,56,32,2);for(let i=0;i<30;i++)px(ctx,(i*17+previewTick*3)%128,(i*11)%55);drawLine(ctx,48,50,64,38);drawLine(ctx,80,50,64,38);}
    else if(a==="listening_animation"){px(ctx,50,24,28,16);for(let r=4;r<28;r+=8){ctx.beginPath();ctx.arc(28,32,r,-0.7,0.7);ctx.stroke();ctx.beginPath();ctx.arc(100,32,r,Math.PI-0.7,Math.PI+0.7);ctx.stroke();}}
    requestAnimationFrame(preview);
  }

  /* ── BOOT ────────────────────────────────────────── */
  async function boot() {
    try {
      manifest = await (await fetch("/control/manifest")).json();
      autoVisuals = Boolean(manifest.auto_visuals ?? autoVisuals);
      renderAutoVisuals();
      renderManifest();
      await refreshPorts();
      await refreshStatus();
      log("dashboard ready");
    } catch(err) {
      log(`boot error: ${err.message}`);
    }
    setInterval(refreshStatus, 1200);
    requestAnimationFrame(preview);
  }
  boot();
</script>
</body>
</html>"""


async def dashboard_handler(request: web.Request) -> web.Response:
    # What MBUDDY last heard and said is private: local only, like /control.
    from api.device_control import _require_local
    _require_local(request)
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


def register_dashboard_routes(app: web.Application) -> None:
    app.router.add_get("/dashboard", dashboard_handler)
