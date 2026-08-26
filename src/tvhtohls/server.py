"""Per-server TVHeadend HTTP client.

One `TVHServer` per configured TVHeadend instance, each carrying its own
credentials — the app talks to several servers at once, so authentication can no
longer come from a module-level global.
"""
from urllib.parse import quote

import requests
from requests.auth import HTTPDigestAuth


# requests has no default timeout: without one, a slow/unresponsive TVHeadend
# hangs this call forever. Since check_status's background loop is single
# threaded, a hung EPG refetch for any one channel freezes the idle-kill sweep
# for every channel, so ffmpeg never gets stopped even when nothing accessed
# its playlist. The connect timeout is separate and much shorter: when a server
# is *down* (as opposed to slow) we want to find out immediately, because
# startup fetches several endpoints per server.
CONNECT_TIMEOUT = 3
READ_TIMEOUT = 10
# The startup grids are big (a 1700-channel /api/channel/grid takes seconds, and
# more when the server is busy scanning), and a slow one there only delays
# startup — it can't stall the background loop. Give them more room.
STARTUP_READ_TIMEOUT = 60


class TVHError(Exception):
    """A TVHeadend request failed: transport error, non-200, or unparsable body."""


class TVHServer:
    """A single TVHeadend instance: base URL, credentials, pooled session."""

    def __init__(self, name, ip, port="9981", user=None, password=None, order=0):
        self.name = name
        self.ip = ip
        self.port = str(port)
        self.user = user
        self.password = password
        # Position in the configured server list == failover priority.
        self.order = order
        self.base_url = "http://%s:%s/" % (ip, self.port)
        self.auth = HTTPDigestAuth(user, password)
        self.session = requests.Session()

    @classmethod
    def from_config(cls, entry, order=0):
        return cls(
            entry["name"], entry["ip"], entry.get("port", "9981"),
            entry.get("user"), entry.get("pass"), order=order,
        )

    def __repr__(self):
        return "<TVHServer %s %s:%s>" % (self.name, self.ip, self.port)

    def get(self, path, read_timeout=READ_TIMEOUT):
        """GET a TVHeadend JSON endpoint (`path` is relative to the server root)."""
        url = self.base_url + path.lstrip("/")
        try:
            req = self.session.get(
                url, auth=self.auth, timeout=(CONNECT_TIMEOUT, read_timeout),
            )
        except requests.RequestException as e:
            raise TVHError("%s: %s: %s" % (self.name, path, e))
        req.encoding = "UTF-8"
        if req.status_code != 200:
            raise TVHError("%s: %s returned HTTP %s" % (self.name, path, req.status_code))
        try:
            return req.json()
        except ValueError as e:
            raise TVHError("%s: %s returned no JSON (%s)" % (self.name, path, e))

    def stream_url(self, tvh_uuid):
        """ffmpeg input URL with inline credentials.

        The credentials are percent-encoded: a password containing '@', ':' or '/'
        would otherwise cut the URL in the wrong place.
        """
        return "http://%s:%s@%s:%s/stream/channel/%s" % (
            quote(self.user or "", safe=""),
            quote(self.password or "", safe=""),
            self.ip, self.port, tvh_uuid,
        )


def build_servers(entries):
    """Turn config["servers"] into TVHServer objects, preserving configured order."""
    return [TVHServer.from_config(e, order=i) for i, e in enumerate(entries)]
