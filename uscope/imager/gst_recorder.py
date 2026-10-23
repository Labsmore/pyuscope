"""
Record the live video feed to a file without disturbing other tee consumers

While recording a branch is attached to a running tee:
    tee ! queue (leaky) ! videoconvert ! videoscale ! capsfilter ! encoder [! parser] ! mux ! filesink
    [audiosrc ! audioconvert ! audioresample ! queue ! encoder [! parser] ! mux]

Stopping unlinks the branch from the tee and sends EOS through only that branch
so the muxer can finalize the file (ex: mp4 moov atom) before the branch is removed
"""

from uscope.gst_util import Gst
from gi.repository import GLib

import os
import threading

# (factory, properties) in order of preference
# Properties are strings set with Gst.util_set_object_arg
VIDEO_ENCODERS = {
    "h264": [
        ("x264enc", {
            "speed-preset": "superfast",
            # No lookahead: lower latency and much less memory at high res
            "tune": "zerolatency",
            "pass": "qual",
            "quantizer": "21",
            "key-int-max": "60",
        }),
        ("openh264enc", {
            "complexity": "low",
            "rate-control": "bitrate",
            "bitrate": "20000000",
        }),
        ("vaapih264enc", {}),
        ("avenc_h264_omx", {}),
    ],
    "vp8": [
        ("vp8enc", {
            "deadline": "1",
            "cpu-used": "8",
            "target-bitrate": "20000000",
        }),
    ],
}
VIDEO_PARSERS = {
    "h264": "h264parse",
    "vp8": None,
}
AUDIO_ENCODERS = {
    "aac": [("avenc_aac", {}), ("voaacenc", {}), ("fdkaacenc", {})],
    "mp3": [("lamemp3enc", {})],
    "opus": [("opusenc", {})],
    "vorbis": [("vorbisenc", {})],
}
AUDIO_PARSERS = {
    "aac": "aacparse",
    "mp3": "mpegaudioparse",
    "opus": None,
    "vorbis": None,
}
# container: (muxer, extension, video codecs, audio codecs)
CONTAINERS = {
    "mp4": ("mp4mux", ".mp4", ("h264", ), ("aac", "mp3")),
    "mkv": ("matroskamux", ".mkv", ("h264", "vp8"), ("opus", "vorbis", "aac",
                                                       "mp3")),
}
# Try the requested container first
CONTAINER_FALLBACK = ("mp4", "mkv")


class RecordError(Exception):
    pass


def have_element(factory):
    return Gst.ElementFactory.find(factory) is not None


def _first_available(candidates, has_element):
    for factory, props in candidates:
        if has_element(factory):
            return factory, props
    return None, None


def plan_recording(container="mp4", audio=False, has_element=have_element):
    """
    Pick muxer + encoders for a recording
    If the requested container can't be satisfied fall back to another one

    Returns dict with keys:
    container, extension, mux, video_encoder, video_encoder_props, video_parser,
    audio_encoder, audio_encoder_props, audio_parser (audio_* None if not audio)
    Raises RecordError if nothing usable is installed
    """
    if container not in CONTAINERS:
        raise RecordError(f"Unknown video container {container}")
    containers = [container
                  ] + [c for c in CONTAINER_FALLBACK if c != container]
    problems = []
    for container in containers:
        mux, extension, vcodecs, acodecs = CONTAINERS[container]
        if not has_element(mux):
            problems.append(f"{container}: missing muxer {mux}")
            continue
        ret = {
            "container": container,
            "extension": extension,
            "mux": mux,
            "audio_encoder": None,
            "audio_encoder_props": None,
            "audio_parser": None,
        }
        for vcodec in vcodecs:
            factory, props = _first_available(VIDEO_ENCODERS[vcodec],
                                              has_element)
            parser = VIDEO_PARSERS[vcodec]
            if factory and (parser is None or has_element(parser)):
                ret["video_encoder"] = factory
                ret["video_encoder_props"] = dict(props)
                ret["video_parser"] = parser
                break
        else:
            problems.append(f"{container}: no video encoder for " +
                            "/".join(vcodecs))
            continue
        if audio:
            for acodec in acodecs:
                factory, props = _first_available(AUDIO_ENCODERS[acodec],
                                                  has_element)
                parser = AUDIO_PARSERS[acodec]
                if factory and (parser is None or has_element(parser)):
                    ret["audio_encoder"] = factory
                    ret["audio_encoder_props"] = dict(props)
                    ret["audio_parser"] = parser
                    break
            else:
                problems.append(f"{container}: no audio encoder for " +
                                "/".join(acodecs))
                continue
        return ret
    raise RecordError("No usable GStreamer video recording elements (" +
                      "; ".join(problems) + ")")


def fit_wh(width, height, max_wh=None):
    """
    Scale (width, height) down to fit in max_wh, keeping aspect ratio
    Encoders want even dimensions (4:2:0 chroma subsampling)
    """
    if max_wh:
        max_w, max_h = max_wh
        scale = min(1.0, max_w / width, max_h / height)
        width = int(width * scale)
        height = int(height * scale)
    width = max(2, width - width % 2)
    height = max(2, height - height % 2)
    return width, height


def make_element(factory, props=None, name=None):
    element = Gst.ElementFactory.make(factory, name)
    if element is None:
        raise RecordError(f"Failed to create GStreamer element {factory}")
    for k, v in (props or {}).items():
        Gst.util_set_object_arg(element, k, str(v))
    return element


class RecordingBin(Gst.Bin):
    """
    Keeps recording errors from reaching the pipeline bus
    (the video pipeline shuts down on any error)
    and reports when the file has been finalized
    """
    def __init__(self, on_error, on_eos):
        super().__init__()
        self.on_error = on_error
        self.on_eos = on_eos
        self.filesink = None

    def do_handle_message(self, message):
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.on_error(f"{err} ({debug})")
            return
        if message.type == Gst.MessageType.EOS and message.src == self.filesink:
            self.on_eos()
        Gst.Bin.do_handle_message(self, message)


class GstVideoRecorder:
    """
    Records video from a tee in a running pipeline

    Callbacks (called from a GStreamer / worker thread):
    done(filename, error): recording finalized. error is None on success
    """
    def __init__(self, player, tee, log=None, wh=None):
        """
        player: Gst.Pipeline
        tee: tee to record from (must already be in player)
        wh: fallback frame size if the tee hasn't negotiated caps yet
        """
        self.player = player
        self.tee = tee
        if log is None:
            log = print
        self.log = log
        self.wh = wh
        self.lock = threading.Lock()
        self.bin = None
        self.tee_pad = None
        self.audio_src = None
        self.filename = None
        self.done = None
        self.error = None
        self.stopping = False
        self.finished = threading.Event()
        self.finished.set()
        self.stop_timer = None

    def is_recording(self):
        return self.bin is not None

    def _tee_wh(self):
        caps = self.tee.get_static_pad("sink").get_current_caps()
        if caps and caps.get_size():
            s = caps.get_structure(0)
            ok_w, w = s.get_int("width")
            ok_h, h = s.get_int("height")
            if ok_w and ok_h:
                return w, h
        return self.wh

    def start(self,
              filename_base,
              container="mp4",
              audio=False,
              audio_source="autoaudiosrc",
              max_wh=None,
              done=None):
        """
        filename_base: output file name without extension
        audio_source: gst-launch style description, ex: "pulsesrc device=foo"
        Returns the file name being written
        Raises RecordError if recording couldn't be started
        """
        with self.lock:
            if self.bin is not None:
                raise RecordError("Already recording")
            plan = plan_recording(container=container, audio=audio)
            if plan["container"] != container:
                self.log(
                    f"Recording: {container} unavailable, using {plan['container']}"
                )
            filename = filename_base + plan["extension"]
            wh = self._tee_wh()
            if wh is None:
                raise RecordError("Video size unknown: is the camera running?")
            width, height = fit_wh(wh[0], wh[1], max_wh)
            # Errors before the branch is linked are raised to the caller
            self.error = None
            self.bin = self._create_bin(plan, filename, width, height,
                                        audio_source)
            self.filename = filename
            self.done = done
            self.stopping = False
            self.finished.clear()
            try:
                self._link()
            except Exception:
                self._teardown()
                self.finished.set()
                raise
            self.log(f"Recording: started {filename} ({width}x{height}, "
                     f"{plan['video_encoder']}" +
                     (f" + {plan['audio_encoder']}" if audio else "") + ")")
            return filename

    def _create_bin(self, plan, filename, width, height, audio_source):
        rbin = RecordingBin(on_error=self._on_error, on_eos=self._on_eos)
        elements = []

        def add(factory, props=None):
            element = make_element(factory, props)
            rbin.add(element)
            elements.append(element)
            return element

        # Leaky: if the encoder can't keep up drop frames here
        # instead of stalling the tee (live view, snapshots)
        add("queue", {
            "leaky": "downstream",
            "max-size-buffers": "3",
            "max-size-bytes": "0",
            "max-size-time": "0",
        })
        add("videoconvert")
        add("videoscale")
        add("capsfilter", {
            "caps":
            f"video/x-raw,format=I420,width={width},height={height},pixel-aspect-ratio=1/1"
        })
        add(plan["video_encoder"], plan["video_encoder_props"])
        if plan["video_parser"]:
            add(plan["video_parser"])
        mux = add(plan["mux"])
        filesink = add("filesink", {"location": filename, "async": "false"})
        rbin.filesink = filesink
        for src, dst in zip(elements, elements[1:]):
            if not src.link(dst):
                raise RecordError(f"Failed to link {src.get_name()} => "
                                  f"{dst.get_name()}")
        ghost = Gst.GhostPad.new("sink", elements[0].get_static_pad("sink"))
        rbin.add_pad(ghost)

        self.audio_src = None
        if plan["audio_encoder"]:
            try:
                self.audio_src = Gst.parse_bin_from_description(
                    audio_source, True)
            except GLib.Error as e:
                raise RecordError(
                    f"Failed to create audio source {audio_source}: {e}")
            rbin.add(self.audio_src)
            achain = [self.audio_src]
            for factory, props in (
                ("audioconvert", None),
                ("audioresample", None),
                ("queue", None),
                (plan["audio_encoder"], plan["audio_encoder_props"]),
                (plan["audio_parser"], None),
            ):
                if factory:
                    element = make_element(factory, props)
                    rbin.add(element)
                    achain.append(element)
            for src, dst in zip(achain, achain[1:]):
                if not src.link(dst):
                    raise RecordError(f"Failed to link {src.get_name()} => "
                                      f"{dst.get_name()}")
            if not achain[-1].link(mux):
                raise RecordError("Failed to link audio to muxer")
        return rbin

    def _running_time(self):
        clock = self.player.get_clock()
        if clock is None:
            return 0
        return max(0, clock.get_time() - self.player.get_base_time())

    def _link(self):
        self.player.add(self.bin)
        self.tee_pad = self.tee.request_pad_simple("src_%u")
        # Start the file at t=0 instead of at pipeline uptime
        offset = -self._running_time()
        sinkpad = self.bin.get_static_pad("sink")
        sinkpad.set_offset(offset)
        if self.audio_src is not None:
            self.audio_src.get_static_pad("src").set_offset(offset)
        if self.tee_pad.link(sinkpad) != Gst.PadLinkReturn.OK:
            raise RecordError("Failed to link recording branch to tee")
        if not self.bin.sync_state_with_parent():
            raise RecordError("Failed to start recording branch")
        if self.audio_src is not None:
            self.player.recalculate_latency()

    def stop(self, wait=False, timeout=5.0):
        """
        Finalize the file and remove the recording branch
        Asynchronous unless wait: done callback reports completion
        Returns True if finished (always True when not waiting)
        """
        with self.lock:
            if self.bin is None or self.stopping:
                pass
            else:
                self.stopping = True
                self.tee_pad.add_probe(Gst.PadProbeType.IDLE,
                                       self._idle_probe)
                # Live sources only stop on request
                if self.audio_src is not None:
                    self.audio_src.send_event(Gst.Event.new_eos())
                # ex: pipeline not PLAYING => EOS never makes it through
                self.stop_timer = threading.Timer(timeout,
                                                  self._stop_timeout)
                self.stop_timer.daemon = True
                self.stop_timer.start()
        if wait:
            return self.finished.wait(timeout + 1.0)
        return True

    def _idle_probe(self, pad, info):
        # Tee won't push to us anymore: safe to unlink
        sinkpad = pad.get_peer()
        if sinkpad is not None:
            pad.unlink(sinkpad)
            sinkpad.send_event(Gst.Event.new_eos())
        return Gst.PadProbeReturn.REMOVE

    def _stop_timeout(self):
        self._finish_async(error="timed out waiting for file to finalize")

    def _on_eos(self):
        self._finish_async(error=None)

    def _on_error(self, error):
        self._finish_async(error=error)

    def _finish_async(self, error):
        # Called from streaming threads which can't tear down their own elements
        threading.Thread(target=self._finish,
                         args=(error, ),
                         name="recording-stop",
                         daemon=True).start()

    def _finish(self, error):
        with self.lock:
            if self.bin is None:
                return
            if self.stop_timer:
                self.stop_timer.cancel()
                self.stop_timer = None
            filename = self.filename
            done = self.done
            self._teardown()
        if error:
            self.log(f"Recording: failed {filename}: {error}")
        else:
            self.log(f"Recording: saved to {filename}")
        self.error = error
        self.finished.set()
        if done:
            done(filename, error)

    def _teardown(self):
        rbin = self.bin
        tee_pad = self.tee_pad
        self.bin = None
        self.tee_pad = None
        self.audio_src = None
        self.done = None
        if tee_pad is not None:
            peer = tee_pad.get_peer()
            if peer is not None:
                tee_pad.unlink(peer)
        rbin.set_state(Gst.State.NULL)
        if rbin.get_parent() is not None:
            self.player.remove(rbin)
        if tee_pad is not None:
            self.tee.release_request_pad(tee_pad)
        # Don't leave a truncated file from a failed start lying around
        if self.filename and os.path.exists(
                self.filename) and os.path.getsize(self.filename) == 0:
            os.unlink(self.filename)
