"""Pure-Python Unix crypt(3) for the schemes GL.iNet routers use.

GL.iNet's firmware-4.x ``challenge`` step returns an ``alg`` field selecting one
of three modular-crypt schemes:

* ``alg=1`` -> md5_crypt   (``$1$``)
* ``alg=5`` -> sha256_crypt (``$5$``)
* ``alg=6`` -> sha512_crypt (``$6$``)

The login hash is then ``HASH(f"{user}:{crypt_output}:{nonce}")``.

Home Assistant may run on Python 3.13+, where the stdlib ``crypt`` module has been
removed, so this module implements the three schemes directly with ``hashlib`` and
has **no third-party dependency**. The algorithms are the well-known reference
implementations (Poul-Henning Kamp's MD5 crypt and Ulrich Drepper's SHA-crypt);
the test suite cross-checks the output byte-for-byte against stdlib ``crypt`` where
that module is available.
"""

from __future__ import annotations

import hashlib

# crypt(3) uses its own non-standard base64 alphabet ("./0-9A-Za-z").
_B64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _b64_from_24bit(b2: int, b1: int, b0: int, n: int) -> str:
    """Encode three bytes into ``n`` crypt-base64 chars (little-endian groups)."""
    w = (b2 << 16) | (b1 << 8) | b0
    out = []
    for _ in range(n):
        out.append(_B64[w & 0x3F])
        w >>= 6
    return "".join(out)


def md5_crypt(password: str, salt: str) -> str:
    """Compute FreeBSD/Linux ``$1$`` md5_crypt."""
    pw = password.encode()
    salt = salt[:8]
    salt_b = salt.encode()
    magic = b"$1$"

    ctx = hashlib.md5(pw + magic + salt_b)

    alt = hashlib.md5(pw + salt_b + pw).digest()
    pw_len = len(pw)
    for i in range(pw_len, 0, -16):
        ctx.update(alt[: min(16, i)])

    i = pw_len
    while i:
        ctx.update(b"\x00" if i & 1 else pw[:1])
        i >>= 1

    final = ctx.digest()

    for i in range(1000):
        c = hashlib.md5()
        c.update(pw if i & 1 else final)
        if i % 3:
            c.update(salt_b)
        if i % 7:
            c.update(pw)
        c.update(final if i & 1 else pw)
        final = c.digest()

    out = (
        _b64_from_24bit(final[0], final[6], final[12], 4)
        + _b64_from_24bit(final[1], final[7], final[13], 4)
        + _b64_from_24bit(final[2], final[8], final[14], 4)
        + _b64_from_24bit(final[3], final[9], final[15], 4)
        + _b64_from_24bit(final[4], final[10], final[5], 4)
        + _b64_from_24bit(0, 0, final[11], 2)
    )
    return f"$1${salt}${out}"


def _sha_crypt(password: str, salt: str, bits: int) -> str:
    """Ulrich Drepper SHA-crypt for ``$5$`` (256) and ``$6$`` (512)."""
    if bits == 256:
        hfun = hashlib.sha256
        magic = "$5$"
        dlen = 32
        # Output permutation order for sha256_crypt.
        order = [
            (0, 10, 20), (21, 1, 11), (12, 22, 2), (3, 13, 23),
            (24, 4, 14), (15, 25, 5), (6, 16, 26), (27, 7, 17),
            (18, 28, 8), (9, 19, 29),
        ]
        # Final group: b64_from_24bit(0, buf[31], buf[30], 3)
        tail = (None, 31, 30, 3)
    else:
        hfun = hashlib.sha512
        magic = "$6$"
        dlen = 64
        order = [
            (0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45),
            (25, 46, 4), (47, 5, 26), (6, 27, 48), (28, 49, 7),
            (50, 8, 29), (9, 30, 51), (31, 52, 10), (53, 11, 32),
            (12, 33, 54), (34, 55, 13), (56, 14, 35), (15, 36, 57),
            (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19),
            (62, 20, 41),
        ]
        # Final group: b64_from_24bit(0, 0, buf[63], 2)
        tail = (None, None, 63, 2)

    pw = password.encode()
    pw_len = len(pw)
    salt = salt[:16]
    salt_b = salt.encode()
    salt_len = len(salt_b)

    # Digest B
    b = hfun(pw + salt_b + pw).digest()

    # Digest A
    ctx = hashlib.new(hfun().name)
    a = hfun()
    a.update(pw + salt_b)
    for i in range(pw_len, 0, -dlen):
        a.update(b[: min(dlen, i)])
    i = pw_len
    while i:
        a.update(b if i & 1 else pw)
        i >>= 1
    a_digest = a.digest()

    # DP sequence
    dp = hfun()
    for _ in range(pw_len):
        dp.update(pw)
    dp_digest = dp.digest()
    p = b"".join(
        dp_digest[: min(dlen, pw_len - i)] for i in range(0, pw_len, dlen)
    )

    # DS sequence
    ds = hfun()
    for _ in range(16 + a_digest[0]):
        ds.update(salt_b)
    ds_digest = ds.digest()
    s = b"".join(
        ds_digest[: min(dlen, salt_len - i)] for i in range(0, salt_len, dlen)
    )

    # 5000 rounds
    c = a_digest
    for i in range(5000):
        ctx = hfun()
        ctx.update(p if i & 1 else c)
        if i % 3:
            ctx.update(s)
        if i % 7:
            ctx.update(p)
        ctx.update(c if i & 1 else p)
        c = ctx.digest()

    out = "".join(_b64_from_24bit(c[x], c[y], c[z], 4) for x, y, z in order)
    t2, t1, t0, tn = tail
    out += _b64_from_24bit(
        0 if t2 is None else c[t2],
        0 if t1 is None else c[t1],
        0 if t0 is None else c[t0],
        tn,
    )
    return f"{magic}{salt}${out}"


def sha256_crypt(password: str, salt: str) -> str:
    """Compute ``$5$`` sha256_crypt (default 5000 rounds, no explicit rounds=)."""
    return _sha_crypt(password, salt, 256)


def sha512_crypt(password: str, salt: str) -> str:
    """Compute ``$6$`` sha512_crypt (default 5000 rounds, no explicit rounds=)."""
    return _sha_crypt(password, salt, 512)


# alg -> (scheme function)
_ALG_FUNCS = {
    1: md5_crypt,
    5: sha256_crypt,
    6: sha512_crypt,
}


def crypt_password(password: str, alg: int, salt: str) -> str:
    """Return the full crypt(3) string for the given GL.iNet ``alg`` and salt."""
    try:
        func = _ALG_FUNCS[alg]
    except KeyError as err:
        raise ValueError(f"Unsupported crypt alg: {alg}") from err
    return func(password, salt)
