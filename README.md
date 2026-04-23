# ATM10 Control Panel – Setup Anleitung

Webbasiertes Control Panel für einen **All The Mods 10** Minecraft Server auf Proxmox KVM/QEMU VM.  
Features: Start / Stop, Spieler-Anzeige, Auto-Shutdown, Live-Konsole.

---

## Voraussetzungen

| Was | Wo |
|---|---|
| Proxmox VM (KVM/QEMU) | Ubuntu 22.04 oder Debian 12 empfohlen |
| ATM10 Server | bereits eingerichtet und mind. einmal manuell gestartet |
| `start.sh` vorhanden | im Serverordner, ausführbar |

> `app.py` läuft **direkt in der VM**, nicht auf dem Proxmox-Host.

---

## Schritt 1 – Mit der VM verbinden

**Option A: Proxmox Web-Konsole**  
Proxmox öffnen → VM auswählen → **Console** klicken → einloggen

**Option B: SSH (empfohlen)**  
```bash
ssh benutzer@<VM-IP>
```
Die VM-IP findest du in Proxmox unter der VM → **Summary** → IP Address.  
Falls dort keine IP steht, in der Proxmox-Konsole der VM eingeben:
```bash
ip a | grep "inet " | grep -v 127
```

---

## Schritt 2 – Pfade herausfinden

In der VM-Konsole oder SSH-Session ausführen:

```bash
# Startscript finden
find / -name "start.sh" 2>/dev/null

# Ausgabe Beispiel:
# /opt/minecraft/start.sh   <-- das ist dein SERVER_DIR
```

Merke dir:
- **SERVER_DIR** → der Ordner, in dem `start.sh` liegt (z. B. `/opt/minecraft`)
- **START_SCRIPT** → immer `./start.sh` (relativ zum SERVER_DIR)

---

## Schritt 3 – Startscript prüfen

```bash
# Ausführbar machen (falls nötig)
chmod +x /opt/minecraft/start.sh   # Pfad anpassen!

# Kurz testen ob es syntaktisch korrekt ist
bash -n /opt/minecraft/start.sh && echo "OK"
```

---

## Schritt 4 – Abhängigkeiten installieren

```bash
apt update && apt install -y python3 python3-pip screen
pip3 install flask
```

> **Hinweis:** `screen` ist wichtig – der Minecraft Server läuft darin als Hintergrundprozess, auch wenn die SSH-Verbindung getrennt wird.

---

## Schritt 5 – App-Verzeichnis anlegen & Script übertragen

**Option A: Direkt in der VM erstellen**
```bash
mkdir -p /opt/mcweb
nano /opt/mcweb/app.py
# --> Inhalt von app.py vollständig einfügen, dann Strg+O, Enter, Strg+X
```

**Option B: Per SCP von deinem PC hochladen**
```bash
# Auf deinem PC (nicht in der VM) ausführen:
scp app.py benutzer@<VM-IP>:/opt/mcweb/app.py
```

---

## Schritt 6 – Script konfigurieren

```bash
nano /opt/mcweb/app.py
```

Passe die Variablen ganz oben an:

```python
SERVER_DIR   = "/opt/minecraft"   # <-- Pfad zu deinem Serverordner in der VM
START_SCRIPT = "./start.sh"       # <-- meistens so lassen
SCREEN_NAME  = "mcserver"         # <-- frei wählbar, Name der Screen-Session
```

Speichern mit `Strg+O`, dann `Enter`, beenden mit `Strg+X`.

---

## Schritt 7 – Einmalig testen

```bash
python3 /opt/mcweb/app.py
```

Erwartete Ausgabe:
```
 * Running on http://0.0.0.0:8080
```

Öffne im Browser: `http://<VM-IP>:8080`

Beende den Testlauf mit `Strg+C` wenn alles funktioniert.

---

## Schritt 8 – Autostart mit systemd einrichten

```bash
nano /etc/systemd/system/mcweb.service
```

Folgenden Inhalt einfügen:

```ini
[Unit]
Description=ATM10 Minecraft Web Panel
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/mcweb/app.py
WorkingDirectory=/opt/mcweb
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

Speichern (`Strg+O` → `Enter` → `Strg+X`), dann aktivieren:

```bash
systemctl daemon-reload
systemctl enable --now mcweb
systemctl status mcweb
```

Erwartete Ausgabe (Auszug):
```
● mcweb.service - ATM10 Minecraft Web Panel
     Active: active (running) since ...
```

---

## Schritt 9 – Firewall (falls aktiv)

```bash
# UFW (Ubuntu)
ufw allow 8080/tcp

# iptables (Debian)
iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
```

---

## Weboberfläche benutzen

```
http://<VM-IP>:8080
```

| Element | Funktion |
|---|---|
| **▶ Starten** | Startet `start.sh` in einer Screen-Session |
| **■ Stoppen** | Sendet `stop` in die Minecraft-Konsole (sauberes Shutdown) |
| **↻ Refresh** | Sofort aktuellen Status abrufen |
| **Auto-Shutdown Toggle** | Auto-Shutdown ein- oder ausschalten |
| **Timeout (Min.)** | Wie lange kein Spieler online sein darf, bevor gestoppt wird |
| **Countdown** | Zeigt verbleibende Zeit bis zum Auto-Shutdown |
| **Konsole** | Letzte 25 Zeilen des Server-Logs |

> ATM10 braucht nach dem Starten **2–4 Minuten** bis der Server vollständig geladen ist.  
> In dieser Zeit zeigt das Panel „Startet..." an.

---

## Nützliche Befehle

```bash
# Panel-Logs anzeigen (für Fehlersuche)
journalctl -u mcweb -f

# Panel neu starten
systemctl restart mcweb

# Screen-Session des Minecraft Servers manuell öffnen
screen -r mcserver

# Screen-Session verlassen (ohne Server zu stoppen!)
# Tastenkombination: Strg+A, dann D

# Alle laufenden Screen-Sessions anzeigen
screen -list
```

---

## Fehlersuche

**Problem:** Webseite lädt nicht  
→ Prüfen ob der Service läuft: `systemctl status mcweb`  
→ Prüfen ob Port belegt ist: `ss -tlnp | grep 8080`  
→ VM-IP korrekt? `ip a | grep "inet "`

**Problem:** Start-Button drücken, aber Server startet nicht  
→ `SERVER_DIR` und `START_SCRIPT` in `app.py` prüfen  
→ Manuell testen: `cd /opt/minecraft && bash start.sh`  
→ Logs prüfen: `cat /tmp/mcserver.log`

**Problem:** Spieleranzahl wird nicht angezeigt (bleibt auf `–`)  
→ Normal während des Startvorgangs (Forge/ATM10 braucht Zeit)  
→ Sicherstellen dass Port 25565 in der VM offen ist: `ss -tlnp | grep 25565`  
→ In `server.properties` prüfen: `enable-status=true` (Standard: an)

**Problem:** Auto-Shutdown funktioniert nicht  
→ Prüfen ob Auto-Shutdown auf der Webseite aktiviert ist  
→ Timeout-Wert kontrollieren  
→ Panel-Logs prüfen: `journalctl -u mcweb -f`

**Problem:** SSH-Verbindung trennt, Minecraft Server stoppt  
→ Sicherstellen dass der Server per Panel gestartet wurde (läuft in `screen`)  
→ Nie direkt `bash start.sh` im Terminal ausführen – immer über das Panel oder manuell mit `screen`

---

## Dateistruktur

```
/opt/mcweb/           ← In der VM
└── app.py

/opt/minecraft/       ← Dein Serverordner (Beispiel, in der VM)
├── start.sh
├── server.jar / forge-*.jar
├── server.properties
└── ...

/tmp/mcserver.log     ← Screen-Log (wird beim Start neu erstellt)
```
