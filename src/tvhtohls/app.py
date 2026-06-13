import html
import threading

import uvicorn
from fastapi import FastAPI, Query, Response
from fastapi.staticfiles import StaticFiles

from .config import config, tvh_base_url
from .flags import country_name, flag_emoji
from .streams import check_status
from .tvheadend import tv_channel_epg, tvheadend_get, tvheadend_get_channel_list


# Globally accessible state populated by load_state() at startup.
channel_list = []
channel_hash = {}      # hls_uuid → TVChannel
tvh_uuid_hash = {}     # tvh_uuid → TVChannel
epg = {}               # tvh_uuid → tv_channel_epg
main_thread = None


def load_state():
    """Fetch channels + EPG from TVHeadend and populate the module-level state dicts."""
    channels, by_hls_uuid = tvheadend_get_channel_list()
    channel_list[:] = channels
    channel_hash.clear()
    channel_hash.update(by_hls_uuid)
    tvh_uuid_hash.clear()
    tvh_uuid_hash.update({ch.tvh_uuid: ch for ch in channel_list})
    print("Loaded %d TV channels" % len(channel_list))

    epg.clear()
    epg_json = tvheadend_get(tvh_base_url + "/api/epg/events/grid?limit=10000")
    for event in epg_json["entries"]:
        channel_uuid = event["channelUuid"]
        if channel_uuid in epg:
            epg[channel_uuid].add(event)
        else:
            epg[channel_uuid] = tv_channel_epg(channel_uuid, event)
    print("Loaded %d EPG channel feeds" % len(epg))


app = FastAPI()


_TIME_SCRIPT = (
    "<script>"
    "document.querySelectorAll('time[data-ts]').forEach(e=>{"
    "const d=new Date(e.dataset.ts*1000);"
    "e.textContent=d.toLocaleDateString([],{weekday:'short'})"
    "+' '+d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});"
    "});"
    "</script>"
)


def _render_epg_entry(ev):
    """Return one EPG entry as a <tr> row: time + title; subtitle/description indented one level deeper."""
    title = html.escape(ev.get("title", ""))
    subtitle = (ev.get("subtitle") or "").strip()
    desc = (ev.get("summary") or ev.get("description") or "").strip()
    content = '<time data-ts="%d"></time> <b>%s</b>' % (ev["start"], title)
    detail_parts = []
    if subtitle:
        detail_parts.append("<i>" + html.escape(subtitle) + "</i>")
    # Skip the description when it duplicates the subtitle (often happens with sparse EPG).
    if desc and desc != subtitle:
        detail_parts.append(html.escape(desc))
    if detail_parts:
        content += '<div class="epg-detail">' + "<br>".join(detail_parts) + "</div>"
    return "<tr><td>" + content + "</td></tr>"


def _render_channel_block(service, n_entries=3):
    """Render one channel as: <h3>Name link</h3> + <table> of EPG entries + 'full EPG' link."""
    name = service.name
    if service.stream:
        name = name + " 👀"
    uuid = service.hls_uuid
    out = (
        '<h3><a href="stream?uuid=' + html.escape(uuid)
        + '" rel="nofollow">' + html.escape(name) + "</a>"
    )
    if service.provider:
        out += " <small>(" + html.escape(service.provider) + ")</small>"
    if service.number and str(service.number) != "0":
        out += " <small>#" + html.escape(str(service.number)) + "</small>"
    out += "</h3>"
    entries = epg[service.tvh_uuid].get_entries(n_entries) if service.tvh_uuid in epg else []
    if entries:
        out += "<table>"
        for ev in entries:
            out += _render_epg_entry(ev)
        out += "</table>"
    out += '<p class="epg-link"><a href="epg?uuid=' + html.escape(uuid) + '">full EPG</a></p>'
    return out


def _group_by_country(channels):
    """Return list of (country_code, [channels…]) sorted by display name; '' (unknown) goes last."""
    groups = {}
    for ch in channels:
        groups.setdefault(ch.country or "", []).append(ch)
    ordered = sorted(
        (cc for cc in groups if cc),
        key=lambda c: country_name(c).lower(),
    )
    if "" in groups:
        ordered.append("")
    return [(cc, groups[cc]) for cc in ordered]


_PAGE_STYLE = (
    "<style>"
    "body{font-family:sans-serif;max-width:60em;margin:0 auto;padding:0 1em}"
    "h2{margin:1.5em 0 0.3em 0;padding-top:0.5em;border-top:1px solid #ccc}"
    "h3{margin:0.8em 0 0.2em 1.5em;font-size:1.05em}"
    "table,p.epg-link{margin:0.2em 0 0.2em 3em}"
    "td{padding:0.1em 0.4em;vertical-align:top}"
    "td time{font-family:monospace;color:#555}"
    ".epg-detail{margin-left:2em;color:#444}"
    "nav.toc{background:#f3f3f3;padding:0.6em 0.8em;border-radius:4px;line-height:1.8em}"
    "nav.toc a{display:inline-block;margin-right:1em;text-decoration:none}"
    "nav.toc a:hover{text-decoration:underline}"
    "p.lists{margin-bottom:0.3em}"
    "</style>"
)


@app.get("/")
async def read_root(list_name: str = Query("", alias="list")):
    if list_name and list_name in config["top_channel_lists"]:
        top_uuids = config["top_channel_lists"][list_name]
        active = list_name
    else:
        top_uuids = config["top_channels"]
        active = ""

    pinned = [tvh_uuid_hash[u] for u in top_uuids if u in tvh_uuid_hash]
    pinned_set = {ch.tvh_uuid for ch in pinned}
    rest = [c for c in channel_list if c.tvh_uuid not in pinned_set]
    rest_sorted = sorted(rest, key=lambda x: x.name.lower())
    grouped = _group_by_country(rest_sorted)

    # Build TOC entries: (anchor_id, label, count)
    toc = []
    if pinned:
        toc.append(("pinned", "📌 Pinned", len(pinned)))
    for cc, members in grouped:
        anchor = "cc-" + (cc or "other")
        if cc:
            label = flag_emoji(cc) + " " + country_name(cc)
        else:
            label = "Other"
        toc.append((anchor, label, len(members)))

    data = "<html><head><title>Channels</title>"
    data += _PAGE_STYLE
    data += "</head><body>"
    data += "<h1>List of TV channels"
    if active:
        data += " &mdash; " + html.escape(active)
    data += "</h1>"
    if config["top_channel_lists"]:
        data += '<p class="lists">Lists: <a href="/">default</a>'
        for n in sorted(config["top_channel_lists"]):
            data += ' | <a href="?list=' + html.escape(n) + '">' + html.escape(n) + "</a>"
        data += "</p>"
    # Country index (jumps to sections below)
    data += '<nav class="toc">'
    for anchor, label, count in toc:
        data += (
            '<a href="#' + anchor + '">'
            + label + " <small>(" + str(count) + ")</small></a>"
        )
    data += "</nav>"

    if pinned:
        data += '<h2 id="pinned">📌 Pinned</h2>'
        for service in pinned:
            data += _render_channel_block(service)

    for cc, members in grouped:
        anchor = "cc-" + (cc or "other")
        if cc:
            heading = flag_emoji(cc) + " " + html.escape(country_name(cc))
        else:
            heading = "Other"
        data += '<h2 id="' + anchor + '">' + heading + "</h2>"
        for service in members:
            data += _render_channel_block(service)

    data += _TIME_SCRIPT
    data += "</body></html>"
    return Response(content=data, media_type="text/html;charset=utf-8")


@app.get("/epg")
async def read_epg(uuid: str = ""):
    if uuid not in channel_hash:
        return Response(content="NIX", media_type="text/plain;charset=utf-8")
    channel = channel_hash[uuid]
    tvh_uuid = channel.tvh_uuid
    if tvh_uuid not in epg:
        try:
            epg_json = tvheadend_get(
                tvh_base_url + "/api/epg/events/grid?limit=50&channel=" + tvh_uuid
            )
            for event in epg_json["entries"]:
                if event["channelUuid"] != tvh_uuid:
                    continue
                if tvh_uuid in epg:
                    epg[tvh_uuid].add(event)
                else:
                    epg[tvh_uuid] = tv_channel_epg(tvh_uuid, event)
        except Exception:
            pass
    entries = epg[tvh_uuid].get_entries(50) if tvh_uuid in epg else []
    title = html.escape(channel.name) + ((" " + channel.flag) if channel.flag else "")
    data = (
        "<html><head><title>EPG: " + title + "</title>"
        + _PAGE_STYLE +
        "</head>"
        '<body><a href="/">‹ channels</a>'
        "<h1>EPG: " + title + "</h1>"
    )
    if entries:
        data += "<table>"
        for ev in entries:
            data += _render_epg_entry(ev)
        data += "</table>"
    data += _TIME_SCRIPT + "</body></html>"
    return Response(content=data, media_type="text/html;charset=utf-8")


@app.get("/stream.m3u8")
async def read_m3u8(uuid: str = "", stream_id: int = -1):
    if uuid not in channel_hash:
        return Response(content="NIX", media_type="text/plain;charset=utf-8")
    channel = channel_hash[uuid]
    res = channel.start_stream()
    if not res:
        return Response(content="NIX", media_type="text/plain;charset=utf-8")

    suffix = ""
    if stream_id >= 0:
        suffix = "+" + str(stream_id)
    try:
        with open(channel.m3u8_file + suffix, "r") as m3u8:
            lines = m3u8.readlines()
    except OSError:
        return Response(content="NIX", media_type="text/plain;charset=utf-8")

    data = ""
    for line in lines:
        if line.find(".ts") >= 0:
            data = data + config["hls_http_path"] + line
            continue
        if line.find(".m3u8+") >= 0:
            data = data + "stream.m3u8?uuid=" + line.replace(".m3u8+", "&stream_id=")
            continue
        data = data + line

    return Response(content=data, media_type="text/plain;charset=utf-8")


def player_page(uri: str = "", name: str = "", channel_uuid: str = ""):
    data = "<html><head><title>%s</title></head>" % html.escape(name)
    data += "<body>"
    data += '<script src="' + config["static_http_path"] + 'hls.js"></script>'
    data += '''
    <center>
      <h1>%s</h1>
      <video width="100%%" id="video" controls></video>
      <div><small>Currently: <span id="variant-info">…</span></small></div>
    </center>

    <script>
      var video = document.getElementById('video');
      function fmtRate(bps) { return (bps/1000000).toFixed(1) + ' Mbps'; }
      function showLevel(lvl) {
        document.getElementById('variant-info').textContent =
          lvl.height + 'p · ' + fmtRate(lvl.bitrate);
      }
      if (Hls.isSupported()) {
        var hls = new Hls({
          debug: true,
        });
        hls.loadSource('%s');
        hls.attachMedia(video);
        hls.on(Hls.Events.MEDIA_ATTACHED, function () {
          video.muted=true;
          setTimeout(startPlayer, 5000)
        });
        hls.on(Hls.Events.LEVEL_SWITCHED, function (e, data) {
          showLevel(hls.levels[data.level]);
        });
      }
      else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = '%s';
        video.addEventListener('canplay', function () {
          video.muted=true;
          setTimeout(startPlayer, 5000)
        });
        // Native HLS: only resolution is available on the <video> element.
        video.addEventListener('resize', function () {
          document.getElementById('variant-info').textContent =
            video.videoHeight + 'p';
        });
      }

      function startPlayer() {
        video.play()
      }
    </script>
''' % (html.escape(name), uri, uri)
    data += '<br><a href="%s">URL for use with VLC</a>' % uri
    if channel_uuid:
        data += ' &middot; <a href="epg?uuid=%s">EPG</a>' % html.escape(channel_uuid)
        data += ' &middot; <a href="/">‹ all channels</a>'
    data += "</body>"

    return Response(content=data, media_type="text/html;charset=utf-8")


@app.get("/stream")
async def read_stream(uuid: str = ""):
    if uuid not in channel_hash:
        return Response(content="NIX", media_type="text/plain;charset=utf-8")
    channel = channel_hash[uuid]
    res = channel.start_stream()
    if res:
        return player_page(res, channel.name, channel.hls_uuid)
    data = (
        '<html><head><title>Bitte warten</title>'
        '<meta http-equiv="refresh" content="1"></head>'
        '<body>'
        'Please wait while the stream is starting. This can take 30 seconds or more. '
        'This page will meanwhile reload.'
        '</body></html>'
    )
    return Response(content=data, media_type="text/html;charset=utf-8")


@app.on_event("startup")
def startup_event():
    threading.Thread(
        target=check_status,
        args=(channel_list, epg, main_thread),
        daemon=True,
    ).start()


app.mount(
    "/" + config["static_http_path"],
    StaticFiles(directory=config["static_local_path"]),
    name="static",
)
app.mount(
    "/" + config["hls_http_path"],
    StaticFiles(directory=config["hls_local_path"]),
    name="hls",
)


def main():
    global main_thread
    main_thread = threading.current_thread()
    load_state()
    uvicorn.run(app, port=8888, host="0.0.0.0", log_level="info")
