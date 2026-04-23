# ATM10 Control Panel – Setup Anleitung

Webbasiertes Control Panel für einen **All The Mods 10** Minecraft Server auf Proxmox LXC.  
Features: Start / Stop, Spieler-Anzeige, Auto-Shutdown, Live-Konsole.

---

## Voraussetzungen

| Was | Wo |
|---|---|
| Proxmox LXC Container | Ubuntu 22.04 oder Debian 12 empfohlen |
| ATM10 Server | bereits eingerichtet und mind. einmal manuell gestartet |
| `start.sh` vorhanden | im Serverordner, ausführbar |

---

## Schritt 1 – Pfade herausfinden

Verbinde dich mit dem Container (entweder über Proxmox Shell oder SSH) und führe folgendes aus:

```bash
# Startscript finden
find / -name "start.sh" 2>/dev/null

# Ausgabe Beispiel:
# /opt/minecraft/start.sh   <-- das ist dein SERVER_DIR + START_SCRIPT
```

Merke dir:
- **SERVER_DIR** → der Ordner, in dem `start.sh` liegt (z. B. `/opt/minecraft`)
- **START_SCRIPT** → immer `./start.sh` (relativ)

---

## Schritt 2 – Startscript prüfen

```bash
# Ausführbar machen (falls nötig)
chmod +x /opt/minecraft/start.sh   # Pfad anpassen!

# Kurz testen ob es syntaktisch korrekt ist
bash -n /opt/minecraft/start.sh && echo "OK"
```

---

## Schritt 3 – Abhängigkeiten installieren

```bash
apt update && apt install -y python3 python3-pip screen
pip3 install flask
```

> **Hinweis:** `screen` ist wichtig – der Minecraft Server läuft darin als Hintergrundprozess.

---

## Schritt 4 – App-Verzeichnis anlegen & Script kopieren

```bash
mkdir -p /opt/mcweb
cp app.py /opt/mcweb/app.py
```

---

## Schritt 5 – Script konfigurieren

Öffne die Datei mit einem Texteditor:

```bash
nano /opt/mcweb/app.py
```

Passe die drei Variablen ganz oben an:

```python
SERVER_DIR   = "/opt/minecraft"   # <-- Pfad zu deinem Serverordner
START_SCRIPT = "./start.sh"       # <-- meistens so lassen
SCREEN_NAME  = "mcserver"         # <-- frei wählbar, Name der Screen-Session
```

Speichern mit `Strg+O`, dann `Enter`, beenden mit `Strg+X`.

---

## Schritt 6 – Einmalig testen

Starte das Script manuell um sicherzustellen, dass alles funktioniert:

```bash
python3 /opt/mcweb/app.py
```

Erwartete Ausgabe:
```
 * Running on http://0.0.0.0:8080
```

Öffne jetzt im Browser: `http://<Container-IP>:8080`

> Die Container-IP findest du in Proxmox unter dem Container → Summary → IP Address,  
> oder im Container selbst mit: `ip a | grep inet`

Beende den Testlauf mit `Strg+C` wenn alles funktioniert.

---

## Schritt 7 – Autostart mit systemd einrichten

Damit das Panel nach jedem Neustart automatisch startet:

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
# Systemd neu laden
systemctl daemon-reload

# Service aktivieren und sofort starten
systemctl enable --now mcweb

# Status prüfen
systemctl status mcweb
```

Erwartete Ausgabe (Auszug):
```
● mcweb.service - ATM10 Minecraft Web Panel
     Active: active (running) since ...
```

---

## Schritt 8 – Firewall (falls aktiv)

Falls im Container eine Firewall läuft, Port 8080 freigeben:

```bash
# UFW (Ubuntu)
ufw allow 8080/tcp

# iptables (Debian)
iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
```

---

## Weboberfläche benutzen

Öffne im Browser:

```
http://<Container-IP>:8080
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
```

---

## Fehlersuche

**Problem:** Webseite lädt nicht  
→ Prüfen ob der Service läuft: `systemctl status mcweb`  
→ Prüfen ob Port belegt ist: `ss -tlnp | grep 8080`

**Problem:** Start-Button drücken, aber Server startet nicht  
→ `SERVER_DIR` und `START_SCRIPT` in `app.py` prüfen  
→ Manuell testen: `cd /opt/minecraft && bash start.sh`  
→ Logs prüfen: `cat /tmp/mcserver.log`

**Problem:** Spieleranzahl wird nicht angezeigt (bleibt auf `–`)  
→ Normal während des Startvorgangs (Forge/ATM10 braucht Zeit)  
→ Sicherstellen dass Port 25565 im Container offen ist: `ss -tlnp | grep 25565`  
→ In `server.properties` prüfen: `enable-status=true` (Standard: an)

**Problem:** Auto-Shutdown funktioniert nicht  
→ Prüfen ob Auto-Shutdown auf der Webseite aktiviert ist  
→ Timeout-Wert kontrollieren  
→ Panel-Logs prüfen: `journalctl -u mcweb -f`

---

## Dateistruktur

```
/opt/mcweb/
└── app.py           ← Das Web Panel

/opt/minecraft/      ← Dein Serverordner (Beispiel)
├── start.sh
├── server.jar / forge-*.jar
├── server.properties
└── ...

/tmp/mcserver.log    ← Screen-Log (wird beim Start neu erstellt)
```
