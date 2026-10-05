"""USB webcam backend.

A USB webcam is a V4L2 capture device. On Linux it appears as
``/dev/video0``, ``/dev/video1``, … — **never** as a ``ttyUSB*`` serial
port. (``/dev/ttyUSB0`` on this project is the NPK sensor's RS485/FTDI
adapter; pointing a camera at it can never work.)

Two ways to select the device, in priority order:

``device_path``
    An explicit path such as ``/dev/video0``. Deterministic and the
    recommended setting.
``device_index``
    An integer OpenCV index. Fragile — the index-to-path mapping depends
    on enumeration order — but kept for backward compatibility.

Failure is never silent here. If the device cannot be opened,
:attr:`UsbCamera.error` holds the reason and :meth:`UsbCamera.status`
reports ``configured: False`` *together with that reason*, so the
dashboard can tell the user why instead of claiming the camera was never
installed.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from camera.camera import Camera

logger = logging.getLogger(__name__)

# /dev/video0, /dev/video1, …
_VIDEO_DEVICE_RE = re.compile(r"^/dev/video(\d+)$")


# ----------------------------------------------------------------------
# Device enumeration
# ----------------------------------------------------------------------

def _import_cv2():
    """Import OpenCV, returning ``(module, None)`` or ``(None, reason)``."""
    try:
        import cv2  # type: ignore

        return cv2, None
    except ImportError as exc:
        return None, (
            f"OpenCV is not installed ({exc}). "
            f"Install it with: pip install opencv-python-headless"
        )
    except Exception as exc:  # pragma: no cover - broken native library
        return None, f"OpenCV failed to load: {exc}"


def cv2_availability() -> tuple[bool, str | None]:
    """``(available, reason)`` — lets the dashboard explain a missing cv2."""
    _, error = _import_cv2()
    return error is None, error


def load_cv2():
    """Public ``(module, reason)`` accessor for the OpenCV backend."""
    return _import_cv2()


def _video_devices() -> list[tuple[str, int]]:
    """Return ``[(path, index), ...]`` for every V4L2 node, sorted by index."""
    found: list[tuple[int, str]] = []
    for path in glob.glob("/dev/video*"):
        match = _VIDEO_DEVICE_RE.match(path)
        if match:
            found.append((int(match.group(1)), path))
    return [(path, index) for index, path in sorted(found)]


def _path_for_index(index: int) -> str | None:
    """Resolve an OpenCV capture index to its ``/dev/videoN`` path."""
    for path, candidate in _video_devices():
        if candidate == index:
            return path
    return None


def _sysfs_name(path: str) -> str | None:
    """Best-effort human-readable device name from sysfs."""
    node = path.rsplit("/", 1)[-1]
    try:
        with open(f"/sys/class/video4linux/{node}/name", encoding="utf-8") as handle:
            text = handle.read().strip()
        return text or None
    except OSError:
        return None


def _describe_with_v4l2ctl(entries: list[dict[str, Any]]) -> bool:
    """Fill in card/capture/resolution details using ``v4l2-ctl``.

    Returns True when at least one device was described. Silently does
    nothing when the tool is not installed — it is an optional nicety.
    """
    import shutil
    import subprocess

    if shutil.which("v4l2-ctl") is None:
        return False

    described = False
    for entry in entries:
        path = entry["path"]
        entry["capture"] = False
        try:
            proc = subprocess.run(
                ["v4l2-ctl", "-d", path, "--all"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("v4l2-ctl failed for %s: %s", path, exc)
            continue

        described = True
        blob = proc.stdout
        entry["capture"] = "capture" in blob.lower()
        for line in blob.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("card type"):
                card = stripped.split(":", 1)[-1].strip()
                if card:
                    entry["card"] = card
                break
        width = re.search(r"Width:\s*(\d+)", blob)
        height = re.search(r"Height:\s*(\d+)", blob)
        if width and height:
            entry["resolution"] = f"{width.group(1)}x{height.group(1)}"
    return described


def list_video_devices() -> list[dict[str, Any]]:
    """List V4L2 devices visible to this process.

    Each entry is ``{"path", "index", "name", "capture", "card"?,
    "resolution"?}``. ``capture`` is a bool when ``v4l2-ctl`` is
    available and ``None`` when it is not — the caller can still show the
    user which nodes exist.
    """
    entries: list[dict[str, Any]] = [
        {
            "path": path,
            "index": index,
            "name": _sysfs_name(path),
            "capture": None,
        }
        for path, index in _video_devices()
    ]
    if entries:
        _describe_with_v4l2ctl(entries)
    return entries


def _target_label(target: str | int) -> str:
    """Human-readable description of a device selector."""
    if isinstance(target, int):
        resolved = _path_for_index(target)
        return resolved if resolved else f"camera index {target}"
    return str(target)


# ----------------------------------------------------------------------
# Numeric coercion
# ----------------------------------------------------------------------

def _as_int(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0 or number >= 2**31:  # NaN-safe
        return None
    return int(number)


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:
        return None
    return round(number, 2)


def probe_device(target: str | int, cv2) -> dict[str, Any]:
    """Open *target* and report whether it really delivers frames.

    Distinguishes the three failures that matter on a Pi with many video
    nodes: the node is missing, it opens but is metadata-only (libcamera
    pipelines behave this way), or it delivers frames.

    The capture is always released before returning.
    """
    result: dict[str, Any] = {
        "target": target if isinstance(target, str) else f"index {target}",
        "opened": False,
        "delivers_frames": False,
        "width": None,
        "height": None,
        "error": None,
    }

    try:
        cap = cv2.VideoCapture(target)
    except Exception as exc:
        result["error"] = f"open failed: {exc}"
        return result

    try:
        if not cap.isOpened():
            result["error"] = "driver refused to open it"
            return result
        result["opened"] = True

        result["width"] = _as_int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        result["height"] = _as_int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        ok, frame = cap.read()
        if ok and frame is not None:
            result["delivers_frames"] = True
        else:
            result["error"] = (
                "opens but returns no frames — this is a metadata-only node "
                "(common with libcamera pipelines), not a usable camera"
            )
    except Exception as exc:
        result["error"] = f"probe failed: {exc}"
    finally:
        try:
            cap.release()
        except Exception:
            pass
    return result


def candidate_devices() -> list[str]:
    """Device paths worth trying, best first.

    Prefers nodes ``v4l2-ctl`` reports as capture-capable, because on a Pi
    with libcamera the /dev/video* list also contains metadata-only nodes
    that open successfully but never produce an image.
    """
    entries = list_video_devices()
    if not entries:
        return []

    capture, unknown, metadata = [], [], []
    for entry in entries:
        bucket = {True: capture, None: unknown, False: metadata}.get(
            entry.get("capture"), unknown
        )
        bucket.append((entry.get("index", 0), entry["path"]))

    # Sort by node index so the probe order is deterministic. Glob order is
    # not guaranteed, and video0/video1 are the usual UVC webcam positions.
    def ordered(bucket):
        return [path for _, path in sorted(bucket)]

    return ordered(capture) + ordered(unknown) + ordered(metadata)


# ----------------------------------------------------------------------
# Camera
# ----------------------------------------------------------------------

class UsbCamera(Camera):
    """USB webcam implementation using OpenCV."""

    name = "usb_camera"

    def __init__(
        self,
        device_index: int | None = None,
        device_path: str | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: int | None = None,
        image_dir: str | Path = "data/images",
        simulate: bool = False,
    ) -> None:
        self.device_index = device_index
        self.device_path = device_path
        self.width = width
        self.height = height
        self.fps = fps
        self.image_dir = Path(image_dir)
        self.simulate = simulate

        #: Human-readable reason the camera is unusable, or None.
        self.error: str | None = None
        #: True when a real device was requested but could not be opened.
        #: Kept apart from ``simulate`` on purpose: a degraded camera must
        #: fail loudly rather than hand out fabricated frames that pest
        #: detection would happily report as "no pests found".
        self._degraded = False
        self._closed = False
        #: Nodes examined during a failed auto-detection, for diagnostics.
        self.tried_devices: list[str] = []
        #: Actual stream properties reported by the driver.
        self.actual_width: int | None = None
        self.actual_height: int | None = None
        self.actual_fps: float | None = None

        # cv2.VideoCapture is not thread-safe. The automation controller
        # reads frames on its own thread while the dashboard streams them
        # on a request thread, so every access is serialized here.
        self._cap_lock = threading.Lock()
        self._cap = None
        self._cv2 = None

        self.image_dir.mkdir(parents=True, exist_ok=True)

        if simulate:
            logger.info("USB camera in SIMULATED mode")
        else:
            self._open()

    # ------------------------------------------------------------------
    # Opening
    # ------------------------------------------------------------------

    def _open(self) -> None:
        """Open the device, recording an actionable reason on failure."""
        cv2, import_error = _import_cv2()
        if cv2 is None:
            self._fail(import_error)
            return
        self._cv2 = cv2

        # device_path wins; otherwise fall back to the index, then to 0.
        if self.device_path and self.device_path.lower() == "auto":
            if self._open_auto(cv2):
                return
            self._fail(self._describe_open_failure("auto"))
            return

        target: str | int = self.device_path if self.device_path else (
            self.device_index if self.device_index is not None else 0
        )

        try:
            cap = cv2.VideoCapture(target)
        except Exception as exc:
            self._fail(f"Error opening {_target_label(target)}: {exc}")
            return

        if not cap.isOpened():
            cap.release()
            self._fail(self._describe_open_failure(target))
            return

        self._cap = cap
        self._finish_open(target)
        if self._cap is not None:
            self._resolve_index_path(target)

    def _open_auto(self, cv2) -> bool:
        """Probe candidate nodes until one delivers frames.

        Needed because a Pi with libcamera exposes many /dev/video* nodes
        and most of them are metadata-only: they open fine and never
        produce an image.
        """
        candidates = candidate_devices()
        if not candidates:
            logger.error("Auto-detection found no /dev/video* nodes")
            return False

        logger.info("Auto-detecting camera among: %s", ", ".join(candidates))
        for path in candidates:
            verdict = probe_device(path, cv2)
            if verdict["delivers_frames"]:
                logger.info("Auto-detected working camera at %s", path)
                self.device_path = path
                cap = cv2.VideoCapture(path)
                if cap.isOpened():
                    self._cap = cap
                    self._finish_open(path)
                    return self._cap is not None
            logger.debug("Skipping %s: %s", path, verdict.get("error"))

        self.tried_devices = candidates
        return False

    def _finish_open(self, target: str | int) -> None:
        """Apply the requested format, verify frames, and set status."""
        cv2 = self._cv2
        cap = self._cap
        self._closed = False
        self._degraded = False

        # Requested format. These are only hints — many drivers ignore
        # them — so the actual values are read back below.
        if self.width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.width))
        if self.height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.height))
        if self.fps:
            cap.set(cv2.CAP_PROP_FPS, int(self.fps))

        self.actual_width = _as_int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.actual_height = _as_int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.actual_fps = _as_float(cap.get(cv2.CAP_PROP_FPS))

        # Prove the device actually delivers frames. isOpened() returns
        # true for nodes that never produce an image, and that is exactly
        # the case that used to degrade into a silent "not installed".
        ok, frame = self._guarded_read()
        if not ok or frame is None:
            cap.release()
            self._cap = None
            self._fail(
                f"Opened {_target_label(target)} but it returned no frames. "
                f"Another program may be holding the webcam, or the service "
                f"user may lack access (fix: sudo usermod -aG video veggiecare)."
            )
            return

        logger.info(
            "USB camera ready on %s (%sx%s @ %s fps)",
            self.device_path or target,
            self.actual_width,
            self.actual_height,
            self.actual_fps,
        )

    def _resolve_index_path(self, target: str | int) -> None:
        """After an index-based open, report the path it resolved to."""
        if not self.device_path and isinstance(target, int):
            self.device_path = _path_for_index(target)

    def _fail(self, reason: str) -> None:
        """Record *reason* and mark the camera degraded (not simulated)."""
        self.error = reason
        self._degraded = True
        self._cap = None
        logger.error("USB camera unavailable: %s", reason)

    def _describe_open_failure(self, target: str | int) -> str:
        """Build an actionable message for a device that would not open."""
        label = _target_label(target)

        if target == "auto":
            tried = getattr(self, "tried_devices", [])
            detail = f" Tried: {', '.join(tried)}." if tried else ""
            return (
                f"Auto-detection found no usable camera among {len(tried)} "
                f"video node(s).{detail} Nodes that open but never deliver frames "
                f"are metadata-only (typical of libcamera pipelines). Install "
                f"v4l-utils for 'v4l2-ctl --list-devices', and set camera.device_path "
                f"in config.yaml to the node that is your webcam."
            )

        if isinstance(target, str):
            if not os.path.exists(target):
                available = [entry["path"] for entry in list_video_devices()]
                hint = f" Available: {', '.join(available)}." if available else ""
                return f"{label} does not exist.{hint}"
            if not os.access(target, os.R_OK | os.W_OK):
                return (
                    f"No permission to open {label}. Add the service user to the "
                    f"'video' group: sudo usermod -aG video veggiecare, then restart."
                )

        if _video_devices():
            return (
                f"Could not open {label}. The node exists but the driver refused it. "
                f"Check that the webcam is plugged in and not claimed by another "
                f"program (a running guvcview, browser or video call will hold it)."
            )
        return (
            f"Could not open {label} and no /dev/video* nodes were found. "
            f"The kernel does not see the webcam — check the USB cable and run "
            f"'lsusb | grep -i camera'."
        )

    def _guarded_read(self):
        """Read one frame under the lock; returns ``(ok, frame)``."""
        if self._cap is None or self._degraded:
            return False, None
        if not self._cap_lock.acquire(timeout=5.0):
            return False, None
        try:
            return self._cap.read()
        except Exception as exc:
            logger.error("Error reading frame: %s", exc)
            return False, None
        finally:
            self._cap_lock.release()

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, save_path: str | Path | None = None) -> str | None:
        if save_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            save_path = self.image_dir / f"capture_{ts}.jpg"

        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if self._degraded:
            # A device we were asked to use but could not open is a fault.
            # Returning None makes the controller skip the cycle and report
            # the error, instead of analysing a fabricated image.
            logger.error("Capture requested but the camera is unavailable: %s", self.error)
            return None

        if self.simulate:
            return self._write_simulated_frame(save_path)

        ok, frame = self._guarded_read()
        if not ok or frame is None:
            # A real device that stops delivering frames is a fault, not a
            # reason to fabricate an image.
            logger.error("Failed to read frame from USB camera")
            return None

        try:
            self._cv2.imwrite(str(save_path), frame)
        except Exception as exc:
            logger.error("Error saving capture: %s", exc)
            return None
        logger.debug("Captured image saved to %s", save_path)
        return str(save_path)

    def _write_simulated_frame(self, save_path: Path) -> str:
        """Write a placeholder image when running in simulated mode."""
        try:
            from PIL import Image, ImageDraw  # type: ignore

            img = Image.new("RGB", (640, 480), color=(0, 100, 0))
            draw = ImageDraw.Draw(img)
            draw.text((10, 10), f"SIMULATED CAPTURE\n{datetime.now().isoformat()}",
                      fill=(255, 255, 255))
            img.save(save_path, format="JPEG")
            logger.debug("SIMULATE capture saved to %s", save_path)
            return str(save_path)
        except Exception:
            save_path.write_bytes(b"")
            return str(save_path)

    # ------------------------------------------------------------------
    # Streaming (dashboard live view)
    # ------------------------------------------------------------------

    def stream(self, fps: float | None = None) -> Iterator[bytes]:
        """Yield JPEG-encoded frames for the dashboard's MJPEG endpoint.

        Blocks until the client disconnects or the device drops out.
        Frames are read under the capture lock but encoded outside it, so
        a slow encoder never blocks pest-detection captures for long.
        """
        if self._cap is None or self._degraded or self.simulate or self._cv2 is None:
            return

        target_fps = fps or self.actual_fps or self.fps or 10.0
        frame_interval = 1.0 / max(1.0, float(target_fps))
        cv2 = self._cv2

        while True:
            started = time.monotonic()
            ok, frame = self._guarded_read()
            if not ok or frame is None:
                # Device dropped out mid-stream. Stop; the frontend
                # surfaces the reason from /api/camera/status.
                logger.warning("Camera stream ended: no frame available")
                return
            try:
                encoded_ok, buffer = cv2.imencode(".jpg", frame)
            except Exception as exc:
                logger.error("Failed to encode frame: %s", exc)
                return
            if not encoded_ok:
                return
            yield buffer.tobytes()

            elapsed = time.monotonic() - started
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "configured": self._cap is not None and not self._degraded,
            "name": self.name,
            "device_index": self.device_index,
            "device_path": self.device_path,
            "width": self.actual_width,
            "height": self.actual_height,
            "fps": self.actual_fps,
            "simulated": self.simulate,
            "error": self.error,
            "message": self._status_message(),
            "tried_devices": list(self.tried_devices),
        }

    def _status_message(self) -> str:
        if self._closed:
            return "Camera closed — no device open"
        if self._degraded:
            return f"Camera unavailable — {self.error}"
        if self.simulate:
            return "Simulated camera — no real device attached"
        resolution = ""
        if self.actual_width and self.actual_height:
            resolution = f" at {self.actual_width}x{self.actual_height}"
        return f"Connected{resolution}"

    def close(self) -> None:
        with self._cap_lock:
            self._closed = True
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
