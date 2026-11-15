"""cv2.resize must not read past the end of the source image.

Builds with KleidiCV 26.03 (opencv-python-headless 4.14.0.94 and 5.0.0.93 on Apple Silicon) do,
with INTER_LINEAR when both sides shrink slightly, which is what RapidOCR's text detection does to a
rendered PDF page. Whether that
crashes depends on what memory follows the image, so it crashed the OCR worker only now and then
(SIGSEGV in kleidicv_resize_generic_stripe_u8). Here the image ends exactly at a page that is
mapped without access (the Electric Fence technique), so any such read crashes every time. The
check runs in a child process so that a crash fails this test instead of ending the test run."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX mprotect")

CHECK = r"""
import ctypes, mmap, sys
import numpy as np, cv2

libc = ctypes.CDLL(None, use_errno=True)
libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
PAGE = mmap.PAGESIZE
height, width, channels, out_w, out_h = map(int, sys.argv[1:])
size = height * width * channels
pages = -(-size // PAGE)
memory = mmap.mmap(-1, (pages + 1) * PAGE)
start = ctypes.addressof(ctypes.c_char.from_buffer(memory))
if libc.mprotect(start + pages * PAGE, PAGE, 0) != 0:  # PROT_NONE on the page after the image
    raise OSError(ctypes.get_errno(), "mprotect")
image = np.frombuffer(memory, np.uint8, size, pages * PAGE - size).reshape(height, width, channels)
image[...] = 128
cv2.resize(image, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
"""


@pytest.mark.parametrize(
    ("height", "width", "channels", "out_w", "out_h"),
    [
        # RapidOCR detection input for a page rendered from aws.pdf: 1836x2376 to 1824x2368.
        (2376, 1836, 3, 1824, 2368),
        (2376, 1836, 1, 1824, 2368),
        # Recognition input: a text line scaled to height 48.
        (79, 452, 3, 275, 48),
    ],
)
def test_resize_stays_inside_the_image(height, width, channels, out_w, out_h):
    result = subprocess.run(  # noqa: S603  Our own interpreter and script.
        [sys.executable, "-c", CHECK, *map(str, (height, width, channels, out_w, out_h))],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"cv2.resize read past the end of the image (exit {result.returncode}); "
        f"check the opencv-python-headless version: {result.stderr[-500:]}"
    )
