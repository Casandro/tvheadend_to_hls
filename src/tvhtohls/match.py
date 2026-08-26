"""Pure channel-matching logic: which channels on different TVHeadend servers are
the same channel, and what stable id the merged result gets.

This module deliberately imports nothing from the rest of the package (in
particular not `.config`, which exits at import time when `hls_local_path` is
missing) so it stays unit-testable outside the container. All thresholds are
keyword defaults.

The matching signals, strongest first:

  1. DVB triplet — original_network_id + transport_stream_id + service_id.
     Identical across instances receiving the same transponder; conclusive.
  2. Same (tsid, sid) on what looks like the same transponder, used when onid is
     missing (TVHeadend frequently reports onid=0 on cable/IPTV).
  3. Same channel name plus agreeing EPG (same event start times and titles).
  4. Same channel name with no EPG anywhere to corroborate or contradict it.

Levels 3 and 4 can be vetoed by contradicting evidence (disagreeing EPG,
disagreeing DVB triplets, HD-vs-SD service types, different guessed countries).
"""
import time


# ---------------------------------------------------------------- scalar helpers

# TVHeadend reports "no value" for DVB ids as 0, and occasionally as 0xFFFF.
UNKNOWN_IDS = frozenset((0, 0xFFFF, -1))


def as_int(v):
    """int(v) or None — TVHeadend sends numbers as ints, strings or nothing."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def known_id(v):
    """A DVB id that actually carries information."""
    i = as_int(v)
    if i is None or i in UNKNOWN_IDS:
        return None
    return i


def delsys_family(delsys):
    """Group TVHeadend's `delsys` strings into satellite / terrestrial / cable."""
    d = (delsys or "").upper()
    if not d or d == "NONE":
        return None
    if d.startswith(("DVBS", "DVB-S", "DSS", "ISDBS", "TURBO")):
        return "DVB-S"
    if d.startswith(("DVBT", "DVB-T", "ISDBT", "ATSC_T", "ATSCT", "DTMB")):
        return "DVB-T"
    if d.startswith(("DVBC", "DVB-C", "ATSC_C", "ISDBC")):
        return "DVB-C"
    return d


def normalize_frequency(raw, delsys=None):
    """Return a frequency in Hz, or None.

    TVHeadend stores DVB-S mux frequencies in kHz and DVB-T/T2/C/ATSC ones in Hz
    (the property label literally differs between the mux classes), and some
    setups store MHz. Trust `delsys` when we know it, but sanity-check against
    the magnitude: broadcast RF lives between ~40 MHz and ~14 GHz.
    """
    v = as_int(raw)
    if not v or v < 0:
        return None
    if 1e2 <= v < 1e5:
        by_magnitude = v * 1000000     # MHz
    elif 1e5 <= v < 1e8:
        by_magnitude = v * 1000        # kHz
    else:
        by_magnitude = v               # Hz
    if delsys_family(delsys) == "DVB-S":
        return v * 1000 if v < 1e8 else v
    return by_magnitude


def normalize_symbolrate(raw):
    """Return a symbol rate in Sym/s, or None (some setups store kSym/s)."""
    v = as_int(raw)
    if not v or v < 0:
        return None
    return v * 1000 if v < 100000 else v


def normalize_polarisation(raw):
    """'HORIZONTAL' / 'H' / 'v' → 'H' / 'V'; empty → None."""
    p = (raw or "").strip().upper()
    return p[0] if p else None


def sanitize(name):
    """Sanitize a channel name to a filename-safe id (no collision handling)."""
    out = "".join(
        c if ("A" <= c <= "Z" or "0" <= c <= "9") else ("_" if c == " " else "")
        for c in (name or "").upper()
    )
    if len(out) < 2:
        out = "INVALID"
    return out


def normalize_name(name):
    """Matching key for a channel or programme title.

    Folds case, punctuation and whitespace but deliberately KEEPS the tokens that
    distinguish different feeds: 'HD'/'UHD'/'SD' stay, and '+' becomes ' PLUS '
    so a '+1' timeshift channel doesn't normalize onto its parent channel.
    """
    s = (name or "").upper().replace("+", " PLUS ")
    s = "".join(c if c.isalnum() else " " for c in s)
    return " ".join(s.split())


def tight_name(name_key):
    """Space-free variant, so 'TV 3' and 'TV3' can match. Weaker — name+EPG only."""
    return name_key.replace(" ", "")


# ------------------------------------------------------------- service identity

# DVB service_type values. 0x16 is "advanced codec SD", 0x19 "advanced codec HD".
SD_SERVICETYPES = frozenset((0x01, 0x16))
HD_SERVICETYPES = frozenset((0x11, 0x19, 0x1E, 0x1F))


class ServiceIdentity:
    """What we know about one DVB service, joined with its mux."""

    __slots__ = ("sid", "tsid", "onid", "delsys", "freq_hz", "symbolrate",
                 "polarisation", "orbital_pos", "servicetype")

    def __init__(self, sid=None, tsid=None, onid=None, delsys=None, freq_hz=None,
                 symbolrate=None, polarisation=None, orbital_pos=None,
                 servicetype=None):
        self.sid = sid
        self.tsid = tsid
        self.onid = onid
        self.delsys = delsys
        self.freq_hz = freq_hz
        self.symbolrate = symbolrate
        self.polarisation = polarisation
        self.orbital_pos = orbital_pos
        self.servicetype = servicetype

    def __repr__(self):
        return "ServiceIdentity(sid=%r, tsid=%r, onid=%r, freq=%r)" % (
            self.sid, self.tsid, self.onid, self.freq_hz)


def build_mux_index(muxes):
    """Index mux records for the service→mux join.

    Returns (by_uuid, by_name, ambiguous_names). The name index is the fallback
    for TVHeadend builds whose service grid has no `multiplex_uuid`: the service's
    `multiplex` field is a display string produced from the mux's nice name.
    """
    by_uuid = {}
    by_name = {}
    for m in muxes or []:
        uuid = m.get("uuid")
        if uuid:
            by_uuid[uuid] = m
        name = m.get("name")
        network = m.get("network")
        for key in (name, "%s/%s" % (network, name) if network and name else None):
            if key:
                by_name.setdefault(key, []).append(m)
    ambiguous = frozenset(k for k, v in by_name.items() if len(v) > 1)
    return by_uuid, {k: v[0] for k, v in by_name.items()}, ambiguous


def resolve_mux(service, mux_index):
    """The mux record a service sits on, or None when it can't be determined."""
    by_uuid, by_name, ambiguous = mux_index
    uuid = service.get("multiplex_uuid")
    if uuid and uuid in by_uuid:
        return by_uuid[uuid]
    nice = service.get("multiplex")
    if nice and nice not in ambiguous:
        return by_name.get(nice)
    return None


def service_identity(service, mux=None):
    """Build a ServiceIdentity from a service record and (optionally) its mux."""
    mux = mux or {}
    delsys = mux.get("delsys")
    return ServiceIdentity(
        sid=known_id(service.get("sid")),
        tsid=known_id(mux.get("tsid")),
        onid=known_id(mux.get("onid")),
        delsys=delsys,
        freq_hz=normalize_frequency(mux.get("frequency"), delsys),
        symbolrate=normalize_symbolrate(mux.get("symbolrate")),
        polarisation=normalize_polarisation(mux.get("polarisation")),
        # TVHeadend calls this "orbital" in 4.2/4.4 and "orbital_pos" elsewhere.
        orbital_pos=(mux.get("orbital") or mux.get("orbital_pos") or None),
        servicetype=as_int(service.get("dvb_servicetype")),
    )


# Frequency tolerance: two records of the same transponder differ only by scan
# rounding (11836 vs 11836.5 MHz), never by more than a couple of MHz. Half the
# symbol rate is comfortably above that while staying below the spacing of
# adjacent transponders — on Astra 19.2E, 29.5 MSym/s carriers sit ~29.5 MHz
# apart on one polarisation, so a full-symbol-rate window would make neighbours
# compare equal.
FREQ_TOL_MIN = 1000000
FREQ_TOL_MAX = 4000000
FREQ_TOL_OFDM = 500000        # DVB-T/T2 muxes carry no symbol rate


def same_transponder(x, y, tol_min=FREQ_TOL_MIN, tol_max=FREQ_TOL_MAX,
                     tol_ofdm=FREQ_TOL_OFDM):
    """True if two identities look like the same physical transponder."""
    fam_x, fam_y = delsys_family(x.delsys), delsys_family(y.delsys)
    if fam_x != fam_y:
        return False
    if fam_x == "DVB-S":
        if x.polarisation and y.polarisation and x.polarisation != y.polarisation:
            return False
        if x.orbital_pos and y.orbital_pos and x.orbital_pos != y.orbital_pos:
            return False
    if x.freq_hz is None or y.freq_hz is None:
        return False
    rates = [r for r in (x.symbolrate, y.symbolrate) if r]
    if rates:
        tol = max(tol_min, min(min(rates) // 2, tol_max))
    else:
        tol = tol_ofdm
    return abs(x.freq_hz - y.freq_hz) <= tol


# ------------------------------------------------------------------------ EPG

EPG_HORIZON = 6 * 3600
EPG_MAX_EVENTS = 12
EPG_START_TOL = 90            # seconds; over-the-air EIT is exact, grabbers drift
EPG_AGREE = 0.5               # at/above this, EPG supports a name match
EPG_CONFLICT = 0.2            # below this, EPG vetoes a name match
EPG_MIN_EVENTS = 2            # fewer than this on either side → "no evidence"


class EpgEvent(tuple):
    """(start, stop, title_key) — comparable and hashable."""

    __slots__ = ()

    def __new__(cls, start, stop, title_key):
        return tuple.__new__(cls, (start, stop, title_key))

    start = property(lambda self: self[0])
    stop = property(lambda self: self[1])
    title_key = property(lambda self: self[2])


def epg_fingerprint(events, now=None, horizon=EPG_HORIZON, max_events=EPG_MAX_EVENTS):
    """Upcoming events as a sorted tuple of EpgEvent; () when there is no usable EPG.

    Only (start, stop, title) are used. TVHeadend's `eventId`/`nextEventId` are
    per-instance counters, not DVB event ids, so they are meaningless across
    servers.
    """
    now = time.time() if now is None else now
    out = set()
    for e in events or []:
        start = as_int(e.get("start"))
        stop = as_int(e.get("stop"))
        title = (e.get("title") or "").strip()
        if start is None or stop is None or not title:
            continue
        if stop < now or start > now + horizon:
            continue
        out.add(EpgEvent(start, stop, normalize_name(title)))
    return tuple(sorted(out)[:max_events])


def _title_agrees(p, q):
    if p == q:
        return True
    # Grabbers truncate or extend titles ("TAGESSCHAU" vs "TAGESSCHAU 20 UHR").
    return len(p) >= 6 and len(q) >= 6 and (p.startswith(q) or q.startswith(p))


def epg_agreement(a, b, start_tol=EPG_START_TOL, min_events=EPG_MIN_EVENTS):
    """Fraction of the smaller fingerprint that is present in the other one.

    None when either side has too few events to conclude anything — that is
    "no evidence", which is neither support nor contradiction.
    """
    if len(a) < min_events or len(b) < min_events:
        return None
    hits = 0
    for x in a:
        for y in b:
            if abs(x.start - y.start) <= start_tol and _title_agrees(x.title_key, y.title_key):
                hits += 1
                break
    return hits / float(min(len(a), len(b)))


# ------------------------------------------------------------- channel sources

class ChannelSource:
    """One server's view of one channel, before merging."""

    def __init__(self, tvh_uuid, name, *, server=None, server_name=None,
                 server_order=0, number=None, tags="", tag_names=(), country=None,
                 provider=None, audio_only=False, is_radio=False, identities=(),
                 epg_events=(), epg_fp=None, now=None):
        self.server = server
        self.server_name = server_name or (getattr(server, "name", None) or "")
        self.server_order = (
            server_order if server is None else getattr(server, "order", server_order)
        )
        self.tvh_uuid = tvh_uuid
        self.name = name
        self.number = number
        self.tags = tags
        self.tag_names = tuple(tag_names)
        self.country = country
        self.provider = provider
        self.audio_only = audio_only
        self.is_radio = is_radio
        self.identities = tuple(identities)
        self.epg_events = list(epg_events)
        self.name_key = normalize_name(name)
        self.tight_key = tight_name(self.name_key)
        self.epg_fp = epg_fingerprint(self.epg_events, now=now) if epg_fp is None else epg_fp
        self.servicetypes = frozenset(
            i.servicetype for i in self.identities if i.servicetype is not None
        )

    @property
    def sort_key(self):
        """Stable across restarts: configured server order, then TVHeadend's uuid."""
        return (self.server_order, self.tvh_uuid)

    @property
    def label(self):
        return "%s:%s" % (self.server_name, self.tvh_uuid)

    def __repr__(self):
        return "<ChannelSource %s %r>" % (self.label, self.name)


# ------------------------------------------------------------------- matching

LEVEL_TRIPLET = 300
LEVEL_TRANSPONDER = 200
LEVEL_NAME_EPG = 100
LEVEL_NAME_ONLY = 50
LEVEL_VETO = 0


class MatchResult(tuple):
    """(level, label, detail). level == LEVEL_VETO means "actively contradicted"."""

    __slots__ = ()

    def __new__(cls, level, label, detail=""):
        return tuple.__new__(cls, (level, label, detail))

    level = property(lambda self: self[0])
    label = property(lambda self: self[1])
    detail = property(lambda self: self[2])

    def __repr__(self):
        return "MatchResult(%d, %r, %r)" % self


def _identity_match(a, b):
    """Best DVB-identity match between two sources, or None."""
    best = None
    for x in a.identities:
        if x.sid is None:
            continue
        for y in b.identities:
            if y.sid is None or x.sid != y.sid:
                continue
            if x.tsid is None or y.tsid is None or x.tsid != y.tsid:
                continue
            if x.onid is not None and y.onid is not None:
                if x.onid != y.onid:
                    continue
                return MatchResult(
                    LEVEL_TRIPLET, "triplet",
                    "onid=%d tsid=%d sid=%d" % (x.onid, x.tsid, x.sid),
                )
            # onid unknown on at least one side: confirm via the transponder, or
            # accept when neither side reports a frequency at all.
            no_freq = x.freq_hz is None or y.freq_hz is None
            if same_transponder(x, y) or (
                no_freq and delsys_family(x.delsys) == delsys_family(y.delsys)
            ):
                if best is None:
                    freq = x.freq_hz or y.freq_hz
                    best = MatchResult(
                        LEVEL_TRANSPONDER, "transponder",
                        "tsid=%d sid=%d%s" % (
                            x.tsid, x.sid,
                            " @%.1fMHz" % (freq / 1e6) if freq else "",
                        ),
                    )
    return best


def _identity_contradiction(a, b):
    """True when both sides know full triplets everywhere and none of them match."""
    if not a.identities or not b.identities:
        return False
    for src in (a, b):
        for i in src.identities:
            if i.sid is None or i.tsid is None or i.onid is None:
                return False
    return True


def _hd_sd_contradiction(a, b):
    a_hd = bool(a.servicetypes & HD_SERVICETYPES)
    a_sd = bool(a.servicetypes & SD_SERVICETYPES)
    b_hd = bool(b.servicetypes & HD_SERVICETYPES)
    b_sd = bool(b.servicetypes & SD_SERVICETYPES)
    return (a_hd and not a_sd and b_sd and not b_hd) or (
        b_hd and not b_sd and a_sd and not a_hd)


def _name_veto(a, b, agreement, epg_conflict=EPG_CONFLICT):
    if agreement is not None and agreement < epg_conflict:
        return MatchResult(LEVEL_VETO, "epg-conflict", "agreement=%.2f" % agreement)
    if _identity_contradiction(a, b):
        return MatchResult(LEVEL_VETO, "identity-conflict", "DVB triplets differ")
    if _hd_sd_contradiction(a, b):
        return MatchResult(LEVEL_VETO, "hd-sd", "service types differ")
    if a.country and b.country and a.country != b.country:
        return MatchResult(LEVEL_VETO, "country", "%s vs %s" % (a.country, b.country))
    return None


def compare(a, b, epg_agree=EPG_AGREE, epg_conflict=EPG_CONFLICT):
    """How strongly two sources look like the same channel.

    Returns a MatchResult (level > 0 = merge, level == 0 = actively contradicted)
    or None when the two are simply unrelated.
    """
    ident = _identity_match(a, b)
    if ident is not None:
        # Same physical DVB service: EPG disagreement just means one grabber is
        # stale, so nothing vetoes this.
        return ident

    names_equal = bool(a.name_key) and a.name_key == b.name_key
    tight_equal = bool(a.tight_key) and a.tight_key == b.tight_key
    if not (names_equal or tight_equal):
        return None

    agreement = epg_agreement(a.epg_fp, b.epg_fp)
    veto = _name_veto(a, b, agreement, epg_conflict=epg_conflict)
    if veto is not None:
        return veto
    if agreement is not None:
        if agreement >= epg_agree:
            return MatchResult(LEVEL_NAME_EPG, "name+epg", "agreement=%.2f" % agreement)
        # Partial agreement: not enough to merge, not bad enough to veto.
        return None
    if names_equal:
        return MatchResult(LEVEL_NAME_ONLY, "name", "no EPG on either side")
    return None


def _candidate_pairs(ordered):
    """Index-pairs worth comparing.

    Every match level requires either an equal service id or an equal name key,
    so bucketing on those is complete and avoids the full O(n^2) sweep.
    """
    buckets = {}
    for i, s in enumerate(ordered):
        keys = {("name", s.name_key), ("tight", s.tight_key)}
        for ident in s.identities:
            if ident.sid is not None:
                keys.add(("sid", ident.sid))
        for k in keys:
            buckets.setdefault(k, []).append(i)
    pairs = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for pos, i in enumerate(members):
            for j in members[pos + 1:]:
                pairs.add((i, j))
    return sorted(pairs)


def group_channels(sources, log=None, epg_agree=EPG_AGREE, epg_conflict=EPG_CONFLICT):
    """Group per-server sources into merged channels.

    Two sources from the *same* server are never grouped together (TVHeadend
    already merges its own services into channels, so two entries there are
    genuinely two channels). That constraint makes this not an equivalence
    relation, so grouping is greedy in order of match strength: conclusive DVB
    matches claim their partner before weak name-only matches get a chance.

    `log(kind, a, b, match)` is called for every merge and every veto, with
    `kind` one of "merge" / "veto".

    Deterministic: the result depends only on the set of sources, not on the
    order they arrive in.
    """
    ordered = sorted(sources, key=lambda s: s.sort_key)
    scored = []
    for i, j in _candidate_pairs(ordered):
        a, b = ordered[i], ordered[j]
        if a.server_name == b.server_name:
            continue
        m = compare(a, b, epg_agree=epg_agree, epg_conflict=epg_conflict)
        if m is None:
            continue
        if m.level <= LEVEL_VETO:
            if log:
                log("veto", a, b, m)
            continue
        scored.append((-m.level, a.sort_key, b.sort_key, i, j, m))
    scored.sort(key=lambda t: t[:3])

    parent = list(range(len(ordered)))
    servers = [{s.server_name} for s in ordered]

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for _, _, _, i, j, m in scored:
        ra, rb = find(i), find(j)
        if ra == rb:
            continue
        if servers[ra] & servers[rb]:
            if log:
                log("veto", ordered[i], ordered[j],
                    MatchResult(LEVEL_VETO, "server-overlap",
                                "already merged with that server"))
            continue
        keep, drop = (ra, rb) if ra < rb else (rb, ra)
        parent[drop] = keep
        servers[keep] |= servers[drop]
        if log:
            log("merge", ordered[i], ordered[j], m)

    groups = {}
    for i, s in enumerate(ordered):
        groups.setdefault(find(i), []).append(s)
    return [groups[root] for root in sorted(groups)]


def assign_hls_uuids(groups):
    """One filename/URL-safe id per group, parallel to `groups`.

    Collisions get a "-2", "-3", … suffix assigned in a deterministic order (by
    the group's primary source), so a restart doesn't shuffle which channel a
    bookmarked ?uuid= points at.
    """
    by_base = {}
    for i, group in enumerate(groups):
        by_base.setdefault(sanitize(group[0].name), []).append(i)
    out = [None] * len(groups)
    for base, idxs in by_base.items():
        for n, i in enumerate(sorted(idxs, key=lambda i: groups[i][0].sort_key), 1):
            out[i] = base if n == 1 else "%s-%d" % (base, n)
    return out
