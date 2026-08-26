#!/usr/bin/env python3
"""Print all TV channels as UUID<TAB>number<TAB>name (prefixed by server name
when more than one TVHeadend is configured).

Reads the same environment as the main app: either `tvheadend_servers` (a JSON
array of {name, ip, port, user, pass} objects) or the flat tvheadend_user /
tvheadend_pass / tvheadend_ip / tvheadend_port variables.

Useful for picking UUIDs to put into a top_channels[_name] env var. UUIDs are
per-server, but the app resolves a UUID from any configured server, so a plain
UUID is enough; the "server:uuid" form printed with multiple servers also works.
"""
import json
import os
import signal
import sys

import requests
from requests.auth import HTTPDigestAuth

signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def servers_from_env():
    raw = os.environ.get("tvheadend_servers", "").strip()
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError as e:
            print("tvheadend_servers: not valid JSON (%s)" % e, file=sys.stderr)
            sys.exit(1)
        out = []
        for e in entries:
            ip = e.get("ip") or e.get("host")
            out.append({
                "name": e.get("name") or ip,
                "ip": ip,
                "port": str(e.get("port") or "9981"),
                "user": e.get("user"),
                "pass": e.get("pass") if e.get("pass") is not None else e.get("password"),
            })
        return out
    user = os.environ.get("tvheadend_user")
    pwd = os.environ.get("tvheadend_pass")
    if not user or not pwd:
        print("set tvheadend_servers, or tvheadend_user and tvheadend_pass, "
              "in the environment", file=sys.stderr)
        sys.exit(1)
    ip = os.environ.get("tvheadend_ip", "192.168.5.5")
    return [{"name": ip, "ip": ip, "port": os.environ.get("tvheadend_port", "9981"),
             "user": user, "pass": pwd}]


servers = servers_from_env()
show_server = len(servers) > 1

for s in servers:
    try:
        r = requests.get(
            "http://%s:%s/api/channel/grid?limit=99999" % (s["ip"], s["port"]),
            auth=HTTPDigestAuth(s["user"], s["pass"]),
            timeout=(3, 15),
        )
        r.raise_for_status()
    except requests.RequestException as e:
        print("%s: %s" % (s["name"], e), file=sys.stderr)
        continue
    chans = [c for c in r.json()["entries"] if c.get("name") != "{name-not-set}"]
    prefix = ("%s\t" % s["name"]) if show_server else ""
    for c in sorted(chans, key=lambda x: (x.get("number") or 0, x.get("name", "").lower())):
        print(f"{prefix}{c['uuid']}\t{c.get('number', '')}\t{c.get('name', '')}")
