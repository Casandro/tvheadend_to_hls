"""TVHeadend API access: per-server snapshots, channel merging, EPG feeds."""
import concurrent.futures
import time

from .config import config
from .flags import guess_country, service_av
from .match import (
    ChannelSource, assign_hls_uuids, build_mux_index, group_channels,
    resolve_mux, service_identity,
)
from .server import STARTUP_READ_TIMEOUT, TVHError
from .streams import StreamSource, TVChannel


class tv_channel_epg:
    """The EPG for one merged channel, fed from exactly one server.

    Events are *not* pooled across servers: `_upcoming()` walks TVHeadend's
    `nextEventId` chain, and those ids are per-instance counters, so mixing two
    servers' events either truncates the listing or jumps to an unrelated
    programme.
    """

    # When a flagged channel has run dry, don't refetch from TVHeadend more
    # often than this (seconds) — the background loop ticks every second and we
    # don't want to hammer the server while it genuinely has no fresh events.
    REFETCH_INTERVAL = 3600

    def __init__(self, server, uuid, event_hash=None):
        self.server = server
        self.uuid = uuid
        self.now = None
        self.events = {}
        self.last_fetch = time.time()
        if event_hash is not None:
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

    def _prune(self):
        """Drop events that have already ended, advancing `self.now` along the
        chain. Leaves `self.now = None` when no current event remains."""
        while (
            self.now is not None
            and self.now in self.events
            and self.events[self.now]["stop"] < time.time()
        ):
            ev = self.events[self.now]
            del self.events[self.now]
            self.now = ev.get("nextEventId")
        if self.now not in self.events:
            self.now = None

    def has_events(self):
        """True if a current/upcoming event remains after pruning ended ones."""
        self._prune()
        return self.now is not None

    def refetch(self, n=20):
        """Fetch fresh EPG events for this channel from its server."""
        self.last_fetch = time.time()  # set first so the throttle holds even on error
        epg_json = self.server.get(
            "api/epg/events/grid?limit=%d&channel=%s" % (max(n, 20), self.uuid)
        )
        for event in epg_json["entries"]:
            if event["channelUuid"] == self.uuid:
                self.add(event)

    def update(self):
        """Prune ended events; if the channel has run dry, refetch (throttled).

        Returns True if a current event remains afterwards.
        """
        self._prune()
        if self.now is None and time.time() - self.last_fetch >= self.REFETCH_INTERVAL:
            try:
                self.refetch()
            except Exception:
                pass
        return self.now is not None

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
            self._prune()
        except Exception:
            pass
        entries = self._upcoming(n)
        if len(entries) < n and time.time() - self.last_fetch >= self.REFETCH_INTERVAL:
            try:
                self.refetch(n)
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


def _channel_providers(channel, services_by_uuid):
    """Sorted-unique provider strings across the channel's linked services."""
    return sorted({
        services_by_uuid[su]["provider"]
        for su in (channel.get("services") or [])
        if su in services_by_uuid and services_by_uuid[su].get("provider")
    })


def _tags(tag_entries):
    """Return (tags_by_uuid, tv_tag_uuid, radio_tag_uuid) from /api/channeltag/list."""
    tags_by_uuid = {t["key"]: t["val"] for t in tag_entries}
    tv_tag = next((k for k, v in tags_by_uuid.items() if v == "TV channels"), None)
    radio_tag = next((k for k, v in tags_by_uuid.items() if v == "Radio channels"), None)
    return tags_by_uuid, tv_tag, radio_tag


class ServerSnapshot:
    """Everything we read from one server at startup."""

    def __init__(self, server, channels, services_by_uuid, mux_index, tags_by_uuid,
                 tv_tag, radio_tag, epg_by_channel, identity_available):
        self.server = server
        self.channels = channels
        self.services_by_uuid = services_by_uuid
        self.mux_index = mux_index
        self.tags_by_uuid = tags_by_uuid
        self.tv_tag = tv_tag
        self.radio_tag = radio_tag
        self.epg_by_channel = epg_by_channel
        self.identity_available = identity_available


def _entries(server, path, what, optional=True):
    """GET `path` and return its "entries" list; [] plus a log line on failure."""
    try:
        return server.get(path, read_timeout=STARTUP_READ_TIMEOUT)["entries"]
    except (TVHError, KeyError, TypeError) as e:
        if optional:
            print("%s: %s unavailable (%s)" % (server.name, what, e))
            return []
        raise TVHError(str(e))


def fetch_server_snapshot(server):
    """Read one server's channels, services, muxes, tags and bulk EPG.

    Never raises: a server that can't produce a channel list is reported and
    skipped, and every other endpoint degrades to empty so a stream-only API
    account (no rights on /api/mpegts/*) still yields a usable channel list.
    """
    try:
        channels = _entries(server, "api/channel/grid?limit=99999",
                            "channel grid", optional=False)
    except TVHError as e:
        print("%s: channel list unavailable (%s) — skipping this server" % (server.name, e))
        return None

    services = _entries(server, "api/mpegts/service/grid?limit=99999", "service grid")
    services_by_uuid = {s["uuid"]: s for s in services if s.get("uuid")}
    muxes = _entries(server, "api/mpegts/mux/grid?limit=99999", "mux grid")
    tag_entries = _entries(server, "api/channeltag/list", "channel tags")
    if not tag_entries:
        print("%s: no channel tags — radio channels can't be identified "
              "(include_radio filtering disabled for this server)" % server.name)
    tags_by_uuid, tv_tag, radio_tag = _tags(tag_entries)

    epg_by_channel = {}
    for event in _entries(server, "api/epg/events/grid?limit=10000", "EPG grid"):
        uuid = event.get("channelUuid")
        if uuid:
            epg_by_channel.setdefault(uuid, []).append(event)

    print("%s: %d channels, %d services, %d muxes, %d EPG feeds, identity=%s"
          % (server.name, len(channels), len(services), len(muxes), len(epg_by_channel),
             "yes" if services and muxes else ("sid-only" if services else "no")))

    return ServerSnapshot(
        server, channels, services_by_uuid, build_mux_index(muxes),
        tags_by_uuid, tv_tag, radio_tag, epg_by_channel,
        identity_available=bool(services),
    )


def fetch_snapshots(servers):
    """Snapshot every server in parallel, preserving the configured order.

    Parallel because a dead server costs several connect timeouts, which would
    otherwise delay startup for the servers that *are* up.
    """
    if len(servers) == 1:
        return [s for s in (fetch_server_snapshot(servers[0]),) if s is not None]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(servers)) as pool:
        results = list(pool.map(fetch_server_snapshot, servers))
    return [r for r in results if r is not None]


def _identities(channel, snapshot):
    """DVB identities for every service the channel is mapped to."""
    out = []
    for su in channel.get("services") or []:
        service = snapshot.services_by_uuid.get(su)
        if not service:
            continue
        out.append(service_identity(service, resolve_mux(service, snapshot.mux_index)))
    return tuple(out)


def build_channel_sources(snapshot):
    """One ChannelSource per usable channel on this server."""
    sources = []
    for channel in snapshot.channels:
        name = channel.get("name") or ""
        if _should_skip(name):
            continue
        tag_ids = channel.get("tags") or []
        is_radio = snapshot.radio_tag is not None and snapshot.radio_tag in tag_ids
        if is_radio and not config["include_radio"]:
            continue

        tag_display_names = []
        non_tv_tag_names = []  # tag display names except the umbrella "TV channels" tag
        for t in tag_ids:
            display = snapshot.tags_by_uuid.get(t)
            if display is None:
                continue
            tag_display_names.append(display)
            if t != snapshot.tv_tag:
                non_tv_tag_names.append(display)
        tags = "(" + ", ".join(non_tv_tag_names) + ")" if non_tv_tag_names else ""

        providers = _channel_providers(channel, snapshot.services_by_uuid)
        provider = ", ".join(providers) if providers else None
        country = guess_country(
            channel, snapshot.services_by_uuid, tag_display_names, provider=provider)
        # Synthesize a dummy video only with positive evidence of audio-without-video
        # (or an explicit Radio tag). Unknown metadata → treat as a normal TV channel.
        has_video, has_audio = service_av(channel, snapshot.services_by_uuid)
        audio_only = is_radio or (has_video is False and has_audio)

        sources.append(ChannelSource(
            channel["uuid"], name,
            server=snapshot.server,
            number=channel.get("number"),
            tags=tags,
            tag_names=tag_display_names,
            country=country,
            provider=provider,
            audio_only=audio_only,
            is_radio=is_radio,
            identities=_identities(channel, snapshot),
            epg_events=snapshot.epg_by_channel.get(channel["uuid"], ()),
        ))
    return sources


def _epg_source(group):
    """The source whose EPG we use: richest first, ties to the earlier server."""
    return max(group, key=lambda s: (len(s.epg_fp), len(s.epg_events), -s.server_order))


def build_merged_channels(groups):
    """Turn source groups into TVChannel objects plus their EPG feeds.

    Returns (channel_list, epg_by_hls_uuid).
    """
    hls_uuids = assign_hls_uuids(groups)
    channels = []
    epg = {}
    for group, hls_uuid in zip(groups, hls_uuids):
        primary = group[0]
        sources = [
            StreamSource(s.server, s.tvh_uuid, s.server.stream_url(s.tvh_uuid), s.audio_only)
            for s in group
        ]
        channel = TVChannel(
            primary.name, primary.tags, primary.number, hls_uuid, sources,
            country=primary.country, provider=primary.provider,
        )
        channels.append(channel)

        feed_source = _epg_source(group)
        if feed_source.epg_events:
            feed = tv_channel_epg(feed_source.server, feed_source.tvh_uuid)
            for event in feed_source.epg_events:
                feed.add(event)
            if feed.has_events():
                epg[hls_uuid] = feed
                channel.had_epg = True

        numbers = {str(s.number) for s in group if s.number and str(s.number) != "0"}
        if len(numbers) > 1:
            print("note   number-mismatch %-24s %s" % (
                '"%s"' % primary.name, ", ".join(sorted(numbers))))

    channels.sort(key=lambda x: getattr(x, config["sort"]))
    return channels, epg


def _log_decision(kind, a, b, m):
    print("%-6s %-16s %-26s %s + %s  (%s)" % (
        kind, m.label, '"%s"' % a.name[:24], a.label, b.label, m.detail))


def load_channels(servers):
    """Fetch every server, merge duplicate channels, build the served state.

    Returns (channel_list, by_hls_uuid, epg_by_hls_uuid).
    """
    snapshots = fetch_snapshots(servers)
    if not snapshots:
        print("No TVHeadend server returned a channel list — check the configuration")
        raise SystemExit(1)

    sources = []
    for snapshot in snapshots:
        sources.extend(build_channel_sources(snapshot))

    groups = group_channels(sources, log=_log_decision if len(snapshots) > 1 else None)
    channels, epg = build_merged_channels(groups)

    if len(snapshots) > 1:
        per_server = {}
        for group in groups:
            for s in group:
                per_server[s.server_name] = per_server.get(s.server_name, 0) + 1
        print("dedup: %d sources -> %d channels (%d collapsed); %s" % (
            len(sources), len(channels), len(sources) - len(channels),
            ", ".join("%s %d" % (n, c) for n, c in sorted(per_server.items())) or "-",
        ))
    print("Loaded %d TV channels, %d with EPG" % (len(channels), len(epg)))

    return channels, {ch.hls_uuid: ch for ch in channels}, epg
