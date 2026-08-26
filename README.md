# tvheadend_to_hls
A small web service that exports tvheadend services via HLS

# How to set this up

1. Setup [tvheadend](https://tvheadend.org/)
2. Map channels either manually or via the automated mapping.
3. Create a user and allow it to stream. Be sure to limit the IP adresses to the machien you run `tvheadend_to_hls` on.
4. Create a `.env`-File in the directory you downloaded `tvheadend_to_hls` to configure it.
```
tvheadend_user=username
tvheadend_pass=password
tvheadend_ip=ip
```
   To combine the channels of several TVHeadend servers into one list, use
   `tvheadend_servers` instead — see [Multiple TVHeadend servers](#multiple-tvheadend-servers).
5. In the directory you have gotten `tvheadend_to_hls`, run `docker-compose build` and then `docker-compose up` to run it.


## I don't like Docker
This is fine. The src subdirectory contains a Python program which acts as a web server and calls ffmpeg to create the files for the stream. It should also serve those files. This is probably not wise, as a dedicated webserver likely is faster.

### I want to scale this

This is not meant to be scalable. This is a small personal project to provide, for example, TV to a small dormatory or community.

Make sure you understand your bottleneck. If you are dealing with many different channels that are being streamed, make sure the encoder has enough oompf to handle it or lower the encoding complexity. 

If your stream the same channel to a lot of people, just install a caching proxy in front of it.



# Example configuration:

```
tvheadend_user=user
tvheadend_pass=pass
tvheadend_ip=ip
```

## Multiple TVHeadend servers

Set `tvheadend_servers` to a JSON array to pull channels from several TVHeadend
instances at once, each with its own credentials. The channel lists are combined
and channels that exist on more than one server are merged into a single entry.

```
tvheadend_servers=[{"name":"attic","ip":"192.168.5.5","user":"alice","pass":"s3cr3t"},{"name":"cellar","ip":"192.168.5.9","port":"9981","user":"bob","pass":"hunter2"}]
```

Per entry: `ip` (required), `user` and `pass` (required), `port` (default
`9981`), `name` (default: the IP; used in log messages). Keep the whole value on
**one line** and don't append a `#` comment — `.env` is parsed line by line, not
as shell syntax.

`tvheadend_servers` is opt-in. When it is unset, the flat `tvheadend_ip` /
`tvheadend_port` / `tvheadend_user` / `tvheadend_pass` settings above define the
single server, exactly as before. When it *is* set, the flat settings are
ignored — the two formats are never mixed.

Order matters: a merged channel is streamed from the first server in the list
that has it. If that server's ffmpeg fails to produce a playlist (offline, wrong
credentials, no free tuner), the next server providing the same channel is used
and the failed one is skipped for five minutes.

### How duplicates are detected

At startup, every channel of every server is compared against the channels of
the *other* servers (two channels on the same server are never merged). Any one
of these is enough to merge:

- the same DVB service identity — original network id, transport stream id and
  service id, which are identical on every receiver of the same transponder;
- the same identity minus the network id (TVHeadend often reports it as 0),
  confirmed by the two muxes being the same transponder — same delivery system,
  same polarisation, and a frequency difference well below the symbol rate, so
  slightly different scan results still match while neighbouring transponders
  don't;
- the same channel name plus agreeing EPG (same programme start times and
  titles);
- the same channel name with no EPG anywhere to corroborate or contradict it.

The last two are dropped again when something contradicts them: clearly
disagreeing EPG (this is what keeps a `+1` timeshift channel separate), DVB
identities that are fully known on both sides and differ (regional variants),
an HD service against an SD one, or two different guessed countries (so a
Swedish "Kanal 5" is not merged with a Macedonian one).

Note that resolutions stay distinct: "ARD" and "ARD HD" are different channels,
and are not merged with each other. Channels that appear several times on a
*single* server (the same programme from different satellites, say) also stay
separate — TVHeadend already decides what one channel is over there.

The DVB-identity signals need `/api/mpegts/service/grid` and
`/api/mpegts/mux/grid`, which require an API user with web-interface rights, not
just streaming rights. Without them the app still works and falls back to
name+EPG matching; the startup log says which server ended up in which mode.

### Reviewing what got merged

Every merge and every rejection is printed at startup. To review them without
starting the service, set `merge_report_only=1`: the report is printed and the
process exits before the web server (and any ffmpeg) starts.

```
tvheadend: 2 server(s): attic(192.168.5.5:9981), cellar(192.168.5.9:9981)
attic:  1716 channels, 6472 services, 672 muxes, 587 EPG feeds, identity=yes
cellar: 1690 channels, 6398 services, 668 muxes, 583 EPG feeds, identity=yes
merge  triplet      "Das Erste HD"   attic:a1b2… + cellar:c3d4…  (onid=1 tsid=1079 sid=28006)
merge  name+epg     "Sky Sport News" attic:…     + cellar:…      (agreement=0.83)
merge  name         "Bibel TV"       attic:…     + cellar:…      (no EPG on either side)
veto   epg-conflict "Kanal 5"        attic:…     vs cellar:…     (agreement=0.04)
dedup: 1946 sources -> 973 channels (973 collapsed); attic 973, cellar 973
Loaded 973 TV channels, 411 with EPG
```

A `veto` line means two candidates were *not* merged, and why. The weakest tier
is `merge  name` — those are the lines worth eyeballing after adding a server.

### When a server is unavailable

A server that can't be reached at startup is logged and skipped; the others are
still served. Channels only that server had are simply missing until the next
restart (channel lists are read once at startup).

While streaming, a source that fails — offline, wrong credentials, no free tuner
— is reported, skipped for five minutes, and the next server carrying the same
channel takes over. The player picks this up on its own without a reload, and
the HLS URL of a channel never changes, whichever server it currently comes
from.

## Optional settings

All of these can be set in `.env` as well.

- `top_channels` — comma-separated list of TVHeadend channel UUIDs (the `uuid` field returned by `/api/channel/grid`). Channels listed here are pinned to the top of the channel list in the order given, regardless of the current sort. Unknown UUIDs are silently dropped. Example: `top_channels=abc123...,def456...`. With several servers a UUID from any of them works; when the same UUID exists on two servers, use `servername:uuid` to disambiguate.

- `top_channels_<name>` — define additional named lists. Each one becomes selectable from the top of `/` and via `/?list=<name>`. Example: `top_channels_evening=uuid1,uuid2` and `top_channels_kids=uuid3,uuid4`. With `compose.yaml`'s `env_file: .env`, any vars you add to `.env` are forwarded — no need to enumerate each new list in `compose.yaml`.

  To produce a list of UUIDs to choose from, run `./list_channels.py` from the project root with your TVHeadend creds in the environment. It prints `UUID<TAB>number<TAB>name`, one channel per line (prefixed by the server name when `tvheadend_servers` lists more than one). Pipe through `grep`, `awk`, etc. to extract the UUIDs you want.

- `min_bitrate`, `max_bitrate`, `num_streams` — define the ABR ladder. The
  app produces `num_streams` transcoded variants in a geometric series between
  `min_bitrate` (lowest) and `max_bitrate` (highest), plus a stream-copy
  variant carrying the original quality. Bitrate values accept `100k`, `3M`,
  or plain bits/second (e.g. `1500000`). Defaults: `min_bitrate=100k`,
  `max_bitrate=3M`, `num_streams=4`. Each variant's target height is derived
  from its bitrate (≈0.085 bits/pixel/frame at 25 fps) and is always capped at
  the source height — no upscaling, ever. Source aspect ratio is preserved.

- `merge_report_only` — set to `1` to print the multi-server channel merge
  report at startup and exit instead of serving anything. Useful for reviewing
  which channels got merged after changing the server list.

- `hwaccel` — controls Intel/AMD GPU encoding (ffmpeg's `h264_vaapi`).
  - Unset / `auto` (default): probe `vaapi_device`; use VAAPI if reachable, else CPU.
  - `vaapi`: force VAAPI without probing (the old explicit setting still works).
  - `none` / `cpu` / `off`: force CPU `libx264` even on a GPU host.
  At startup the app prints which path it chose.

- `vaapi_device` — render node to probe / use. Default `/dev/dri/renderD128`.

- `include_radio` — set to `true` (or `1`/`yes`/`on`) to list radio / audio-only
  channels, which are hidden by default. Audio-only services have no video, so
  the app synthesizes a low-bandwidth black video carrying the channel name
  (using `drawtext`) and muxes it with the source audio — this keeps the normal
  HLS pipeline and the `<video>` player working unchanged. Such channels are
  marked with 📻 in the list. The same dummy-video path is used for any service
  that has audio but no video, even if it isn't tagged as radio.

- `dummy_video_size`, `dummy_video_fps`, `dummy_video_bitrate`, `dummy_video_font`
  — tune the synthesized video for audio-only channels. Defaults: `320x180`,
  `10` fps, `64k` (accepts `100k`/`3M`/plain bps), and the DejaVu Sans font
  shipped in the image (`/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`).

The default `compose.yaml` passes `/dev/dri` into the container so the auto-detect can find the GPU. If your host has no GPU and that mapping fails at `docker compose up`, delete the `devices:` and `group_add:` stanzas in `compose.yaml`.

## Development

The channel-matching logic (name/EPG/DVB-identity comparison, merge grouping)
has unit tests that need neither a TVHeadend nor any dependency beyond the
standard library:

```bash
python3 -m unittest discover -s tests -v
```
