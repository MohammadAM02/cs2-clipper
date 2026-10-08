"""Fakes shared by more than one test module."""

from __future__ import annotations

import os


def refusing(times: int):
    """An os.replace that Windows refuses `times` times ("in use") before it goes through."""
    real, left = os.replace, [times]

    def replace(src, dst):
        if left[0]:
            left[0] -= 1
            raise PermissionError(13, "Access is denied")
        real(src, dst)
    return replace
