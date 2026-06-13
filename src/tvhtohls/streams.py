import math
import os
import subprocess
import time
import traceback

from .config import config, tvh_base_url_auth
from .flags import flag_emoji


def _abr_ladder():
    """Geometric series of (bitrate_bps, target_height) pairs, highest first.

    Bitrate ranges from config["min_bitrate"] to config["max_bitrate"];
    `config["num_streams"]` entries are produced. Heights are derived from
    bitrate via a simple bits-per-pixel-per-frame model (bpp≈0.085 @ 25 fps),
    then snapped to a multiple of 8 (h.264 likes even dimensions).
    """
    n = max(1, config["num_streams"])
    lo = max(1, config["min_bitrate"])
    hi = max(lo, config["max_bitrate"])
    if n == 1:
        bitrates = [hi]
    else:
        ratio = (hi / lo) ** (1.0 / (n - 1))
        bitrates = [int(lo * (ratio ** i)) for i in range(n)]
        bitrates[0] = lo   # force exact endpoints — geometric rounding may drift by 1
        bitrates[-1] = hi
    bitrates.sort(reverse=True)
    return [(b, _height_for_bitrate(b)) for b in bitrates]


def _height_for_bitrate(bps):
    # bits per pixel per frame ≈ 0.085 for h.264 at typical broadcast quality.
    pixels = bps / (25 * 0.085)
    h = math.sqrt(pixels * 9 / 16)            # 16:9 height
    return max(72, int(round(h / 8) * 8))     # round to a multiple of 8


def _scale_spec(scale_filter, target_h):
    """Scale filter that fits target_h height with source aspect, never upscaling.

    `min(target_h, ih)` clamps to source height; the width is derived from
    source aspect so the picture isn't stretched, rounded down to even.
    """
    return (
        "%s=w='trunc(min(%d,ih)*iw/ih/2)*2':h='min(%d,ih)'"
        % (scale_filter, target_h, target_h)
    )


def build_codecs():
    """Build the per-output ffmpeg args using a shared filter graph.

    Returns (hwaccel_args, video_args, n_outputs, var_stream_map):
      - hwaccel_args: input-side flags (before -i)
      - video_args: -filter_complex + per-output -map / -c:v / -b:v sequences,
        ending with a 'copy' output that packet-copies the source video
      - n_outputs: total number of video outputs (transcoded + copy)
      - var_stream_map: value for ffmpeg's -var_stream_map (HLS master)

    A single `-filter_complex` graph decodes & deinterlaces the source *once*,
    then splits the result to N parallel scalers — one per transcoded variant.
    Compared to the previous per-variant `-filter:v:N` args, this halves the
    decode/deinterlace work and keeps one less copy of the frame in GPU memory.
    """
    hwaccel = config["hwaccel"] == "vaapi"
    if hwaccel:
        deinterlace = "deinterlace_vaapi"
        scale_filter = "scale_vaapi"
        encoder = "h264_vaapi"
        hwaccel_args = [
            "-hwaccel", "vaapi",
            "-vaapi_device", config["vaapi_device"],
            "-hwaccel_output_format", "vaapi",
        ]
    else:
        deinterlace = "yadif"
        scale_filter = "scale"
        encoder = "libx264"
        hwaccel_args = []

    ladder = _abr_ladder()
    n_scaled = len(ladder)

    # Filter graph: decode + deinterlace once, then split into per-variant scalers.
    if n_scaled == 1:
        # split=1 is a degenerate no-op; build a single chain instead.
        target_h = ladder[0][1]
        filter_complex = "[0:v]%s,%s[v0]" % (
            deinterlace, _scale_spec(scale_filter, target_h),
        )
    else:
        split_outputs = "".join("[s%d]" % i for i in range(n_scaled))
        chains = ["[0:v]%s,split=%d%s" % (deinterlace, n_scaled, split_outputs)]
        for i, (_, target_h) in enumerate(ladder):
            chains.append("[s%d]%s[v%d]" % (i, _scale_spec(scale_filter, target_h), i))
        filter_complex = ";".join(chains)

    video_args = ["-filter_complex", filter_complex]
    for i, (bps, _) in enumerate(ladder):
        video_args += [
            "-map", "[v%d]" % i,
            "-c:v:%d" % i, encoder,
            "-b:v:%d" % i, str(bps),
        ]
    # Stream-copy variant: packet-copies the source so no GPU/CPU encoding needed.
    copy_idx = n_scaled
    video_args += ["-map", "0:v:0", "-c:v:%d" % copy_idx, "copy"]
    n_outputs = n_scaled + 1

    var_stream_map = ", ".join(
        "v:%d,a:%d" % (i, i) for i in range(n_outputs)
    ) + ", "

    return hwaccel_args, video_args, n_outputs, var_stream_map


class TVChannel:
    def __init__(self, name, tags, number, tvh_uuid, hls_uuid, *, country=None, provider=None):
        self.name = name
        self.tags = tags
        self.number = number
        self.tvh_uuid = tvh_uuid
        self.hls_uuid = hls_uuid
        self.country = country
        self.flag = flag_emoji(country)
        self.provider = provider
        self.tvh_url = tvh_base_url_auth + "stream/channel/" + tvh_uuid
        self.m3u8_file = config["hls_local_path"] + "/" + self.hls_uuid + ".m3u8"
        self.stream = None
        self.last_used = time.time()
        self.clean_stream()

    def start_stream(self):
        self.last_used = time.time()
        if self.stream:
            if os.path.isfile(self.m3u8_file):
                return "stream.m3u8?uuid=" + self.hls_uuid
            else:
                if self.stream.poll() is None:
                    return False
                # ffmpeg exited before producing a playlist; clean up before respawn
                self.clean_stream()

        hwaccel_args, video_args, n_outputs, var_stream_map = build_codecs()
        acodec_params = ["-map", "a:0"] * n_outputs

        self.stream = subprocess.Popen(
            ["/usr/bin/ffmpeg"] + hwaccel_args + [
                "-i", self.tvh_url,
                "-preset", "veryfast",
                "-sc_threshold", "0",
                # Force a keyframe every 25 frames (~1 s at 25 fps) so the HLS muxer
                # can split segments quickly. Without this, libx264's default GOP of
                # 250 frames forced 10 s segments and slow startup.
                "-g", "25",
            ] + video_args + acodec_params + [
                "-c:a", "aac", "-b:a", "96k", "-ac", "2",
                "-f", "hls",
                "-r", "25", "-sn",
                "-hls_flags", "delete_segments+independent_segments",
                "-hls_segment_filename",
                config["hls_local_path"] + "/" + self.hls_uuid + "_%v_%02d.ts",
                "-hls_list_size", "10",
                "-hls_time", str(config["segment_len"]),
                "-hls_playlist_type", "event",
                "-master_pl_name", self.hls_uuid + ".m3u8",
                "-var_stream_map", var_stream_map,
                self.m3u8_file + "+%v",
            ]
        )
        self.last_used = time.time()
        return False

    def clean_stream(self):
        base = config["hls_local_path"]
        for f in os.listdir(base):
            if f.startswith(self.hls_uuid):
                os.remove(base + "/" + f)
        self.stream = None


def check_status(channel_list, epg, main_thread):
    """Background thread: kill ffmpeg processes idle >30s and keep EPG fresh."""
    while main_thread.is_alive():
        time.sleep(1)
        try:
            for channel in channel_list:
                if channel.stream is None:
                    continue
                if time.time() - channel.last_used > 30:
                    channel.stream.kill()
                    time.sleep(1)
                if channel.stream.poll() is None:
                    continue
                channel.clean_stream()
            for channel in channel_list:
                if channel.tvh_uuid in epg:
                    epg[channel.tvh_uuid].update()
        except Exception:
            traceback.print_exc()
