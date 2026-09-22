#!/usr/bin/env bash
set -euo pipefail
# Run as root after copying the source to /opt/summon/app.
APP=/opt/summon/app
id summon >/dev/null 2>&1 || useradd --system --home /var/lib/summon --shell /usr/sbin/nologin summon
install -d -m 750 -o summon -g summon /var/lib/summon
install -d -m 755 /var/www/summon-acme
install -d -m 750 -o root -g summon /etc/summon
python3 -m venv /opt/summon/venv
/opt/summon/venv/bin/pip install --disable-pip-version-check --index-url https://pypi.org/simple --timeout 15 --retries 1 -r "$APP/hub/requirements.txt"
if [ ! -f /etc/summon/config.json ]; then
  python3 - <<'PY'
import json,secrets,os
config={'origin':'https://summon.entermodetwo.com','mode':'SIMULATED','secure_cookie':True,
        'database':'/var/lib/summon/hub.db','port':8840,'invite':secrets.token_urlsafe(32),
        'operator_codes':{secrets.token_urlsafe(18):'operator_team'},
        'gateway_tokens':{s:secrets.token_urlsafe(32) for s in ('shell_a','shell_b')},
        'shell_labels':{'shell_a':'模拟设备 A','shell_b':'模拟设备 B'},
        'demo_credentials':'/var/lib/summon/demo-credentials.json'}
with open('/etc/summon/config.json','x') as f: json.dump(config,f)
os.chmod('/etc/summon/config.json',0o640)
PY
  chown root:summon /etc/summon/config.json
fi
cat > /etc/systemd/system/summon-hub.service <<'UNIT'
[Unit]
Description=SUMMON Hub
After=network-online.target
[Service]
User=summon
Group=summon
WorkingDirectory=/opt/summon/app
Environment=SUMMON_CONFIG=/etc/summon/config.json
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/summon/venv/bin/python -m hub.app
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/summon
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/summon-demo.service <<'UNIT'
[Unit]
Description=SUMMON explicitly simulated clients
After=summon-hub.service
Requires=summon-hub.service
[Service]
User=summon
Group=summon
WorkingDirectory=/opt/summon/app
Environment=SUMMON_CONFIG=/etc/summon/config.json
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/summon/venv/bin/python -m hub.simulator
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/summon
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now summon-hub.service
systemctl enable --now summon-demo.service
