"""Tests for camera detection, error reporting and the camera endpoints.

These must run on a machine with no webcam and no OpenCV, so every test
either injects a fake ``cv2`` or asserts on the degraded path.
"""

from __future__ import annotations

from pathlib import Path

import itertools

import pytest

from camera.camera import Camera, NotConfiguredCamera
from camera import usb_camera as usb_mod
from camera.usb_camera import UsbCamera


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class FakeFrame:
    """Stands in for a cv2 numpy frame."""

    def __init__(self) -> None:
        self.shape = (480, 640, 3)


class FakeCapture:
    """Minimal cv2.VideoCapture stand-in."""

    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5

    def __init__(self, *, opens: bool = True, delivers: bool = True,
                 width: int = 640, height: int = 480, fps: float = 30.0) -> None:
        self._opens = opens
        self._delivers = delivers
        self._props = {3: width, 4: height, 5: fps}
        self.released = False
        self.requested: dict[int, int] = {}

    def isOpened(self) -> bool:
        return self._opens

    def set(self, prop: int, value) -> None:
        self.requested[prop] = value

    def get(self, prop: int):
        return self._props.get(prop, 0.0)

    def read(self):
        if not self._delivers:
            return False, None
        return True, FakeFrame()

    def release(self) -> None:
        self.released = True


class FakeCv2:
    """Minimal cv2 module stand-in."""

    def __init__(self, capture: FakeCapture) -> None:
        self._capture = capture
        self.CAP_PROP_FRAME_WIDTH = FakeCapture.CAP_PROP_FRAME_WIDTH
        self.CAP_PROP_FRAME_HEIGHT = FakeCapture.CAP_PROP_FRAME_HEIGHT
        self.CAP_PROP_FPS = FakeCapture.CAP_PROP_FPS
        self.targets: list = []
        self.written: list[str] = []
        self.encoded = 0

    def VideoCapture(self, target):  # noqa: N802 - mirrors the cv2 API
        self.targets.append(target)
        return self._capture

    def imwrite(self, path, frame) -> None:
        self.written.append(path)

    def imencode(self, ext, frame):
        self.encoded += 1
        return True, _FakeBuffer(b"\xff\xd8\xff-jpeg-bytes")


class _FakeBuffer:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def tobytes(self) -> bytes:
        return self._data


@pytest.fixture
def fake_cv2(monkeypatch):
    """Install a fake cv2 and hand back a factory for the capture."""
    holder: dict[str, FakeCv2] = {}

    def install(capture: FakeCapture) -> FakeCv2:
        module = FakeCv2(capture)
        holder["module"] = module
        monkeypatch.setattr(usb_mod, "_import_cv2", lambda: (module, None))
        return module

    return install


@pytest.fixture
def no_cv2(monkeypatch):
    monkeypatch.setattr(
        usb_mod,
        "_import_cv2",
        lambda: (None, "OpenCV is not installed (No module named 'cv2')"),
    )


@pytest.fixture
def per_node_cv2(monkeypatch):
    """Fake cv2 whose behaviour depends on which /dev/video* is opened.

    Mirrors a Pi with libcamera: several nodes open successfully but never
    deliver a frame, and only one is the real webcam.
    """
    nodes: dict[str, FakeCapture] = {}

    class MultiCaptureCv2(FakeCv2):
        def VideoCapture(self, target):  # noqa: N802 - mirrors the cv2 API
            self.targets.append(target)
            return nodes.get(str(target), FakeCapture(opens=False))

    module = MultiCaptureCv2(FakeCapture())
    monkeypatch.setattr(usb_mod, "_import_cv2", lambda: (module, None))
    return module, nodes


def _video_node(path: str, *, capture: bool) -> dict:
    return {
        "path": path,
        "index": int(path.replace("/dev/video", "")),
        "name": "usb_camera" if capture else "rp1-cfe",
        "capture": capture,
    }


# ----------------------------------------------------------------------
# NotConfiguredCamera
# ----------------------------------------------------------------------

def test_not_configured_reports_not_installed():
    status = NotConfiguredCamera().status()
    assert status["configured"] is False
    assert status["message"] == "Camera not installed"
    assert status["error"] is None


def test_not_configured_capture_returns_none():
    assert NotConfiguredCamera().capture() is None


def test_not_configured_stream_is_empty():
    assert list(NotConfiguredCamera().stream()) == []


def test_camera_base_stream_defaults_to_empty():
    class Bare(Camera):
        def capture(self, save_path=None):
            return None

    assert list(Bare().stream()) == []


# ----------------------------------------------------------------------
# Missing OpenCV
# ----------------------------------------------------------------------

def test_missing_opencv_is_reported_not_hidden(tmp_path, no_cv2):
    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)

    status = cam.status()
    assert status["configured"] is False
    assert "OpenCV is not installed" in status["error"]
    assert "OpenCV is not installed" in status["message"]


def test_cv2_availability_reports_missing(no_cv2):
    available, reason = usb_mod.cv2_availability()
    assert available is False
    assert "OpenCV" in reason


# ----------------------------------------------------------------------
# Successful open
# ----------------------------------------------------------------------

def test_opens_device_path_and_reports_resolution(tmp_path, fake_cv2):
    capture = FakeCapture(width=1280, height=720, fps=15.0)
    cv2 = fake_cv2(capture)

    cam = UsbCamera(device_path="/dev/video0", width=1280, height=720,
                    fps=15, image_dir=tmp_path)

    assert cv2.targets == ["/dev/video0"], "device_path must take priority"
    status = cam.status()
    assert status["configured"] is True
    assert status["device_path"] == "/dev/video0"
    assert (status["width"], status["height"]) == (1280, 720)
    assert status["fps"] == 15.0
    assert status["error"] is None
    assert "1280x720" in status["message"]
    cam.close()


def test_requested_format_is_applied_and_readback_is_reported(tmp_path, fake_cv2):
    """The driver may ignore a requested size; status must report reality."""
    capture = FakeCapture(width=320, height=240)
    cv2 = fake_cv2(capture)

    cam = UsbCamera(device_path="/dev/video0", width=1280, height=720,
                    image_dir=tmp_path)

    assert cv2.targets == ["/dev/video0"]
    assert capture.requested[FakeCapture.CAP_PROP_FRAME_WIDTH] == 1280
    status = cam.status()
    assert (status["width"], status["height"]) == (320, 240), \
        "must report what the driver actually gave us, not what we asked for"
    cam.close()


def test_missing_device_path_is_reported(tmp_path, fake_cv2, monkeypatch):
    fake_cv2(FakeCapture(opens=False))
    monkeypatch.setattr(usb_mod, "list_video_devices", lambda: [])

    cam = UsbCamera(device_path="/dev/video9", image_dir=tmp_path)

    status = cam.status()
    assert status["configured"] is False
    assert "/dev/video9 does not exist" in status["error"]
    assert status["simulated"] is False


def test_unreadable_device_lists_alternatives(tmp_path, fake_cv2, monkeypatch):
    fake_cv2(FakeCapture(opens=False))
    monkeypatch.setattr(
        usb_mod, "list_video_devices",
        lambda: [{"path": "/dev/video0", "index": 0, "name": None, "capture": None}],
    )

    cam = UsbCamera(device_path="/dev/video9", image_dir=tmp_path)

    assert "/dev/video0" in cam.status()["error"]


def test_permission_denied_is_explained(tmp_path, fake_cv2, monkeypatch):
    fake_cv2(FakeCapture(opens=False))
    monkeypatch.setattr(usb_mod.os.path, "exists", lambda p: True)
    monkeypatch.setattr(usb_mod.os, "access", lambda p, mode: False)
    monkeypatch.setattr(usb_mod, "_video_devices", lambda: [("/dev/video0", 0)])

    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)

    error = cam.status()["error"]
    assert "No permission" in error
    assert "video" in error and "usermod" in error


def test_driver_refuses_open(tmp_path, fake_cv2, monkeypatch):
    fake_cv2(FakeCapture(opens=False))
    monkeypatch.setattr(usb_mod, "_video_devices", lambda: [("/dev/video0", 0)])

    cam = UsbCamera(device_index=0, image_dir=tmp_path)

    error = cam.status()["error"]
    assert "driver refused" in error
    assert "guvcview" in error


def test_no_video_nodes_at_all(tmp_path, fake_cv2, monkeypatch):
    fake_cv2(FakeCapture(opens=False))
    monkeypatch.setattr(usb_mod, "_video_devices", lambda: [])
    monkeypatch.setattr(usb_mod.glob, "glob", lambda p: [])

    cam = UsbCamera(device_index=0, image_dir=tmp_path)

    error = cam.status()["error"]
    assert "no /dev/video* nodes" in error
    assert "lsusb" in error


def test_opened_but_no_frames_is_a_fault(tmp_path, fake_cv2):
    """isOpened() can be true for a node that never delivers images."""
    fake_cv2(FakeCapture(opens=True, delivers=False))

    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)

    status = cam.status()
    assert status["configured"] is False
    assert "no frames" in status["error"]
    assert cam.capture() is None, "must not fabricate an image for a broken device"


# ----------------------------------------------------------------------
# Index fallback
# ----------------------------------------------------------------------

def test_falls_back_to_device_index_when_path_empty(tmp_path, fake_cv2, monkeypatch):
    fake_cv2(FakeCapture())
    monkeypatch.setattr(usb_mod, "_path_for_index", lambda i: "/dev/video0")

    cam = UsbCamera(device_index=0, device_path=None, image_dir=tmp_path)

    assert cam.status()["device_path"] == "/dev/video0"


def test_index_resolves_to_a_real_path(tmp_path, fake_cv2, monkeypatch):
    fake_cv2(FakeCapture())
    monkeypatch.setattr(usb_mod, "_path_for_index", lambda i: f"/dev/video{i}")

    cam = UsbCamera(device_index=2, image_dir=tmp_path)

    assert cam.status()["device_path"] == "/dev/video2"


# ----------------------------------------------------------------------
# Capture / stream
# ----------------------------------------------------------------------

def test_capture_writes_through_the_real_device(tmp_path, fake_cv2):
    cv2 = fake_cv2(FakeCapture())
    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)

    out = cam.capture(save_path=tmp_path / "shot.jpg")

    assert out == str(tmp_path / "shot.jpg")
    assert cv2.written == [str(tmp_path / "shot.jpg")]


def test_simulated_capture_writes_a_file(tmp_path):
    cam = UsbCamera(simulate=True, image_dir=tmp_path)
    out = cam.capture(save_path=tmp_path / "sim.jpg")
    assert out is not None
    assert Path(out).exists()


# ----------------------------------------------------------------------
# Auto-detection
# ----------------------------------------------------------------------

def test_auto_picks_the_node_that_delivers_frames(tmp_path, per_node_cv2, monkeypatch):
    module, nodes = per_node_cv2
    # Reproduces a Pi with libcamera: video0 opens but is metadata-only,
    # video1 is the real webcam.
    monkeypatch.setattr(usb_mod, "candidate_devices",
                        lambda: ["/dev/video0", "/dev/video1"])
    nodes["/dev/video0"] = FakeCapture(opens=True, delivers=False)
    nodes["/dev/video1"] = FakeCapture(opens=True, delivers=True)

    cam = UsbCamera(device_path="auto", image_dir=tmp_path)

    assert cam.status()["configured"] is True
    assert cam.device_path == "/dev/video1"
    assert cam.status()["error"] is None


def test_auto_reports_every_tried_node_when_none_deliver(tmp_path, per_node_cv2, monkeypatch):
    module, nodes = per_node_cv2
    monkeypatch.setattr(usb_mod, "candidate_devices",
                        lambda: ["/dev/video0", "/dev/video1"])
    nodes["/dev/video0"] = FakeCapture(opens=True, delivers=False)
    nodes["/dev/video1"] = FakeCapture(opens=True, delivers=False)

    cam = UsbCamera(device_path="auto", image_dir=tmp_path)
    status = cam.status()

    assert status["configured"] is False
    assert status["simulated"] is False
    assert "no usable camera" in status["error"]
    assert "/dev/video0" in status["error"]
    assert status["tried_devices"] == ["/dev/video0", "/dev/video1"]


def test_auto_fails_clearly_with_no_video_nodes(tmp_path, fake_cv2, monkeypatch):
    fake_cv2(FakeCapture())
    monkeypatch.setattr(usb_mod, "candidate_devices", lambda: [])

    cam = UsbCamera(device_path="auto", image_dir=tmp_path)

    assert cam.status()["configured"] is False
    assert "no usable camera" in cam.status()["error"]


def test_probe_reports_metadata_only_node(tmp_path, per_node_cv2):
    module, nodes = per_node_cv2
    nodes["/dev/video0"] = FakeCapture(opens=True, delivers=False)
    nodes["/dev/video1"] = FakeCapture(opens=True, delivers=True, width=1280, height=720)

    bad = usb_mod.probe_device("/dev/video0", module)
    good = usb_mod.probe_device("/dev/video1", module)

    assert bad["opened"] is True
    assert bad["delivers_frames"] is False
    assert "metadata-only" in bad["error"]
    # A metadata node can still advertise a resolution, which is precisely
    # why isOpened() and resolution checks cannot detect this fault.
    assert (bad["width"], bad["height"]) == (640, 480)

    assert good["delivers_frames"] is True
    assert (good["width"], good["height"]) == (1280, 720)
    assert good["error"] is None


def test_probe_reports_a_node_that_will_not_open(tmp_path, per_node_cv2):
    module, _ = per_node_cv2

    verdict = usb_mod.probe_device("/dev/video9", module)

    assert verdict["opened"] is False
    assert verdict["delivers_frames"] is False
    assert verdict["error"] is not None


def test_probe_always_releases_the_device(tmp_path, per_node_cv2):
    module, nodes = per_node_cv2
    capture = FakeCapture()
    nodes["/dev/video0"] = capture

    usb_mod.probe_device("/dev/video0", module)

    assert capture.released is True


def test_candidate_devices_prefers_capture_capable_nodes(monkeypatch):
    monkeypatch.setattr(usb_mod, "list_video_devices", lambda: [
        _video_node("/dev/video20", capture=False),
        _video_node("/dev/video1", capture=True),
        _video_node("/dev/video0", capture=True),
    ])

    ordered = usb_mod.candidate_devices()

    assert ordered[0] == "/dev/video0"
    assert ordered[1] == "/dev/video1"
    assert "/dev/video20" not in ordered[:2]


def test_stream_yields_encoded_frames_then_stops(tmp_path, fake_cv2):
    capture = FakeCapture()
    cv2 = fake_cv2(capture)
    # Opening the device already consumes one read as its frame check, so
    # allow three more before the device goes dark.
    original_read = capture.read
    state = {"n": 0}

    def read():
        state["n"] += 1
        if state["n"] > 4:
            return False, None
        return original_read()

    capture.read = read
    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)

    frames = list(cam.stream(fps=1000))
    assert len(frames) == 3
    assert frames[0].startswith(b"\xff\xd8\xff")
    assert cv2.encoded == 3


def test_degraded_camera_streams_nothing(tmp_path, fake_cv2):
    fake_cv2(FakeCapture(opens=True, delivers=False))
    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)
    assert list(cam.stream()) == []


def test_degraded_camera_does_not_fabricate_frames(tmp_path, fake_cv2):
    """Regression: a broken device must not feed fake images to the
    detector, which would report 'no pests' forever."""
    fake_cv2(FakeCapture(opens=True, delivers=False))
    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)
    assert cam.capture(save_path=tmp_path / "shot.jpg") is None
    assert not (tmp_path / "shot.jpg").exists()


def test_degraded_status_is_not_reported_as_simulated(tmp_path, fake_cv2):
    fake_cv2(FakeCapture(opens=False))
    status = UsbCamera(device_path="/dev/video9", image_dir=tmp_path).status()
    assert status["simulated"] is False, \
        "a failed device must not masquerade as a simulated one"
    assert status["message"].startswith("Camera unavailable")


def test_stream_is_empty_when_simulated(tmp_path):
    assert list(UsbCamera(simulate=True, image_dir=tmp_path).stream()) == []


def test_close_releases_the_device(tmp_path, fake_cv2):
    capture = FakeCapture()
    fake_cv2(capture)
    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)

    cam.close()

    assert capture.released is True
    assert cam.status()["configured"] is False


def test_closed_camera_does_not_claim_to_be_connected(tmp_path, fake_cv2):
    capture = FakeCapture()
    fake_cv2(capture)
    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)
    assert "Connected" in cam.status()["message"]

    cam.close()

    status = cam.status()
    assert status["configured"] is False
    assert "Connected" not in status["message"]
    assert "closed" in status["message"].lower()


def test_concurrent_capture_and_stream_are_serialized(tmp_path, fake_cv2):
    """The controller thread and the stream share one VideoCapture."""
    import threading

    capture = FakeCapture()
    fake_cv2(capture)
    cam = UsbCamera(device_path="/dev/video0", image_dir=tmp_path)

    errors: list[BaseException] = []

    def hammer(fn):
        try:
            for _ in range(30):
                fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    def consume():
        # stream() is infinite, so bound the work instead of draining it.
        for _ in itertools.islice(cam.stream(fps=1000), 10):
            pass

    threads = [
        threading.Thread(target=hammer, args=(consume,), daemon=True),
        threading.Thread(target=hammer,
                         args=(lambda: cam.capture(save_path=tmp_path / "c.jpg"),),
                         daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent access raised: {errors}"
    cam.close()
