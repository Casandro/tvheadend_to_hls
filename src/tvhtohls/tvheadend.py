"""TVHeadend API client + the channel/EPG domain objects."""
import time

import requests
from requests.auth import HTTPDigestAuth

from .config import config, tvh_base_url
from .flags import guess_country
from .streams import TVChannel


def tvheadend_get(url):
    """GET a TVHeadend JSON endpoint and return the parsed body."""
    req = requests.get(
        url, auth=HTTPDigestAuth(config["tvheadend_user"], config["tvheadend_pass"])
    )
    req.encoding = "UTF-8"
    if req.status_code != 200:
        print("TVHeadend %s returned HTTP %s" % (url, req.status_code))
        raise SystemExit(1)
    return req.json()


# Deduplication counter for clean_name(): tracks how many times a sanitized
# name has been seen so collisions get a "-N" suffix.
_clean_name_counter = {}


def clean_name(name):
    """Sanitize a channel name to a filename-safe ID, de-duplicating collisions."""
    out = "".join(
        c if ("A" <= c <= "Z" or "0" <= c <= "9") else ("_" if c == " " else "")
        for c in name.upper()
    )
    if len(out) < 2:
        out = "INVALID"
    seen = _clean_name_counter.get(out, 0) + 1
    _clean_name_counter[out] = seen
    return out if seen == 1 else "%s-%d" % (out, seen)


class tv_channel_epg:
    def __init__(self, uuid, event_hash):
        self.uuid = uuid
        self.now = None
        self.events = {}
        self.last_update = time.time()
        self.add(event_hash)

    def add(self, event_hash):
        eventid = event_hash["eventId"]
        event_hash["start"] = int(event_hash["start"])
        event_hash["stop"] = int(event_hash["stop"])
        if event_hash["stop"] < time.time():
            return
        self.events[eventid] = event_hash
        if (
            self.now is None
            or self.now not in self.events
            or self.events[eventid]["start"] < self.events[self.now]["start"]
        ):
            self.now = eventid

    def update(self):
        """Drop ended events; if `self.now` no longer points at a valid event, refetch."""
        while (
            self.now is not None
            and self.now in self.events
            and self.events[self.now]["stop"] < time.time()
        ):
            ev = self.events[self.now]
            del self.events[self.now]
            self.now = ev.get("nextEventId")
        if self.now in self.events:
            return False
        epg_json = tvheadend_get(
            tvh_base_url + "/api/epg/events/grid?limit=10&channel=" + self.uuid
        )
        for event in epg_json["entries"]:
            if event["channelUuid"] == self.uuid:
                self.add(event)
        return True

    def _upcoming(self, n):
        out = []
        cur_id = self.now
        while cur_id is not None and cur_id in self.events and len(out) < n:
            out.append(self.events[cur_id])
            cur_id = self.events[cur_id].get("nextEventId")
        return out

    def get_entries(self, n):
        """Up to n upcoming events; refetches from TVHeadend if fewer are linked."""
        try:
            self.update()
        except Exception:
            pass
        entries = self._upcoming(n)
        if len(entries) < n:
            try:
                epg_json = tvheadend_get(
                    tvh_base_url
                    + "/api/epg/events/grid?limit=" + str(max(n, 20))
                    + "&channel=" + self.uuid
                )
                for event in epg_json["entries"]:
                    if event["channelUuid"] == self.uuid:
                        self.add(event)
            except Exception:
                pass
            entries = self._upcoming(n)
        return entries


# Channel names that we always skip — uplink test feeds and IPTV-only feeds we can't stream.
_SKIP_PREFIXES = ("ALT_", "ARD-Test", "Kabelio ")
_SKIP_SUFFIXES = ("(Internet)",)


def _should_skip(name):
    if name == "{name-not-set}":
        return True
    if name.startswith(_SKIP_PREFIXES):
        return True
    if name.endswith(_SKIP_SUFFIXES):
        return True
    return False


def _load_services_by_uuid():
    """Map service UUID → service record (for provider lookup). Empty dict if unavailable."""
    try:
        grid = tvheadend_get(tvh_base_url + "/api/mpegts/service/grid?limit=99999")
        return {s["uuid"]: s for s in grid["entries"]}
    except (Exception, SystemExit):
        return {}


def _load_tags():
    """Return (channel_tags_by_uuid, tv_tag_uuid, radio_tag_uuid)."""
    grid = tvheadend_get(tvh_base_url + "/api/channeltag/list")
    tags_by_uuid = {t["key"]: t["val"] for t in grid["entries"]}
    tv_tag = next((k for k, v in tags_by_uuid.items() if v == "TV channels"), None)
    radio_tag = next((k for k, v in tags_by_uuid.items() if v == "Radio channels"), None)
    return tags_by_uuid, tv_tag, radio_tag


def _channel_providers(channel, services_by_uuid):
    """Sorted-unique provider strings across the channel's linked services."""
    return sorted({
        services_by_uuid[su]["provider"]
        for su in (channel.get("services") or [])
        if su in services_by_uuid and services_by_uuid[su].get("provider")
    })


def tvheadend_get_channel_list():
    """Fetch every TV channel from TVHeadend, build TVChannel objects with country/provider info.

    Returns (sorted_channel_list, by_hls_uuid).
    """
    channels = tvheadend_get(tvh_base_url + "/api/channel/grid?limit=99999")["entries"]
    services_by_uuid = _load_services_by_uuid()
    channel_tags, tv_tag, radio_tag = _load_tags()

    channel_list = []
    for channel in channels:
        name = channel["name"]
        if _should_skip(name):
            continue
        tag_ids = channel.get("tags") or []
        if radio_tag is not None and radio_tag in tag_ids:
            continue

        tag_display_names = []
        non_tv_tag_names = []  # tag display names except the umbrella "TV channels" tag
        for t in tag_ids:
            display = channel_tags.get(t)
            if display is None:
                continue
            tag_display_names.append(display)
            if t != tv_tag:
                non_tv_tag_names.append(display)
        tags = "(" + ", ".join(non_tv_tag_names) + ")" if non_tv_tag_names else ""

        providers = _channel_providers(channel, services_by_uuid)
        provider = ", ".join(providers) if providers else None
        country = guess_country(channel, services_by_uuid, tag_display_names, provider=provider)
        channel_list.append(TVChannel(
            name, tags, channel["number"], channel["uuid"], clean_name(name),
            country=country, provider=provider,
        ))

    by_hls_uuid = {ch.hls_uuid: ch for ch in channel_list}
    channel_list.sort(key=lambda x: getattr(x, config["sort"]))
    return channel_list, by_hls_uuid
