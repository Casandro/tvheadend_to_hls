"""Unit tests for the pure channel-matching logic.

    python3 -m unittest discover -s tests -v
"""
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from tvhtohls import match  # noqa: E402


NOW = 1_700_000_000


def ident(sid=None, tsid=None, onid=None, delsys="DVBS", freq=None, symbolrate=None,
          pol=None, orbital=None, servicetype=None):
    """A ServiceIdentity, with DVB ids normalized the way production does it."""
    return match.ServiceIdentity(
        sid=match.known_id(sid), tsid=match.known_id(tsid), onid=match.known_id(onid),
        delsys=delsys,
        freq_hz=freq, symbolrate=symbolrate, polarisation=pol,
        orbital_pos=orbital, servicetype=servicetype,
    )


def events(*specs):
    """[(minutes_from_now, duration_minutes, title), …] → raw TVHeadend events."""
    out = []
    for offset, length, title in specs:
        start = NOW + offset * 60
        out.append({"start": start, "stop": start + length * 60, "title": title})
    return out


def source(server, uuid, name, *, order=None, identities=(), epg=(), country=None,
           audio_only=False):
    return match.ChannelSource(
        uuid, name,
        server_name=server,
        server_order=order if order is not None else (0 if server == "a" else 1),
        identities=identities, epg_events=epg, country=country,
        audio_only=audio_only, now=NOW,
    )


class TestNameNormalization(unittest.TestCase):
    def test_folds_case_and_punctuation(self):
        self.assertEqual(match.normalize_name("Das Erste"), "DAS ERSTE")
        self.assertEqual(match.normalize_name("ARD-alpha"), "ARD ALPHA")
        self.assertEqual(match.normalize_name("  RTL   Zwei "), "RTL ZWEI")

    def test_keeps_resolution_tokens(self):
        self.assertNotEqual(match.normalize_name("ARD"), match.normalize_name("ARD HD"))
        self.assertNotEqual(
            match.normalize_name("Sky Cinema HD"), match.normalize_name("Sky Cinema UHD"))

    def test_keeps_timeshift_marker(self):
        self.assertEqual(match.normalize_name("Sky Cinema +1"), "SKY CINEMA PLUS 1")
        self.assertNotEqual(
            match.normalize_name("Sky Cinema"), match.normalize_name("Sky Cinema +1"))

    def test_distinct_names_stay_distinct(self):
        self.assertNotEqual(
            match.normalize_name("Pro7 MAXX"), match.normalize_name("ProSieben MAXX"))

    def test_tight_name_only_collapses_spaces(self):
        self.assertEqual(match.tight_name(match.normalize_name("TV 3")), "TV3")
        self.assertEqual(match.tight_name(match.normalize_name("TV3")), "TV3")

    def test_sanitize_matches_legacy_scheme(self):
        self.assertEqual(match.sanitize("ARD-alpha"), "ARDALPHA")
        self.assertEqual(match.sanitize("ProSieben MAXX"), "PROSIEBEN_MAXX")
        self.assertEqual(match.sanitize("!"), "INVALID")


class TestUnitNormalization(unittest.TestCase):
    def test_satellite_frequency_is_khz(self):
        self.assertEqual(match.normalize_frequency(11836000, "DVBS"), 11836000000)
        self.assertEqual(match.normalize_frequency(12480000, "DVBS2"), 12480000000)

    def test_terrestrial_and_cable_frequency_is_hz(self):
        self.assertEqual(match.normalize_frequency(482000000, "DVBT"), 482000000)
        self.assertEqual(match.normalize_frequency(442000000, "DVBC_ANNEX_A"), 442000000)

    def test_magnitude_fallback_without_delsys(self):
        self.assertEqual(match.normalize_frequency(11836000, None), 11836000000)
        self.assertEqual(match.normalize_frequency(482000000, None), 482000000)
        self.assertEqual(match.normalize_frequency(11836, None), 11836000000)

    def test_missing_frequency(self):
        for raw in (0, None, "", "n/a", -1):
            self.assertIsNone(match.normalize_frequency(raw, "DVBS"))

    def test_symbolrate_units(self):
        self.assertEqual(match.normalize_symbolrate(22000), 22000000)
        self.assertEqual(match.normalize_symbolrate(22000000), 22000000)
        self.assertIsNone(match.normalize_symbolrate(0))
        self.assertIsNone(match.normalize_symbolrate(None))

    def test_polarisation(self):
        self.assertEqual(match.normalize_polarisation("HORIZONTAL"), "H")
        self.assertEqual(match.normalize_polarisation("v"), "V")
        self.assertIsNone(match.normalize_polarisation(""))

    def test_unknown_dvb_ids(self):
        self.assertIsNone(match.known_id(0))
        self.assertIsNone(match.known_id(0xFFFF))
        self.assertEqual(match.known_id("1079"), 1079)


class TestServiceIdentity(unittest.TestCase):
    MUXES = [
        {"uuid": "m1", "name": "11836H", "network": "Astra 19.2E", "delsys": "DVBS",
         "frequency": 11836000, "symbolrate": 27500000, "polarisation": "H",
         "tsid": 1079, "onid": 1},
        {"uuid": "m2", "name": "482000", "network": "DVB-T", "delsys": "DVBT",
         "frequency": 482000000, "tsid": 8468, "onid": 0},
    ]

    def test_joins_service_to_mux_by_uuid(self):
        index = match.build_mux_index(self.MUXES)
        svc = {"uuid": "s1", "sid": 28106, "multiplex_uuid": "m1",
               "multiplex": "Astra 19.2E/11836H", "dvb_servicetype": 25}
        i = match.service_identity(svc, match.resolve_mux(svc, index))
        self.assertEqual((i.sid, i.tsid, i.onid), (28106, 1079, 1))
        self.assertEqual(i.freq_hz, 11836000000)
        self.assertEqual(i.symbolrate, 27500000)
        self.assertEqual(i.polarisation, "H")
        self.assertEqual(i.servicetype, 25)

    def test_falls_back_to_the_multiplex_display_name(self):
        index = match.build_mux_index(self.MUXES)
        svc = {"uuid": "s1", "sid": 28106, "multiplex": "Astra 19.2E/11836H"}
        self.assertIsNotNone(match.resolve_mux(svc, index))
        svc2 = {"uuid": "s2", "sid": 1, "multiplex": "11836H"}
        self.assertIsNotNone(match.resolve_mux(svc2, index))

    def test_ambiguous_mux_names_are_not_used(self):
        muxes = self.MUXES + [dict(self.MUXES[0], uuid="m3", network="Hotbird 13E")]
        index = match.build_mux_index(muxes)
        # "11836H" now exists on two networks — the bare name must not resolve.
        self.assertIsNone(match.resolve_mux({"sid": 1, "multiplex": "11836H"}, index))
        # …the network-qualified form still does.
        self.assertIsNotNone(
            match.resolve_mux({"sid": 1, "multiplex": "Astra 19.2E/11836H"}, index))

    def test_zero_onid_becomes_unknown(self):
        index = match.build_mux_index(self.MUXES)
        svc = {"uuid": "s2", "sid": 17540, "multiplex_uuid": "m2"}
        i = match.service_identity(svc, match.resolve_mux(svc, index))
        self.assertEqual((i.sid, i.tsid, i.onid), (17540, 8468, None))

    def test_orbital_position_field_aliases(self):
        for key in ("orbital", "orbital_pos"):
            mux = {"uuid": "m", "delsys": "DVB-S", "frequency": 11389000,
                   "symbolrate": 24500000, "polarisation": "H", "tsid": 34,
                   "onid": 70, key: "1.0W"}
            self.assertEqual(match.service_identity({"sid": 112}, mux).orbital_pos, "1.0W")

    def test_hyphenated_delivery_systems(self):
        self.assertEqual(match.delsys_family("DVB-S"), "DVB-S")
        self.assertEqual(match.delsys_family("DVB-S2"), "DVB-S")
        self.assertEqual(match.delsys_family("DVB-T2"), "DVB-T")
        self.assertEqual(match.delsys_family("DVB-C"), "DVB-C")

    def test_service_without_a_mux_keeps_only_its_sid(self):
        i = match.service_identity({"sid": 28106}, None)
        self.assertEqual(i.sid, 28106)
        self.assertIsNone(i.tsid)
        self.assertIsNone(i.freq_hz)


class TestSameTransponder(unittest.TestCase):
    def test_scan_rounding_matches(self):
        a = ident(freq=11836000000, symbolrate=27500000, pol="H")
        b = ident(freq=11836500000, symbolrate=27500000, pol="H")
        self.assertTrue(match.same_transponder(a, b))

    def test_adjacent_astra_transponders_do_not_match(self):
        # 29.9 MSym/s carriers sit ~29.5 MHz apart: a full symbol-rate window
        # would merge neighbours, so the tolerance is capped well below it.
        a = ident(freq=11_493_000_000, symbolrate=29900000, pol="H")
        b = ident(freq=11_523_000_000, symbolrate=29900000, pol="H")
        self.assertFalse(match.same_transponder(a, b))

    def test_polarisation_must_agree(self):
        a = ident(freq=11836000000, symbolrate=27500000, pol="H")
        b = ident(freq=11836000000, symbolrate=27500000, pol="V")
        self.assertFalse(match.same_transponder(a, b))

    def test_orbital_position_must_agree(self):
        a = ident(freq=11836000000, symbolrate=27500000, pol="H", orbital="19.2E")
        b = ident(freq=11836000000, symbolrate=27500000, pol="H", orbital="13.0E")
        self.assertFalse(match.same_transponder(a, b))

    def test_delivery_systems_must_agree(self):
        a = ident(delsys="DVBS", freq=442000000, symbolrate=6900000)
        b = ident(delsys="DVBC_ANNEX_A", freq=442000000, symbolrate=6900000)
        self.assertFalse(match.same_transponder(a, b))

    def test_ofdm_without_symbolrate_uses_narrow_tolerance(self):
        a = ident(delsys="DVBT", freq=482000000)
        b = ident(delsys="DVBT", freq=482400000)
        c = ident(delsys="DVBT", freq=490000000)
        self.assertTrue(match.same_transponder(a, b))
        self.assertFalse(match.same_transponder(a, c))

    def test_unknown_frequency_is_not_a_match(self):
        a = ident(freq=None, symbolrate=27500000)
        b = ident(freq=11836000000, symbolrate=27500000)
        self.assertFalse(match.same_transponder(a, b))


class TestEpgAgreement(unittest.TestCase):
    def fp(self, *specs):
        return match.epg_fingerprint(events(*specs), now=NOW)

    def test_identical_schedules_fully_agree(self):
        a = self.fp((0, 30, "Tagesschau"), (30, 60, "Tatort"), (90, 30, "Wetter"))
        self.assertEqual(match.epg_agreement(a, a), 1.0)

    def test_timeshifted_schedule_disagrees(self):
        a = self.fp((0, 30, "Tagesschau"), (30, 60, "Tatort"), (90, 30, "Wetter"))
        b = self.fp((60, 30, "Tagesschau"), (90, 60, "Tatort"), (150, 30, "Wetter"))
        self.assertEqual(match.epg_agreement(a, b), 0.0)

    def test_sparse_epg_is_no_evidence(self):
        a = self.fp((0, 30, "Tagesschau"))
        b = self.fp((0, 30, "Tagesschau"), (30, 30, "Tatort"))
        self.assertIsNone(match.epg_agreement(a, b))
        self.assertIsNone(match.epg_agreement((), b))

    def test_start_tolerance_boundary(self):
        a = self.fp((0, 30, "Tagesschau"), (30, 30, "Tatort"))
        near = match.epg_fingerprint(
            [{"start": NOW + 89, "stop": NOW + 1800, "title": "Tagesschau"},
             {"start": NOW + 1800 + 89, "stop": NOW + 3600, "title": "Tatort"}], now=NOW)
        far = match.epg_fingerprint(
            [{"start": NOW + 91, "stop": NOW + 1800, "title": "Tagesschau"},
             {"start": NOW + 1800 + 91, "stop": NOW + 3600, "title": "Tatort"}], now=NOW)
        self.assertEqual(match.epg_agreement(a, near), 1.0)
        self.assertEqual(match.epg_agreement(a, far), 0.0)

    def test_truncated_titles_still_agree(self):
        a = self.fp((0, 30, "Tagesschau"), (30, 30, "Sportstudio"))
        b = self.fp((0, 30, "Tagesschau 20 Uhr"), (30, 30, "Sportstudio"))
        self.assertEqual(match.epg_agreement(a, b), 1.0)

    def test_fingerprint_drops_past_and_far_future(self):
        fp = match.epg_fingerprint(
            events((-120, 30, "Over"), (0, 30, "Now"), (24 * 60, 30, "Tomorrow")),
            now=NOW)
        self.assertEqual([e.title_key for e in fp], ["NOW"])


class TestCompare(unittest.TestCase):
    SCHED_A = ((0, 30, "Tagesschau"), (30, 60, "Tatort"), (90, 30, "Wetter"))
    SCHED_B = ((0, 30, "Kochshow"), (30, 60, "Kaufsendung"), (90, 30, "Nachrichten"))

    def test_triplet_beats_conflicting_epg(self):
        i = dict(sid=28006, tsid=1079, onid=1, freq=11836000000, symbolrate=27500000)
        a = source("a", "u1", "Das Erste HD", identities=[ident(**i)],
                   epg=events(*self.SCHED_A))
        b = source("b", "u2", "ARD HD", identities=[ident(**i)],
                   epg=events(*self.SCHED_B))
        self.assertEqual(match.compare(a, b).level, match.LEVEL_TRIPLET)

    def test_unknown_onid_falls_back_to_transponder(self):
        a = source("a", "u1", "SWR BW", identities=[
            ident(sid=28113, tsid=1101, onid=0, freq=11836000000, symbolrate=27500000, pol="H")])
        b = source("b", "u2", "SWR BW", identities=[
            ident(sid=28113, tsid=1101, onid=0, freq=11836400000, symbolrate=27500000, pol="H")])
        m = match.compare(a, b)
        self.assertEqual(m.level, match.LEVEL_TRANSPONDER)

    def test_same_sid_on_different_transponder_is_not_a_match(self):
        a = source("a", "u1", "Channel One", identities=[
            ident(sid=100, tsid=1, onid=0, freq=11493000000, symbolrate=29900000, pol="H")],
            epg=events(*self.SCHED_A))
        b = source("b", "u2", "Channel Two", identities=[
            ident(sid=100, tsid=1, onid=0, freq=11523000000, symbolrate=29900000, pol="H")],
            epg=events(*self.SCHED_B))
        self.assertIsNone(match.compare(a, b))

    def test_name_plus_agreeing_epg(self):
        a = source("a", "u1", "Sky Sport News", epg=events(*self.SCHED_A))
        b = source("b", "u2", "Sky Sport News", epg=events(*self.SCHED_A))
        self.assertEqual(match.compare(a, b).level, match.LEVEL_NAME_EPG)

    def test_name_plus_conflicting_epg_is_vetoed(self):
        a = source("a", "u1", "Kanal 5", epg=events(*self.SCHED_A))
        b = source("b", "u2", "Kanal 5", epg=events(*self.SCHED_B))
        m = match.compare(a, b)
        self.assertEqual(m.level, match.LEVEL_VETO)
        self.assertEqual(m.label, "epg-conflict")

    def test_name_only_when_no_epg_anywhere(self):
        a = source("a", "u1", "Bibel TV")
        b = source("b", "u2", "Bibel TV")
        self.assertEqual(match.compare(a, b).level, match.LEVEL_NAME_ONLY)

    def test_differing_countries_veto_a_name_match(self):
        a = source("a", "u1", "Kanal 5", country="SE")
        b = source("b", "u2", "Kanal 5", country="MK")
        m = match.compare(a, b)
        self.assertEqual(m.level, match.LEVEL_VETO)
        self.assertEqual(m.label, "country")

    def test_one_unknown_country_still_merges(self):
        a = source("a", "u1", "Bibel TV", country="DE")
        b = source("b", "u2", "Bibel TV", country=None)
        self.assertEqual(match.compare(a, b).level, match.LEVEL_NAME_ONLY)

    def test_hd_sd_service_types_veto_a_name_match(self):
        a = source("a", "u1", "Nickelodeon", identities=[ident(sid=1, servicetype=0x19)])
        b = source("b", "u2", "Nickelodeon", identities=[ident(sid=2, servicetype=0x01)])
        m = match.compare(a, b)
        self.assertEqual(m.level, match.LEVEL_VETO)
        self.assertEqual(m.label, "hd-sd")

    def test_full_triplets_that_disagree_veto_a_name_match(self):
        a = source("a", "u1", "BBC One", identities=[
            ident(sid=6301, tsid=2045, onid=9018, freq=10773000000, symbolrate=22000000)])
        b = source("b", "u2", "BBC One", identities=[
            ident(sid=6317, tsid=2045, onid=9018, freq=10773000000, symbolrate=22000000)])
        m = match.compare(a, b)
        self.assertEqual(m.level, match.LEVEL_VETO)
        self.assertEqual(m.label, "identity-conflict")

    def test_partial_epg_agreement_neither_merges_nor_vetoes(self):
        a = source("a", "u1", "Mixed", epg=events(
            (0, 30, "Same One"), (30, 30, "Different A"), (60, 30, "Also Different A")))
        b = source("b", "u2", "Mixed", epg=events(
            (0, 30, "Same One"), (30, 30, "Different B"), (60, 30, "Also Different B")))
        self.assertIsNone(match.compare(a, b))

    def test_tight_name_needs_epg_support(self):
        a = source("a", "u1", "TV 3", epg=events(*self.SCHED_A))
        b = source("b", "u2", "TV3", epg=events(*self.SCHED_A))
        self.assertEqual(match.compare(a, b).level, match.LEVEL_NAME_EPG)
        # …without EPG, the looser key alone is not enough.
        self.assertIsNone(match.compare(source("a", "u1", "TV 3"), source("b", "u2", "TV3")))

    def test_unrelated_channels(self):
        self.assertIsNone(match.compare(
            source("a", "u1", "Arte"), source("b", "u2", "Phoenix")))


class TestGrouping(unittest.TestCase):
    def sources(self):
        sched = ((0, 30, "Tagesschau"), (30, 60, "Tatort"), (90, 30, "Wetter"))
        return [
            source("a", "a1", "Das Erste HD", identities=[
                ident(sid=28106, tsid=1079, onid=1, freq=11836000000, symbolrate=27500000)]),
            source("b", "b1", "ARD HD", identities=[
                ident(sid=28106, tsid=1079, onid=1, freq=11836500000, symbolrate=27500000)]),
            source("a", "a2", "Sky Sport News", epg=events(*sched)),
            source("b", "b2", "Sky Sport News", epg=events(*sched)),
            source("a", "a3", "Bibel TV"),
            source("b", "b3", "Bibel TV"),
            source("a", "a4", "Regional Nord"),
            source("b", "b4", "Kanal 5", country="MK"),
            source("a", "a5", "Kanal 5", country="SE"),
        ]

    def labels(self, groups):
        return [[s.label for s in g] for g in groups]

    def test_expected_grouping(self):
        groups = match.group_channels(self.sources())
        self.assertEqual(self.labels(groups), [
            ["a:a1", "b:b1"],      # triplet
            ["a:a2", "b:b2"],      # name + EPG
            ["a:a3", "b:b3"],      # name only
            ["a:a4"],
            ["a:a5"],              # country veto keeps Kanal 5 apart
            ["b:b4"],
        ])

    def test_order_independent(self):
        expected = self.labels(match.group_channels(self.sources()))
        rng = random.Random(7)
        for _ in range(20):
            shuffled = self.sources()
            rng.shuffle(shuffled)
            self.assertEqual(self.labels(match.group_channels(shuffled)), expected)

    def test_never_merges_two_channels_from_one_server(self):
        srcs = [
            source("a", "a1", "Eurosport 1"),
            source("a", "a2", "Eurosport 1"),
            source("b", "b1", "Eurosport 1"),
        ]
        groups = match.group_channels(srcs)
        for g in groups:
            self.assertEqual(len({s.server_name for s in g}), len(g))
        self.assertEqual(sorted(len(g) for g in groups), [1, 2])

    def test_strong_match_wins_the_slot(self):
        # b1 matches a1 by triplet and a2 by name; the triplet must win, leaving
        # a2 unmerged rather than the other way round.
        srcs = [
            source("a", "a1", "Sender Eins", identities=[
                ident(sid=1, tsid=2, onid=3, freq=11836000000, symbolrate=27500000)]),
            source("a", "a2", "Kanal Zwei"),
            source("b", "b1", "Kanal Zwei", identities=[
                ident(sid=1, tsid=2, onid=3, freq=11836000000, symbolrate=27500000)]),
        ]
        groups = match.group_channels(srcs)
        self.assertEqual(self.labels(groups), [["a:a1", "b:b1"], ["a:a2"]])

    def test_log_reports_merges_and_vetoes(self):
        seen = []
        match.group_channels(self.sources(), log=lambda k, a, b, m: seen.append((k, m.label)))
        self.assertIn(("merge", "triplet"), seen)
        self.assertIn(("merge", "name+epg"), seen)
        self.assertIn(("merge", "name"), seen)
        self.assertIn(("veto", "country"), seen)


class TestHlsUuidAssignment(unittest.TestCase):
    def test_collisions_get_deterministic_suffixes(self):
        groups = [
            [source("a", "a1", "RTL")],
            [source("a", "a2", "RTL")],
            [source("a", "a3", "Arte")],
        ]
        self.assertEqual(match.assign_hls_uuids(groups), ["RTL", "RTL-2", "ARTE"])

    def test_assignment_independent_of_group_order(self):
        g1 = [source("a", "a1", "RTL")]
        g2 = [source("b", "b1", "RTL")]
        g3 = [source("a", "a2", "RTL")]
        forward = dict(zip(
            [g[0].label for g in (g1, g2, g3)], match.assign_hls_uuids([g1, g2, g3])))
        backward = dict(zip(
            [g[0].label for g in (g3, g2, g1)], match.assign_hls_uuids([g3, g2, g1])))
        self.assertEqual(forward, backward)
        # Order is by (server_order, tvh_uuid): a:a1, a:a2, then b:b1.
        self.assertEqual(forward, {"a:a1": "RTL", "a:a2": "RTL-2", "b:b1": "RTL-3"})

    def test_single_channel_keeps_the_plain_name(self):
        self.assertEqual(
            match.assign_hls_uuids([[source("a", "a1", "ProSieben MAXX")]]),
            ["PROSIEBEN_MAXX"])


if __name__ == "__main__":
    unittest.main()
