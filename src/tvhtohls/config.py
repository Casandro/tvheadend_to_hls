import json
import os
import pathlib


config = {}
config["tvheadend_ip"] = "192.168.5.5"
config["tvheadend_port"] = "9981"
config["tvheadend_user"] = "user"
config["tvheadend_pass"] = "pass"
config["local_port"] = 8888
config["hls_local_path"] = "/tmp/tvhtohls/hls"
config["hls_http_path"] = "hls/"
config["static_local_path"] = pathlib.Path(__file__).parent / "static"
config["static_http_path"] = "static/"
config["sort"] = "name"  # or "number"
config["segment_len"] = 5

for setting in list(config.keys()):
    if setting in os.environ:
        config[setting] = os.environ[setting]

config["top_channels"] = [
    u.strip() for u in os.environ.get("top_channels", "").split(",") if u.strip()
]
config["top_channel_lists"] = {}
for _k, _v in os.environ.items():
    if _k.startswith("top_channels_") and _v.strip():
        _name = _k[len("top_channels_"):]
        _uuids = [u.strip() for u in _v.split(",") if u.strip()]
        if _uuids:
            config["top_channel_lists"][_name] = _uuids
config["vaapi_device"] = os.environ.get("vaapi_device", "/dev/dri/renderD128")


def _parse_bitrate(s):
    """Accept '100k', '3M', or plain bits/second; return int bps."""
    s = str(s).strip().upper()
    if not s:
        return 0
    if s.endswith("K"):
        return int(float(s[:-1]) * 1000)
    if s.endswith("M"):
        return int(float(s[:-1]) * 1_000_000)
    return int(s)


# ABR ladder: geometric series of `num_streams` transcoded variants between
# `min_bitrate` (lowest) and `max_bitrate` (highest). Resolution per variant
# is derived from the bitrate and capped at the source dimensions so we never
# upscale. The original-quality stream-copy variant is always added on top.
config["min_bitrate"] = _parse_bitrate(os.environ.get("min_bitrate", "100k"))
config["max_bitrate"] = _parse_bitrate(os.environ.get("max_bitrate", "3M"))
config["num_streams"] = max(1, int(os.environ.get("num_streams", "4")))

# Radio / audio-only support. Radio channels are hidden unless include_radio is
# enabled. Audio-only services (radio, or any service with audio but no video)
# get a synthesized low-bandwidth black video carrying the channel name so the
# normal HLS pipeline and the <video> player keep working unchanged.
config["include_radio"] = os.environ.get("include_radio", "").lower() in (
    "1", "true", "yes", "on"
)
# Diagnostic: print the multi-server channel merge report at startup and exit
# before uvicorn (and before any ffmpeg) is started.
config["merge_report_only"] = os.environ.get("merge_report_only", "").lower() in (
    "1", "true", "yes", "on"
)
config["dummy_video_size"] = os.environ.get("dummy_video_size", "320x180")
config["dummy_video_fps"] = int(os.environ.get("dummy_video_fps", "10"))
config["dummy_video_bitrate"] = _parse_bitrate(os.environ.get("dummy_video_bitrate", "64k"))
config["dummy_video_font"] = os.environ.get(
    "dummy_video_font", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
)


def _detect_vaapi(device):
    """True if the VAAPI render node exists and we can open it for reading."""
    try:
        with open(device, "rb"):
            return True
    except OSError:
        return False


# hwaccel selection — three-way:
#   unset / "auto"  → probe vaapi_device; use VAAPI if present, else CPU
#   "none"/"cpu"/"off" → force CPU (even on a GPU host)
#   anything else (e.g. "vaapi") → trust the user verbatim, no probe
_requested = os.environ.get("hwaccel", "").lower().strip()
if _requested in ("none", "cpu", "off"):
    config["hwaccel"] = ""
    print("hwaccel: disabled via env")
elif _requested in ("", "auto"):
    if _detect_vaapi(config["vaapi_device"]):
        config["hwaccel"] = "vaapi"
        print("hwaccel: auto-detected VAAPI (%s)" % config["vaapi_device"])
    else:
        config["hwaccel"] = ""
        print("hwaccel: no GPU detected at %s, using CPU" % config["vaapi_device"])
else:
    config["hwaccel"] = _requested
    print("hwaccel: forced via env = %r" % _requested)

if not os.path.isdir(config["hls_local_path"]):
    print("hls_local_path '%s' is not a directory" % config["hls_local_path"])
    raise SystemExit(1)


def _server_entry(raw, index):
    """Normalize one `tvheadend_servers` entry. Exits with a clear message if unusable."""
    if not isinstance(raw, dict):
        print("tvheadend_servers[%d]: expected a JSON object, got %r" % (index, raw))
        raise SystemExit(1)
    ip = str(raw.get("ip") or raw.get("host") or "").strip()
    if not ip:
        print('tvheadend_servers[%d]: missing "ip"' % index)
        raise SystemExit(1)
    user = raw.get("user")
    password = raw.get("pass") if raw.get("pass") is not None else raw.get("password")
    if not user or password is None:
        print('tvheadend_servers[%d] (%s): missing "user" and/or "pass"' % (index, ip))
        raise SystemExit(1)
    return {
        "name": str(raw.get("name") or ip).strip() or ip,
        "ip": ip,
        "port": str(raw.get("port") or "9981").strip(),
        "user": str(user),
        "pass": str(password),
    }


def _unique_names(servers):
    """Server names are log labels and `server:uuid` keys — make them unique."""
    seen = {}
    for s in servers:
        n = seen.get(s["name"], 0) + 1
        seen[s["name"]] = n
        if n > 1:
            s["name"] = "%s-%d" % (s["name"], n)
    return servers


# Multi-server support is opt-in: `tvheadend_servers` (a JSON array) is used only
# when it is set and non-empty. Otherwise the legacy flat tvheadend_ip/port/user/pass
# variables define the one and only server, exactly as before. The two formats are
# never mixed — a malformed `tvheadend_servers` is a hard error rather than a silent
# fallback to the flat vars, which would quietly stream from the wrong server.
_raw_servers = os.environ.get("tvheadend_servers", "").strip()
if _raw_servers:
    try:
        _parsed = json.loads(_raw_servers)
    except ValueError as _e:
        print("tvheadend_servers: not valid JSON (%s)" % _e)
        print("  expected e.g. [{\"name\":\"attic\",\"ip\":\"10.0.0.5\",\"user\":\"u\",\"pass\":\"p\"}]")
        raise SystemExit(1)
    if not isinstance(_parsed, list) or not _parsed:
        print("tvheadend_servers: expected a non-empty JSON array of objects")
        raise SystemExit(1)
    config["servers"] = _unique_names(
        [_server_entry(s, i) for i, s in enumerate(_parsed)]
    )
else:
    config["servers"] = [{
        "name": str(config["tvheadend_ip"]),
        "ip": str(config["tvheadend_ip"]),
        "port": str(config["tvheadend_port"]),
        "user": str(config["tvheadend_user"]),
        "pass": str(config["tvheadend_pass"]),
    }]

# Log the servers we ended up with (never the passwords) so a misquoted .env shows up.
print("tvheadend: %d server(s): %s" % (
    len(config["servers"]),
    ", ".join("%s(%s:%s)" % (s["name"], s["ip"], s["port"]) for s in config["servers"]),
))
