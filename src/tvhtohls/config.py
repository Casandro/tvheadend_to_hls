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


tvh_base_url = (
    "http://" + config["tvheadend_ip"] + ":" + config["tvheadend_port"] + "/"
)
tvh_base_url_auth = (
    "http://"
    + config["tvheadend_user"]
    + ":"
    + config["tvheadend_pass"]
    + "@"
    + config["tvheadend_ip"]
    + ":"
    + config["tvheadend_port"]
    + "/"
)
