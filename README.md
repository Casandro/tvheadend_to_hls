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

## Optional settings

All of these can be set in `.env` as well.

- `top_channels` — comma-separated list of TVHeadend channel UUIDs (the `uuid` field returned by `/api/channel/grid`). Channels listed here are pinned to the top of the channel list in the order given, regardless of the current sort. Unknown UUIDs are silently dropped. Example: `top_channels=abc123...,def456...`.

- `top_channels_<name>` — define additional named lists. Each one becomes selectable from the top of `/` and via `/?list=<name>`. Example: `top_channels_evening=uuid1,uuid2` and `top_channels_kids=uuid3,uuid4`. With `compose.yaml`'s `env_file: .env`, any vars you add to `.env` are forwarded — no need to enumerate each new list in `compose.yaml`.

  To produce a list of UUIDs to choose from, run `./list_channels.py` from the project root with your TVHeadend creds in the environment. It prints `UUID<TAB>number<TAB>name`, one channel per line. Pipe through `grep`, `awk`, etc. to extract the UUIDs you want.

- `min_bitrate`, `max_bitrate`, `num_streams` — define the ABR ladder. The
  app produces `num_streams` transcoded variants in a geometric series between
  `min_bitrate` (lowest) and `max_bitrate` (highest), plus a stream-copy
  variant carrying the original quality. Bitrate values accept `100k`, `3M`,
  or plain bits/second (e.g. `1500000`). Defaults: `min_bitrate=100k`,
  `max_bitrate=3M`, `num_streams=4`. Each variant's target height is derived
  from its bitrate (≈0.085 bits/pixel/frame at 25 fps) and is always capped at
  the source height — no upscaling, ever. Source aspect ratio is preserved.

- `hwaccel` — controls Intel/AMD GPU encoding (ffmpeg's `h264_vaapi`).
  - Unset / `auto` (default): probe `vaapi_device`; use VAAPI if reachable, else CPU.
  - `vaapi`: force VAAPI without probing (the old explicit setting still works).
  - `none` / `cpu` / `off`: force CPU `libx264` even on a GPU host.
  At startup the app prints which path it chose.

- `vaapi_device` — render node to probe / use. Default `/dev/dri/renderD128`.

The default `compose.yaml` passes `/dev/dri` into the container so the auto-detect can find the GPU. If your host has no GPU and that mapping fails at `docker compose up`, delete the `devices:` and `group_add:` stanzas in `compose.yaml`.
