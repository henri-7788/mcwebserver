from flask import Flask, jsonify, render_template_string, request, Response, stream_with_context
import subprocess, os, socket, struct, json, threading, time

app = Flask(__name__)

# ─── Konfiguration ────────────────────────────────────────────────────────────
SERVER_DIR   = "/home/kype/desktop/Server"   # <-- Pfad zum Serverordner anpassen
START_SCRIPT = "./startserver.sh"       # <-- Startscript (relativ zu SERVER_DIR)
SCREEN_NAME  = "mcserver"
MC_HOST      = "127.0.0.1"
MC_PORT      = 25565

# ─── Zustand ──────────────────────────────────────────────────────────────────
state = {
    "auto_shutdown_enabled": True,
    "auto_shutdown_minutes": 45,
    "empty_since": None,
    "last_player_count": None,
    "shutdown_triggered": False,
}
state_lock = threading.Lock()

# ─── Minecraft Server Ping ────────────────────────────────────────────────────
def _pack_varint(val):
    out = b""
    while True:
        b = val & 0x7F
        val >>= 7
        out += bytes([b | (0x80 if val else 0)])
        if not val:
            return out

def _read_varint(sock):
    num = 0
    for shift in range(0, 35, 7):
        b = sock.recv(1)
        if not b:
            raise ConnectionError("Socket closed")
        byte = b[0]
        num |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return num
    raise ValueError("VarInt too long")

def is_port_open():
    """Prüft nur ob der TCP-Port offen ist (Server läuft, aber vielleicht noch nicht pingbar)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((MC_HOST, MC_PORT))
        sock.close()
        return result == 0
    except Exception:
        return False

_last_ping_error = ""

def mc_ping():
    global _last_ping_error
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((MC_HOST, MC_PORT))
        host_b = MC_HOST.encode("utf-8")
        handshake = (
            b"\x00"
            + _pack_varint(767)
            + _pack_varint(len(host_b)) + host_b
            + struct.pack(">H", MC_PORT)
            + b"\x01"
        )
        sock.send(_pack_varint(len(handshake)) + handshake)
        sock.send(b"\x01\x00")
        length = _read_varint(sock)
        data = b""
        while len(data) < length:
            chunk = sock.recv(min(4096, length - len(data)))
            if not chunk:
                break
            data += chunk
        sock.close()
        # Packet-ID VarInt überspringen
        pos = 0
        while pos < len(data) and (data[pos] & 0x80):
            pos += 1
        pos += 1
        # String-Länge VarInt überspringen
        while pos < len(data) and (data[pos] & 0x80):
            pos += 1
        pos += 1
        if pos >= len(data):
            _last_ping_error = "Kein JSON nach VarInts"
            return None
        info = json.loads(data[pos:].decode("utf-8", errors="replace"))
        # "players" kann None sein (z.B. bei NeoForge/ATM10)
        players = info.get("players") or {}
        _last_ping_error = ""
        return {"online": players.get("online", 0), "max": players.get("max", 0)}
    except Exception as e:
        _last_ping_error = f"{type(e).__name__}: {e}"
        return None

# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────
def is_screen_running():
    r = subprocess.run(["screen", "-ls"], capture_output=True, text=True)
    return SCREEN_NAME in r.stdout

def send_stop():
    # Erst `stop` an Minecraft schicken (damit die Welt gespeichert wird)
    subprocess.run(["screen", "-S", SCREEN_NAME, "-X", "stuff", "stop\n"])
    # Im Hintergrund warten bis MC gestoppt ist, dann Screen-Session killen
    # (verhindert dass das Start-Script den Server nach 10s neustartet)
    threading.Thread(target=_kill_screen_when_stopped, daemon=True).start()

def _kill_screen_when_stopped():
    """Wartet bis der MC-Port geschlossen ist, dann beendet die Screen-Session."""
    time.sleep(5)  # kurz warten damit MC den Stop-Befehl verarbeitet
    deadline = time.time() + 120  # max. 2 Minuten warten
    while time.time() < deadline:
        if not is_port_open():
            break
        time.sleep(2)
    time.sleep(3)  # kleiner Puffer für finales Speichern
    subprocess.run(["screen", "-S", SCREEN_NAME, "-X", "quit"])

def get_log_tail(lines=25):
    log_path = f"/tmp/{SCREEN_NAME}.log"
    try:
        if os.path.exists(log_path):
            with open(log_path, errors="replace") as f:
                return "".join(f.readlines()[-lines:])
    except Exception:
        pass
    return ""

# ─── Hintergrund-Thread: Auto-Shutdown-Wächter ───────────────────────────────
def watchdog():
    while True:
        time.sleep(30)
        try:
            if not is_screen_running():
                with state_lock:
                    state["empty_since"] = None
                    state["last_player_count"] = None
                    state["shutdown_triggered"] = False
                continue

            ping = mc_ping()
            with state_lock:
                if ping is None:
                    state["last_player_count"] = None
                    continue

                count = ping["online"]
                state["last_player_count"] = count

                if not state["auto_shutdown_enabled"]:
                    state["empty_since"] = None
                    state["shutdown_triggered"] = False
                    continue

                if count > 0:
                    state["empty_since"] = None
                    state["shutdown_triggered"] = False
                else:
                    if state["empty_since"] is None:
                        state["empty_since"] = time.time()
                    elapsed = time.time() - state["empty_since"]
                    if elapsed >= state["auto_shutdown_minutes"] * 60 and not state["shutdown_triggered"]:
                        state["shutdown_triggered"] = True
                        send_stop()
        except Exception:
            pass

threading.Thread(target=watchdog, daemon=True).start()

# ─── HTML ─────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ATM10 Control Panel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@400;700;800&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:       #0d0f14;
    --surface:  #13161e;
    --surface2: #1a1e2a;
    --border:   #252a38;
    --accent:   #f0a500;
    --accent2:  #ff6b35;
    --green:    #3ddc84;
    --red:      #ff4757;
    --text:     #e8eaf0;
    --muted:    #5a6070;
    --font-mono: 'JetBrains Mono', monospace;
    --font-ui:   'Syne', sans-serif;
  }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-ui);
    min-height: 100vh;
    padding: 32px 20px 60px;
    background-image:
      radial-gradient(ellipse 80% 50% at 50% -20%, rgba(240,165,0,0.07) 0%, transparent 60%),
      url("data:image/svg+xml,%3Csvg width='40' height='40' viewBox='0 0 40 40' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' stroke='%23ffffff08' stroke-width='1'%3E%3Cpath d='M0 0h40v40'/%3E%3C/g%3E%3C/svg%3E");
  }
  header {
    max-width: 860px;
    margin: 0 auto 40px;
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
  }
  .logo-area h1 { font-size: clamp(1.6rem, 4vw, 2.2rem); font-weight: 800; letter-spacing: -0.5px; line-height: 1; }
  .logo-area h1 span { color: var(--accent); }
  .logo-area p { font-family: var(--font-mono); font-size: 0.7rem; color: var(--muted); margin-top: 6px; letter-spacing: 0.1em; text-transform: uppercase; }
  #headerStatus { font-family: var(--font-mono); font-size: 0.75rem; letter-spacing: 0.08em; display: flex; align-items: center; gap: 8px; color: var(--muted); }
  #headerDot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); transition: background 0.4s; }
  #headerDot.online  { background: var(--green); box-shadow: 0 0 8px var(--green); animation: pulse 2s infinite; }
  #headerDot.offline { background: var(--red); }
  #headerDot.starting{ background: var(--accent); animation: pulse 1s infinite; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
  main { max-width: 860px; margin: 0 auto; display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 24px; position: relative; overflow: hidden; transition: border-color 0.3s; }
  .card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: linear-gradient(90deg, transparent, var(--accent), transparent); opacity: 0; transition: opacity 0.3s; }
  .card:hover::before { opacity: 1; }
  .card:hover { border-color: #353a4a; }
  .card.full { grid-column: 1 / -1; }
  .card.accent-card::before { opacity: 1; }
  .card-label { font-family: var(--font-mono); font-size: 0.65rem; letter-spacing: 0.15em; text-transform: uppercase; color: var(--muted); margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
  .card-label::after { content: ''; flex: 1; height: 1px; background: var(--border); }
  .status-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
  .stat { background: var(--surface2); border-radius: 8px; padding: 14px 16px; border: 1px solid var(--border); }
  .stat-label { font-family: var(--font-mono); font-size: 0.6rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }
  .stat-value { font-family: var(--font-mono); font-size: 1.3rem; font-weight: 700; line-height: 1; }
  .stat-value.green  { color: var(--green); }
  .stat-value.red    { color: var(--red); }
  .stat-value.amber  { color: var(--accent); }
  .stat-value.muted  { color: var(--muted); }
  .btn-row { display: flex; gap: 10px; flex-wrap: wrap; }
  .btn { font-family: var(--font-ui); font-size: 0.85rem; font-weight: 700; letter-spacing: 0.04em; border: none; border-radius: 8px; padding: 11px 22px; cursor: pointer; transition: all 0.15s; display: flex; align-items: center; gap: 8px; }
  .btn:active { transform: scale(0.97); }
  .btn:disabled { opacity: 0.35; cursor: not-allowed; transform: none; }
  .btn-start { background: var(--green); color: #000; }
  .btn-start:hover:not(:disabled) { background: #5aeeaa; box-shadow: 0 0 16px rgba(61,220,132,0.35); }
  .btn-stop { background: var(--red); color: #fff; }
  .btn-stop:hover:not(:disabled) { background: #ff6b7a; box-shadow: 0 0 16px rgba(255,71,87,0.35); }
  .btn-secondary { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }
  .btn-secondary:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .countdown-display { text-align: center; padding: 8px 0 4px; }
  .countdown-time { font-family: var(--font-mono); font-size: 3rem; font-weight: 700; letter-spacing: 0.05em; color: var(--accent); line-height: 1; transition: color 0.3s; }
  .countdown-time.urgent { color: var(--red); animation: pulse 1s infinite; }
  .countdown-time.inactive { color: var(--muted); font-size: 1.5rem; }
  .countdown-sub { font-family: var(--font-mono); font-size: 0.68rem; letter-spacing: 0.1em; color: var(--muted); text-transform: uppercase; margin-top: 8px; }
  .progress-bar-wrap { background: var(--surface2); border-radius: 100px; height: 4px; margin-top: 16px; overflow: hidden; }
  #progressBar { height: 100%; border-radius: 100px; background: linear-gradient(90deg, var(--accent), var(--accent2)); width: 100%; transition: width 1s linear, background 0.3s; }
  #progressBar.urgent { background: linear-gradient(90deg, var(--accent2), var(--red)); }
  .settings-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 4px; }
  .toggle-wrap { display: flex; align-items: center; gap: 10px; }
  .toggle { position: relative; width: 42px; height: 24px; cursor: pointer; }
  .toggle input { display: none; }
  .toggle-slider { position: absolute; inset: 0; background: var(--surface2); border: 1px solid var(--border); border-radius: 100px; transition: 0.25s; }
  .toggle-slider::before { content: ''; position: absolute; width: 16px; height: 16px; left: 3px; top: 3px; background: var(--muted); border-radius: 50%; transition: 0.25s; }
  .toggle input:checked + .toggle-slider { border-color: var(--accent); }
  .toggle input:checked + .toggle-slider::before { transform: translateX(18px); background: var(--accent); }
  .toggle-label { font-family: var(--font-mono); font-size: 0.75rem; color: var(--text); }
  .input-group { display: flex; align-items: center; gap: 8px; margin-left: auto; }
  .input-group label { font-family: var(--font-mono); font-size: 0.72rem; color: var(--muted); white-space: nowrap; }
  .number-input { background: var(--surface2); border: 1px solid var(--border); color: var(--text); font-family: var(--font-mono); font-size: 0.9rem; font-weight: 700; border-radius: 6px; padding: 6px 10px; width: 70px; text-align: center; transition: border-color 0.2s; }
  .number-input:focus { outline: none; border-color: var(--accent); }
  .btn-save { background: var(--surface2); color: var(--accent); border: 1px solid var(--accent); font-family: var(--font-mono); font-size: 0.72rem; letter-spacing: 0.05em; border-radius: 6px; padding: 6px 14px; cursor: pointer; transition: all 0.15s; font-weight: 600; white-space: nowrap; }
  .btn-save:hover { background: var(--accent); color: #000; }
  .btn-save:active { transform: scale(0.97); }
  .console { background: #080a0f; border: 1px solid var(--border); border-radius: 8px; padding: 14px; font-family: var(--font-mono); font-size: 0.72rem; line-height: 1.6; max-height: 260px; overflow-y: auto; white-space: pre-wrap; color: #8a9bb0; scrollbar-width: thin; scrollbar-color: var(--border) transparent; }
  .console::-webkit-scrollbar { width: 4px; }
  .console::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  #toast { position: fixed; bottom: 28px; right: 28px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 20px; font-family: var(--font-mono); font-size: 0.78rem; color: var(--text); opacity: 0; transform: translateY(10px); transition: all 0.25s; pointer-events: none; z-index: 99; max-width: 320px; }
  #toast.show { opacity: 1; transform: translateY(0); }
  #toast.ok  { border-color: var(--green); }
  #toast.err { border-color: var(--red); }
  @media (max-width: 580px) {
    main { grid-template-columns: 1fr; }
    .card.full { grid-column: 1; }
    .status-grid { grid-template-columns: 1fr 1fr; }
    .input-group { margin-left: 0; }
  }
</style>
</head>
<body>
<header>
  <div class="logo-area">
    <h1>ATM<span>10</span> Control</h1>
    <p>All The Mods 10 &nbsp;·&nbsp; Server Dashboard</p>
  </div>
  <div id="headerStatus">
    <div id="headerDot"></div>
    <span id="headerStatusText">Verbinde...</span>
  </div>
</header>
<main>
  <div class="card full accent-card">
    <div class="card-label">Server-Status</div>
    <div class="status-grid">
      <div class="stat">
        <div class="stat-label">Zustand</div>
        <div class="stat-value" id="statStatus">–</div>
      </div>
      <div class="stat">
        <div class="stat-label">Spieler Online</div>
        <div class="stat-value amber" id="statPlayers">–</div>
      </div>
      <div class="stat">
        <div class="stat-label">Letzte Prüfung</div>
        <div class="stat-value muted" id="statTime">–</div>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-label">Steuerung</div>
    <div class="btn-row">
      <button class="btn btn-start" id="btnStart" onclick="doAction('start')">▶ &nbsp;Starten</button>
      <button class="btn btn-stop"  id="btnStop"  onclick="doAction('stop')">■ &nbsp;Stoppen</button>
      <button class="btn btn-secondary" onclick="fetchStatus()">↻ &nbsp;Refresh</button>
    </div>
  </div>
  <div class="card">
    <div class="card-label">Auto-Shutdown Timer</div>
    <div class="countdown-display">
      <div class="countdown-time inactive" id="countdownTime">–</div>
      <div class="countdown-sub" id="countdownSub">warte auf Daten...</div>
    </div>
    <div class="progress-bar-wrap">
      <div id="progressBar" style="width:0%"></div>
    </div>
  </div>
  <div class="card full">
    <div class="card-label">Auto-Shutdown Einstellungen</div>
    <div class="settings-row">
      <div class="toggle-wrap">
        <label class="toggle">
          <input type="checkbox" id="toggleEnabled" onchange="saveSettings()">
          <span class="toggle-slider"></span>
        </label>
        <span class="toggle-label" id="toggleLabel">Auto-Shutdown aktiv</span>
      </div>
      <div class="input-group">
        <label for="inputMinutes">Timeout nach</label>
        <input type="number" class="number-input" id="inputMinutes" min="1" max="1440" value="45">
        <label>Min.</label>
        <button class="btn-save" onclick="saveSettings()">Speichern</button>
      </div>
    </div>
  </div>
  <div class="card full">
    <div class="card-label">Server-Konsole (letzte Zeilen)</div>
    <div class="console" id="console">Warte auf Log-Ausgabe...</div>
  </div>
</main>
<div id="toast"></div>
<script>
let autoShutdownMinutes = 45;
let screenRunning = false;

function showToast(msg, type='ok') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show ' + type;
  setTimeout(() => { t.className = ''; }, 3200);
}

function fmtTime(s) {
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), ss = Math.floor(s%60);
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
  return `${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
}

function updateCountdown(d) {
  const el  = document.getElementById('countdownTime');
  const sub = document.getElementById('countdownSub');
  const bar = document.getElementById('progressBar');
  const secs = d.remaining_seconds;
  if (!d.auto_shutdown_enabled) {
    el.textContent = 'Deaktiviert'; el.className = 'countdown-time inactive';
    sub.textContent = 'Auto-Shutdown ist ausgeschaltet';
    bar.style.width = '0%'; bar.className = ''; return;
  }
  if (!screenRunning) {
    el.textContent = '–'; el.className = 'countdown-time inactive';
    sub.textContent = 'Server ist offline';
    bar.style.width = '0%'; bar.className = ''; return;
  }
  if (secs === null) {
    el.textContent = 'Aktiv'; el.className = 'countdown-time inactive';
    sub.textContent = 'Spieler sind online – kein Shutdown geplant';
    bar.style.width = '100%'; bar.className = ''; return;
  }
  const pct = Math.round((secs / (autoShutdownMinutes * 60)) * 100);
  const urgent = secs < 300;
  el.textContent = fmtTime(secs);
  el.className = 'countdown-time' + (urgent ? ' urgent' : '');
  sub.textContent = urgent ? '⚠ Server fährt bald herunter!' : `Shutdown wenn ${autoShutdownMinutes} Min. kein Spieler online`;
  bar.style.width = pct + '%';
  bar.className = urgent ? 'urgent' : '';
}

async function fetchStatus() {
  try {
    const d = await (await fetch('/status')).json();
    screenRunning = d.screen_running;
    autoShutdownMinutes = d.auto_shutdown_minutes;
    const dot = document.getElementById('headerDot');
    const htxt = document.getElementById('headerStatusText');
    if (d.mc_online)          { dot.className='online';   htxt.textContent='ONLINE'; }
    else if (d.port_open)     { dot.className='starting'; htxt.textContent='LÄDT...'; }
    else if (d.screen_running){ dot.className='starting'; htxt.textContent='STARTET...'; }
    else                      { dot.className='offline';  htxt.textContent='OFFLINE'; }
    const st = document.getElementById('statStatus');
    if (d.mc_online)           { st.textContent='Online';   st.className='stat-value green'; }
    else if (d.port_open)      { st.textContent='Lädt…';    st.className='stat-value amber'; }
    else if (d.screen_running) { st.textContent='Startet…'; st.className='stat-value amber'; }
    else                       { st.textContent='Offline';  st.className='stat-value red';   }
    const pEl = document.getElementById('statPlayers');
    pEl.textContent = d.player_count !== null ? `${d.player_count} / ${d.player_max}` : '–';
    document.getElementById('statTime').textContent =
      new Date().toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    document.getElementById('btnStart').disabled = d.screen_running;
    document.getElementById('btnStop').disabled  = !d.screen_running;
    updateCountdown(d);
    document.getElementById('toggleEnabled').checked = d.auto_shutdown_enabled;
    document.getElementById('toggleLabel').textContent = d.auto_shutdown_enabled ? 'Auto-Shutdown aktiv' : 'Auto-Shutdown deaktiviert';
    document.getElementById('inputMinutes').value = d.auto_shutdown_minutes;
  } catch(e) { document.getElementById('headerStatusText').textContent='Fehler'; }
}

async function doAction(cmd) {
  document.getElementById('btnStart').disabled = true;
  document.getElementById('btnStop').disabled  = true;
  if (cmd === 'start') {
    const c = document.getElementById('console');
    c.textContent = '';
  }
  try {
    const d = await (await fetch('/'+cmd,{method:'POST'})).json();
    showToast(d.message, d.ok?'ok':'err');
    setTimeout(fetchStatus, 2000);
  } catch(e) { showToast('Fehler beim Ausführen des Befehls','err'); }
}

async function saveSettings() {
  const enabled = document.getElementById('toggleEnabled').checked;
  const minutes = parseInt(document.getElementById('inputMinutes').value, 10);
  document.getElementById('toggleLabel').textContent = enabled ? 'Auto-Shutdown aktiv' : 'Auto-Shutdown deaktiviert';
  try {
    const d = await (await fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled,minutes})})).json();
    showToast(d.message, d.ok?'ok':'err');
    fetchStatus();
  } catch(e) { showToast('Fehler beim Speichern','err'); }
}

// ── Echtzeit-Konsole via Server-Sent Events ──────────────────────────────────
(function connectConsole() {
  const c = document.getElementById('console');
  let autoScroll = true;
  const MAX_LINES = 500;

  c.addEventListener('scroll', () => {
    autoScroll = c.scrollHeight - c.clientHeight <= c.scrollTop + 40;
  });

  const es = new EventSource('/console-stream');

  es.onmessage = (e) => {
    const line = JSON.parse(e.data);
    const wasEmpty = c.textContent === '' || c.textContent === 'Warte auf Log-Ausgabe...';
    if (wasEmpty) c.textContent = '';
    c.textContent += line + '\n';
    // Puffergröße begrenzen
    const lines = c.textContent.split('\n');
    if (lines.length > MAX_LINES) {
      c.textContent = lines.slice(lines.length - MAX_LINES).join('\n');
    }
    if (autoScroll) c.scrollTop = c.scrollHeight;
  };

  es.onerror = () => {
    es.close();
    setTimeout(connectConsole, 3000); // nach 3s neu verbinden
  };
})();

fetchStatus();
setInterval(fetchStatus, 15000);
</script>
</body>
</html>"""

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/status")
def status():
    running  = is_screen_running()
    port_up  = is_port_open() if running else False
    ping     = mc_ping()      if port_up  else None
    with state_lock:
        rem = None
        if state["auto_shutdown_enabled"] and state["empty_since"] is not None and running:
            elapsed = time.time() - state["empty_since"]
            rem = max(0, state["auto_shutdown_minutes"] * 60 - elapsed)
        return jsonify({
            "screen_running":        running,
            "port_open":             port_up,
            "mc_online":             ping is not None,
            "player_count":          ping["online"] if ping else None,
            "player_max":            ping["max"]    if ping else None,
            "auto_shutdown_enabled": state["auto_shutdown_enabled"],
            "auto_shutdown_minutes": state["auto_shutdown_minutes"],
            "remaining_seconds":     rem,
        })

@app.route("/start", methods=["POST"])
def start():
    if is_screen_running():
        return jsonify({"ok": False, "message": "Server läuft bereits."})
    log_path = f"/tmp/{SCREEN_NAME}.log"
    open(log_path, "w").close()
    subprocess.Popen(
        ["screen", "-dmS", SCREEN_NAME, "-L", "-Logfile", log_path, "bash", START_SCRIPT],
        cwd=SERVER_DIR
    )
    with state_lock:
        state["empty_since"] = None
        state["shutdown_triggered"] = False
    return jsonify({"ok": True, "message": "Server wird gestartet… ATM10 braucht 2–4 Min. zum Laden."})

@app.route("/stop", methods=["POST"])
def stop():
    if not is_screen_running():
        return jsonify({"ok": False, "message": "Server ist bereits gestoppt."})
    send_stop()
    with state_lock:
        state["empty_since"] = None
        state["shutdown_triggered"] = False
    return jsonify({"ok": True, "message": "Stop-Befehl gesendet. Server fährt herunter…"})

@app.route("/settings", methods=["POST"])
def settings():
    data = request.get_json(force=True)
    minutes = int(data.get("minutes", 45))
    enabled = bool(data.get("enabled", True))
    if minutes < 1 or minutes > 1440:
        return jsonify({"ok": False, "message": "Ungültiger Wert (1–1440 Min.)."})
    with state_lock:
        state["auto_shutdown_minutes"] = minutes
        state["auto_shutdown_enabled"] = enabled
        if not enabled:
            state["empty_since"] = None
            state["shutdown_triggered"] = False
    label = f"aktiviert ({minutes} Min.)" if enabled else "deaktiviert"
    return jsonify({"ok": True, "message": f"Auto-Shutdown {label}."})

@app.route("/console-stream")
def console_stream():
    @stream_with_context
    def generate():
        log_path = f"/tmp/{SCREEN_NAME}.log"
        byte_pos = 0
        # Zuerst die letzten 60 Zeilen des bestehenden Logs senden
        try:
            if os.path.exists(log_path):
                with open(log_path, "rb") as f:
                    content = f.read()
                byte_pos = len(content)
                lines = content.decode("utf-8", errors="replace").splitlines()
                for line in lines[-60:]:
                    yield f"data: {json.dumps(line)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps(f'[Log-Fehler: {e}]')}\n\n"
        # Neue Zeilen streamen sobald sie erscheinen
        while True:
            try:
                if os.path.exists(log_path):
                    with open(log_path, "rb") as f:
                        f.seek(byte_pos)
                        new_data = f.read()
                    if new_data:
                        byte_pos += len(new_data)
                        text = new_data.decode("utf-8", errors="replace")
                        for line in text.splitlines():
                            if line:
                                yield f"data: {json.dumps(line)}\n\n"
            except Exception:
                pass
            time.sleep(0.4)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/debug")
def debug():
    screen_out = subprocess.run(["screen", "-ls"], capture_output=True, text=True)
    port_up    = is_port_open()
    ping       = mc_ping() if port_up else None
    return jsonify({
        "screen_ls_stdout": screen_out.stdout,
        "screen_ls_stderr": screen_out.stderr,
        "screen_running":   SCREEN_NAME in screen_out.stdout,
        "port_open":        port_up,
        "ping_result":      ping,
        "last_ping_error":  _last_ping_error,
        "server_dir":       SERVER_DIR,
        "start_script":     START_SCRIPT,
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
