import ctypes
import math
import os
import re
import signal
import subprocess
import time
import traceback

from .config import config
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


def _hls_tail(channel, var_stream_map):
    """The shared ffmpeg HLS muxing arguments used by both pipelines."""
    return [
        "-f", "hls", "-sn",
        "-hls_flags", "delete_segments+independent_segments",
        "-hls_segment_filename",
        config["hls_local_path"] + "/" + channel.hls_uuid + "_%v_%02d.ts",
        "-hls_list_size", "10",
        "-hls_time", str(config["segment_len"]),
        "-hls_playlist_type", "event",
        "-master_pl_name", channel.hls_uuid + ".m3u8",
        "-var_stream_map", var_stream_map,
        channel.m3u8_file + "+%v",
    ]


def _drawtext_escape(path):
    """Escape a path for use as a drawtext filter option value.

    Inside the filter graph, ':' separates options and '\\' escapes; the
    textfile path we pass is uuid-derived but the font path may contain neither.
    """
    return path.replace("\\", "\\\\").replace(":", "\\:")


def build_audio_only_command(channel):
    """ffmpeg command for an audio-only service: synthesize a low-bandwidth black
    video carrying the channel name, muxed with the source audio into one HLS variant.
    """
    fps = config["dummy_video_fps"]
    gop = str(max(1, int(round(fps * config["segment_len"]))))
    name_file = config["hls_local_path"] + "/" + channel.hls_uuid + ".txt"
    drawtext = (
        "[0:v]drawtext="
        "fontfile=" + _drawtext_escape(config["dummy_video_font"])
        + ":textfile=" + _drawtext_escape(name_file)
        + ":expansion=none:fontcolor=white:fontsize=18"
        + ":x=(w-text_w)/2:y=(h-text_h)/2[v]"
    )
    return ["/usr/bin/ffmpeg",
        "-f", "lavfi", "-re",
        "-i", "color=c=black:s=%s:r=%s" % (config["dummy_video_size"], fps),
        "-i", channel.tvh_url,
        "-filter_complex", drawtext,
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-b:v", str(config["dummy_video_bitrate"]), "-pix_fmt", "yuv420p",
        "-r", str(fps), "-g", gop, "-keyint_min", gop, "-sc_threshold", "0",
        "-map", "1:a:0", "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        "-shortest",
    ] + _hls_tail(channel, "v:0,a:0, ")


# Ask the kernel to kill our ffmpeg children if we die without being able to do
# it ourselves ("kill -9", OOM, a crash). Without this they keep running, keep a
# TVHeadend tuner busy and keep writing segments nobody serves.
_PR_SET_PDEATHSIG = 1
try:
    _prctl = ctypes.CDLL("libc.so.6", use_errno=True).prctl
except (OSError, AttributeError):      # non-Linux / no glibc: fall back to cleanup only
    _prctl = None


def _die_with_parent():
    """Child-side pre-exec hook: SIGKILL this child when the app goes away.

    The kernel ties this to the *thread* that forked, which here is always the
    uvicorn main thread (playlist requests run in it), i.e. the process lifetime.
    Survives the exec into ffmpeg.

    Runs in the forked child before exec, which is the documented (and slightly
    risky) preexec_fn window: it does one prctl(2) syscall and nothing else. If
    it ever did wedge, the start deadline in start_stream() reaps that ffmpeg and
    the channel fails over, so the failure mode is self-healing.
    """
    if _prctl is not None:
        _prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)


IDLE_TIMEOUT = 30            # kill ffmpeg after this many seconds without a request
SOURCE_COOLDOWN = 300        # skip a source for this long after it failed
# Startup deadlines. Tuning a transponder, waiting for lock, PAT/PMT and the
# first keyframe can take well over 20 s on a real receiver, so the "is this
# ffmpeg stuck?" test is progress-based: a source with no free tuner sends no
# bytes at all, so nothing lands on disk. Once segments appear we only need to
# wait for the master playlist.
START_DEADLINE = 45          # seconds to produce any output at all
PLAYLIST_DEADLINE = 90       # seconds from the first segment to the master playlist
RETRY_INTERVAL = 15          # min seconds between attempts once every source has failed


class StreamSource:
    """One server's copy of a channel: where to pull it from, and how healthy it is."""

    def __init__(self, server, tvh_uuid, url, audio_only=False):
        self.server = server
        self.tvh_uuid = tvh_uuid
        self.url = url
        # Audio-only is per source: a server with no stream metadata classifies the
        # same channel as normal TV, and the two need different ffmpeg pipelines.
        self.audio_only = audio_only
        self.cooldown_until = 0.0
        self.fail_count = 0

    @property
    def label(self):
        return "%s:%s" % (getattr(self.server, "name", "?"), self.tvh_uuid)

    def __repr__(self):
        return "<StreamSource %s>" % self.label


class TVChannel:
    """One channel as served: one HLS id, one ffmpeg, N interchangeable sources."""

    def __init__(self, name, tags, number, hls_uuid, sources, *,
                 country=None, provider=None):
        self.name = name
        self.tags = tags
        self.number = number
        self.hls_uuid = hls_uuid
        # Ordered by configured server priority: index 0 is tried first, the rest
        # are failover targets.
        self.sources = list(sources)
        self.source_index = 0
        self.country = country
        self.flag = flag_emoji(country)
        self.provider = provider
        # Set once the channel is seen to have EPG (at startup or later). Flagged
        # channels get fresh EPG fetched when they run out of events; channels
        # that never had EPG are left alone so we don't poll TVHeadend for nothing.
        self.had_epg = False
        self.m3u8_file = config["hls_local_path"] + "/" + self.hls_uuid + ".m3u8"
        self.stream = None
        self.stream_started_at = None
        self.last_used = time.time()
        self.last_attempt = 0.0
        self.clean_stream()

    @property
    def source(self):
        return self.sources[self.source_index]

    @property
    def tvh_uuid(self):
        return self.source.tvh_uuid

    @property
    def tvh_url(self):
        return self.source.url

    @property
    def audio_only(self):
        return self.source.audio_only

    def _pick_source(self, now):
        """Index of the highest-priority source that isn't cooling down."""
        for i, s in enumerate(self.sources):
            if s.cooldown_until <= now:
                return i
        # Everything failed recently — retry whichever recovers first.
        return min(range(len(self.sources)),
                   key=lambda i: self.sources[i].cooldown_until)

    def _has_output(self):
        """True once ffmpeg has written a segment or playlist for this channel."""
        try:
            entries = os.listdir(config["hls_local_path"])
        except OSError:
            return False
        for f in entries:
            if f.endswith(".txt"):
                continue        # the drawtext name file — we write that ourselves
            if _owns_file(self.hls_uuid, f):
                return True
        return False

    def startup_failure(self, now):
        """Why to give up on the running ffmpeg, or None while it may still work."""
        waited = now - (self.stream_started_at or now)
        if self._has_output():
            if waited > PLAYLIST_DEADLINE:
                return "no playlist within %ds" % PLAYLIST_DEADLINE
            return None
        if waited > START_DEADLINE:
            return "no data within %ds" % START_DEADLINE
        return None

    def fail_source(self, reason):
        """Retire the current source for a while and tear its ffmpeg down."""
        source = self.source
        source.fail_count += 1
        source.cooldown_until = time.time() + SOURCE_COOLDOWN
        print("stream %s: source %s failed (%s); skipping it for %ds"
              % (self.hls_uuid, source.label, reason, SOURCE_COOLDOWN))
        # Reap before deleting files, so a dying ffmpeg can't write one more
        # segment after the cleanup.
        self.stop_stream()

    def start_stream(self):
        self.last_used = time.time()
        if self.stream:
            if os.path.isfile(self.m3u8_file):
                return "stream.m3u8?uuid=" + self.hls_uuid
            if self.stream.poll() is None:
                reason = self.startup_failure(self.last_used)
                if reason is None:
                    return False
                # A TVHeadend with no free tuner answers the HTTP request and then
                # sends nothing, so ffmpeg neither exits nor writes a playlist.
                self.fail_source(reason)
            else:
                # ffmpeg exited before producing a playlist; clean up before respawn
                self.fail_source("exited before playlist")

        now = time.time()
        self.source_index = self._pick_source(now)
        source = self.source
        if source.cooldown_until > now and now - self.last_attempt < RETRY_INTERVAL:
            # Every source failed recently. Still retry eventually — the failure may
            # have been a busy tuner — but not once per two-second player poll.
            return False
        self.last_attempt = now
        if len(self.sources) > 1:
            print("stream %s: starting from %s" % (self.hls_uuid, source.label))

        if source.audio_only:
            # drawtext reads the channel name from this file (textfile=).
            with open(config["hls_local_path"] + "/" + self.hls_uuid + ".txt", "w") as f:
                f.write(self.name)
            cmd = build_audio_only_command(self)
        else:
            hwaccel_args, video_args, n_outputs, var_stream_map = build_codecs()
            acodec_params = ["-map", "a:0"] * n_outputs
            cmd = ["/usr/bin/ffmpeg"] + hwaccel_args + [
                "-i", source.url,
                "-preset", "veryfast",
                "-sc_threshold", "0",
                # Force a keyframe every 25 frames (~1 s at 25 fps) so the HLS muxer
                # can split segments quickly. Without this, libx264's default GOP of
                # 250 frames forced 10 s segments and slow startup.
                "-g", "25",
            ] + video_args + acodec_params + [
                "-c:a", "aac", "-b:a", "96k", "-ac", "2",
                "-r", "25",
            ] + _hls_tail(self, var_stream_map)

        self.stream = subprocess.Popen(cmd, preexec_fn=_die_with_parent)
        self.stream_started_at = time.time()
        self.last_used = self.stream_started_at
        return False

    def stop_stream(self):
        """Stop ffmpeg (if any) and remove this channel's files."""
        if self.stream is not None:
            if self.stream.poll() is None:
                self.stream.kill()
            try:
                self.stream.wait(timeout=5)
            except Exception:
                pass
        self.clean_stream()

    def clean_stream(self):
        base = config["hls_local_path"]
        for f in os.listdir(base):
            if _owns_file(self.hls_uuid, f):
                try:
                    os.remove(base + "/" + f)
                except OSError:
                    pass
        self.stream = None
        self.stream_started_at = None


ORPHAN_SWEEP_INTERVAL = 60   # seconds between directory-wide orphan sweeps
ORPHAN_GRACE_SECONDS = 15    # skip files younger than this (mid-start safety margin)


_OWNED_SUFFIX_RE = re.compile(r"\A(\.m3u8(\+\d+)?|\.txt|_\d+_\d+\.ts)\Z")


def _owns_file(hls_uuid, filename):
    """True if `filename` is one of the files start_stream() generates for `hls_uuid`.

    A bare startswith() would also match a *different* channel whose uuid
    happens to be a string prefix of this one (e.g. "PROSIEBEN" is a prefix of
    "PROSIEBEN_MAXX", and "ARD" of "ARD-alpha" -> "ARDALPHA") — check the
    remainder against the exact suffixes our own filenames use instead.
    """
    if not filename.startswith(hls_uuid):
        return False
    return bool(_OWNED_SUFFIX_RE.match(filename[len(hls_uuid):]))


def _sweep_orphan_files(channel_list):
    """Delete any file in hls_local_path not owned by a currently-running channel.

    Backstop for files the per-channel cleanup didn't catch: a renamed/removed
    channel's stale hls_uuid files, or any other gap.
    """
    base = config["hls_local_path"]
    now = time.time()
    try:
        entries = os.listdir(base)
    except OSError:
        return
    for f in entries:
        owned = any(ch.stream is not None and _owns_file(ch.hls_uuid, f) for ch in channel_list)
        if owned:
            continue
        path = base + "/" + f
        try:
            if now - os.path.getmtime(path) < ORPHAN_GRACE_SECONDS:
                continue
            os.remove(path)
        except OSError:
            pass


def kill_all_streams(channel_list):
    """Stop every running ffmpeg and clean up. Called when the app shuts down."""
    running = [ch for ch in channel_list if ch.stream is not None]
    if not running:
        return
    print("shutdown: stopping %d ffmpeg process(es)" % len(running))
    for channel in running:
        try:
            channel.stop_stream()
        except Exception:
            traceback.print_exc()


def check_status(channel_list, epg, main_thread):
    """Background thread: reap idle/failed ffmpeg processes and keep EPG fresh."""
    tick = 0
    while main_thread.is_alive():
        time.sleep(1)
        tick += 1
        try:
            for channel in channel_list:
                if channel.stream is None:
                    continue
                now = time.time()
                wanted = now - channel.last_used <= IDLE_TIMEOUT
                alive = channel.stream.poll() is None
                if alive and wanted:
                    if not os.path.isfile(channel.m3u8_file):
                        # Hung ffmpeg (typically a busy tuner): retire the source so
                        # the next request fails over instead of waiting forever.
                        reason = channel.startup_failure(now)
                        if reason:
                            channel.fail_source(reason)
                    continue
                if alive:
                    # Nobody is watching any more: stop and reap it.
                    channel.stop_stream()
                    continue
                if wanted:
                    # Exited while someone was still watching: blame the source.
                    channel.fail_source(
                        "died mid-stream" if os.path.isfile(channel.m3u8_file)
                        else "exited before playlist")
                    continue
                channel.clean_stream()
            for channel in channel_list:
                feed = epg.get(channel.hls_uuid)
                if feed is None:
                    continue
                if feed.has_events():
                    # Flag any channel we've ever seen carry EPG.
                    channel.had_epg = True
                if channel.had_epg:
                    # Keep it fresh; refetches (throttled) once it runs dry.
                    feed.update()
            if tick % ORPHAN_SWEEP_INTERVAL == 0:
                _sweep_orphan_files(channel_list)
        except Exception:
            traceback.print_exc()
