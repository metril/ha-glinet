"""Tests for the pure-Python crypt(3) implementation.

The crypt output IS the security-critical input to the GL.iNet login hash, so
these golden vectors were generated from stdlib ``crypt`` and are checked
byte-for-byte. Where stdlib ``crypt`` is available (< Python 3.13) we also fuzz
against it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet.crypt_util import (
    crypt_password,
    md5_crypt,
    sha256_crypt,
    sha512_crypt,
)

# (alg, password, salt, expected) — generated from stdlib crypt.crypt().
GOLDEN = [
    (1, "password", "shGzEq91", "$1$shGzEq91$86Ts2Bhw.zMhuQ93Gy1lO0"),
    (5, "password", "shGzEq91", "$5$shGzEq91$Tqc4I/AgMFNOyctx7edRMpOMK.40e9RhI7SV51NRWF6"),
    (6, "password", "shGzEq91", "$6$shGzEq91$56fPxAw8dTIB0sPee31Bg4MI6duRm.DsXBDpA2R5CQxQmSFrnvGu2yPoLQEoZTbaVLmC2dOXA0f1WR8WkY1iI1"),
    (1, "admin", "abcd1234", "$1$abcd1234$qa9TGkIrcStkIZ/xzypaE1"),
    (5, "admin", "abcd1234", "$5$abcd1234$PcMPqYHcVJiwJ.ZD5OYXKe5pLJs7g4V.dpKcSrKC/lB"),
    (6, "admin", "abcd1234", "$6$abcd1234$b/Nfh8F4.9BPpF53qJP0VWR6cMQluXG1vef4jLpxqBJEsRsMwE3CjEAYj8miWZhXMXgv5I5FVRLFuLt4Qz4u30"),
    (1, "hunter2", "Xy", "$1$Xy$LY3MbikN/uzGGVzrOkovt/"),
    (5, "hunter2", "Xy", "$5$Xy$hU75Fmm9c6wTmy2ymqzS.dSt.UguTvj3LwnzPyxrABD"),
    (6, "hunter2", "Xy", "$6$Xy$FbHFetwCHundD0fuIAuqaADdFNsYh218PbqszX153nNK.71Y8.J15jfX6FTbRjyW/WC7JLPRi4aL53X5QZmHV1"),
    (1, "", "saltsalt", "$1$saltsalt$5Jhcit4zN9UlGiA0txPkO0"),
    (5, "", "saltsalt", "$5$saltsalt$09agN5RZ2meWdEdnEusqsq5G7RwwghB8jCKoWWADxW/"),
    (6, "", "saltsalt", "$6$saltsalt$qkTgsCrWMTAS9gBGcf9W60sFfH.hU0oTCAOJjhbz5tSp/sU3/xXZK4OFwCtq8lIIdpJ6CatVdOTSHKp97TPkt/"),
    (1, "unicode-pw", "abcDEF09", "$1$abcDEF09$k8IAxRB2KThU9v1FRqYq4/"),
    (5, "unicode-pw", "abcDEF09", "$5$abcDEF09$EQJCU8COixLTreAUs5sj5xRmvDeGdMwMSF1tZxJVCK3"),
    (6, "unicode-pw", "abcDEF09", "$6$abcDEF09$IeSExeUrlh3E/jCpC7ZM04lcllEhhq/T9hFBzvhtxA9K/qIecS7rn.ju5weC6CCLK4GTpp0YqueVB5R4KNF9w1"),
]


@pytest.mark.parametrize("alg, password, salt, expected", GOLDEN)
def test_golden_vectors(alg, password, salt, expected):
    assert crypt_password(password, alg, salt) == expected


def test_dispatch_matches_named_functions():
    assert crypt_password("x", 1, "abcd1234") == md5_crypt("x", "abcd1234")
    assert crypt_password("x", 5, "abcd1234") == sha256_crypt("x", "abcd1234")
    assert crypt_password("x", 6, "abcd1234") == sha512_crypt("x", "abcd1234")


def test_unsupported_alg():
    with pytest.raises(ValueError):
        crypt_password("x", 99, "abcd1234")


def test_fuzz_against_stdlib_if_available():
    """On Python < 3.13, fuzz our output against stdlib crypt."""
    crypt = pytest.importorskip("crypt")
    import random

    rng = random.Random(1234)
    alphabet = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    for _ in range(30):
        pw = "".join(rng.choice("abcABC123!@# ") for _ in range(rng.randint(0, 20)))
        salt = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 16)))
        for alg, fn, prefix in [
            (1, md5_crypt, "$1$"),
            (5, sha256_crypt, "$5$"),
            (6, sha512_crypt, "$6$"),
        ]:
            trimmed = salt[:8] if alg == 1 else salt[:16]
            assert fn(pw, salt) == crypt.crypt(pw, f"{prefix}{trimmed}")
