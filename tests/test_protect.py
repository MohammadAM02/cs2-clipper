"""Tests for clipper.protect: DPAPI round trip and failure handling."""
from __future__ import annotations

import base64

import pytest

from clipper.protect import ProtectError, protect, unprotect


def test_round_trip_including_a_non_ascii_secret():
    secret = "sekrit-ключ-\U0001f511"

    assert unprotect(protect(secret)) == secret


def test_the_stored_text_does_not_contain_the_secret_or_its_base64():
    secret = "super-secret-faceit-api-key"

    blob = protect(secret)

    assert secret not in blob
    assert base64.b64encode(secret.encode("utf-8")).decode("ascii") not in blob


def test_unprotect_of_text_that_is_not_base64_raises_protecterror_without_echoing_it():
    bad = "this is not base64 at all!!"

    with pytest.raises(ProtectError) as excinfo:
        unprotect(bad)

    assert bad not in str(excinfo.value)


def test_unprotect_of_valid_base64_that_is_not_a_dpapi_blob_raises_protecterror_without_echoing_it():
    bad = base64.b64encode(b"not a real dpapi blob").decode("ascii")

    with pytest.raises(ProtectError) as excinfo:
        unprotect(bad)

    assert bad not in str(excinfo.value)
