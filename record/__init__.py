"""X11 window capture + Chromium CDP driver.

CDP/HTTP only drive the UI; the video is XGetImage of the live window, piped to ffmpeg.
"""

from .session import RecordSession
from .window import WindowRecord

__all__ = ["RecordSession", "WindowRecord"]
