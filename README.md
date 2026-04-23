# mcwebserber
# SERVER_DIR und START_SCRIPT oben im Script anpassen, dann:
mkdir -p /opt/mcweb
cp app.py /opt/mcweb/app.py

# Systemd Service (wie vorher)
systemctl enable --now mcweb
