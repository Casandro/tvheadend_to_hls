#!/usr/bin/env python3
"""Print all TV channels as UUID<TAB>number<TAB>name.

Reads tvheadend_user / tvheadend_pass (required) and tvheadend_ip / tvheadend_port
(optional, default 192.168.5.5:9981) from the environment, same as the main app.

Useful for picking UUIDs to put into a top_channels[_name] env var.
"""
import os
import signal
import sys
import requests
from requests.auth import HTTPDigestAuth

signal.signal(signal.SIGPIPE, signal.SIG_DFL)

user = os.environ.get("tvheadend_user")
pwd = os.environ.get("tvheadend_pass")
ip = os.environ.get("tvheadend_ip", "192.168.5.5")
port = os.environ.get("tvheadend_port", "9981")

if not user or not pwd:
    print("set tvheadend_user and tvheadend_pass in the environment", file=sys.stderr)
    sys.exit(1)

r = requests.get(
    f"http://{ip}:{port}/api/channel/grid?limit=99999",
    auth=HTTPDigestAuth(user, pwd),
)
r.raise_for_status()

chans = [c for c in r.json()["entries"] if c.get("name") != "{name-not-set}"]
for c in sorted(chans, key=lambda x: (x.get("number") or 0, x.get("name", "").lower())):
    print(f"{c['uuid']}\t{c.get('number', '')}\t{c.get('name', '')}")
