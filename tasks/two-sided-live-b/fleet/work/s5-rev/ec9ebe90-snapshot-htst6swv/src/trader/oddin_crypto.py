"""Oddin `.js2cc*` envelope: XChaCha20-Poly1305 then klauspost S2, then JSON."""

import base64
import hmac
import json
import os
import struct
from typing import cast

SIGMA = b"expand 32-byte k"
POLY1305_P = (1 << 130) - 5
POLY1305_MASK = 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
NONCE_SIZE = 24
TAG_SIZE = 16
KEY_SIZE = 32
ENVELOPE_PREFIX = ".js2cc*"
MIN_BOX_SIZE = NONCE_SIZE + TAG_SIZE
S2_MAX_DECODED = 4 << 20
DEFAULT_KEY_HEX = "136323044a5be45aa46c66bb953d84bffbe2f976d8de00a393b87495b010027b"
KEY_ENV = "ODDIN_FEED_KEY_HEX"
AUTH_FAILED = "Oddin authentication failed; Oddin may have replaced the key in a new public WASM"


class OddinCryptoError(ValueError):
    """Envelope prefix, key, authentication, S2, or JSON failed."""


def load_feed_key() -> bytes:
    """32-byte key from `ODDIN_FEED_KEY_HEX`, or the current public WASM key."""
    hex_key = os.environ.get(KEY_ENV, DEFAULT_KEY_HEX)
    return parse_feed_key(hex_key)


def parse_feed_key(hex_key: str) -> bytes:
    """Decode a 64-char hex key."""
    try:
        key = bytes.fromhex(hex_key)
    except ValueError as exc:
        raise OddinCryptoError("Oddin feed key must be hex") from exc
    if len(key) != KEY_SIZE:
        raise OddinCryptoError(f"Oddin feed key must be {KEY_SIZE} bytes")
    return key


def _rotl32(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotl32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotl32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotl32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotl32(state[b] ^ state[c], 7)


def _chacha20_rounds(state: list[int]) -> None:
    for _ in range(10):
        _quarter_round(state, 0, 4, 8, 12)
        _quarter_round(state, 1, 5, 9, 13)
        _quarter_round(state, 2, 6, 10, 14)
        _quarter_round(state, 3, 7, 11, 15)
        _quarter_round(state, 0, 5, 10, 15)
        _quarter_round(state, 1, 6, 11, 12)
        _quarter_round(state, 2, 7, 8, 13)
        _quarter_round(state, 3, 4, 9, 14)


def chacha20_block(key: bytes, counter: int, nonce12: bytes) -> bytes:
    """One IETF ChaCha20 block."""
    state = list(struct.unpack("<4I", SIGMA))
    state += list(struct.unpack("<8I", key))
    state.append(counter & 0xFFFFFFFF)
    state += list(struct.unpack("<3I", nonce12))
    working = state[:]
    _chacha20_rounds(working)
    mixed = [(working[index] + state[index]) & 0xFFFFFFFF for index in range(16)]
    return struct.pack("<16I", *mixed)


def hchacha20(key: bytes, nonce16: bytes) -> bytes:
    """HChaCha20 subkey from a 32-byte key and the first 16 nonce bytes."""
    state = list(struct.unpack("<4I", SIGMA))
    state += list(struct.unpack("<8I", key))
    state += list(struct.unpack("<4I", nonce16))
    _chacha20_rounds(state)
    return struct.pack("<4I", *state[:4]) + struct.pack("<4I", *state[12:])


def chacha20_xor(key: bytes, counter: int, nonce12: bytes, data: bytes) -> bytes:
    """XOR `data` with the ChaCha20 keystream starting at `counter`."""
    out = bytearray()
    offset = 0
    block_counter = counter
    while offset < len(data):
        block = chacha20_block(key, block_counter, nonce12)
        take = min(64, len(data) - offset)
        out.extend(a ^ b for a, b in zip(data[offset : offset + take], block[:take], strict=True))
        offset += take
        block_counter = (block_counter + 1) & 0xFFFFFFFF
    return bytes(out)


def _pad16(data: bytes) -> bytes:
    return data + b"\x00" * ((-len(data)) % 16)


def poly1305_tag(one_time_key: bytes, message: bytes) -> bytes:
    """Poly1305 tag for `message` using a 32-byte one-time key."""
    clamped_r = int.from_bytes(one_time_key[:16], "little") & POLY1305_MASK
    s_const = int.from_bytes(one_time_key[16:], "little")
    acc = 0
    for index in range(0, len(message), 16):
        block = message[index : index + 16]
        n_value = int.from_bytes(block + b"\x01", "little")
        acc = ((acc + n_value) * clamped_r) % POLY1305_P
    return ((acc + s_const) % (1 << 128)).to_bytes(16, "little")


def _poly1305_message(aad: bytes, ciphertext: bytes) -> bytes:
    return _pad16(aad) + _pad16(ciphertext) + struct.pack("<QQ", len(aad), len(ciphertext))


def _xchacha_subkey_and_nonce(key: bytes, nonce: bytes) -> tuple[bytes, bytes]:
    return hchacha20(key, nonce[:16]), b"\x00\x00\x00\x00" + nonce[16:]


def decrypt_xchacha20_poly1305(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes:
    """Decrypt IETF XChaCha20-Poly1305 with empty AAD."""
    return decrypt_xchacha20_poly1305_aad(key, nonce, ciphertext, tag, b"")


def encrypt_xchacha20_poly1305(key: bytes, nonce: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    """Encrypt IETF XChaCha20-Poly1305 with empty AAD. Returns (ciphertext, tag)."""
    subkey, nonce12 = _xchacha_subkey_and_nonce(key, nonce)
    one_time_key = chacha20_block(subkey, 0, nonce12)[:32]
    ciphertext = chacha20_xor(subkey, 1, nonce12, plaintext)
    tag = poly1305_tag(one_time_key, _poly1305_message(b"", ciphertext))
    return ciphertext, tag


def decrypt_xchacha20_poly1305_aad(
    key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes, aad: bytes
) -> bytes:
    """Decrypt IETF XChaCha20-Poly1305. Used for the public test vector."""
    subkey, nonce12 = _xchacha_subkey_and_nonce(key, nonce)
    one_time_key = chacha20_block(subkey, 0, nonce12)[:32]
    expected = poly1305_tag(one_time_key, _poly1305_message(aad, ciphertext))
    if not hmac.compare_digest(expected, tag):
        raise OddinCryptoError(AUTH_FAILED)
    return chacha20_xor(subkey, 1, nonce12, ciphertext)


def _uvarint(src: bytes) -> tuple[int, int]:
    value = 0
    shift = 0
    for index, byte in enumerate(src):
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, index + 1
        shift += 7
        if shift >= 64:
            raise OddinCryptoError("s2 varint is too long")
    raise OddinCryptoError("s2 varint is truncated")


def _put_uvarint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _s2_literal(src: bytes, src_pos: int) -> tuple[int, int]:
    extra = src[src_pos] >> 2
    if extra < 60:
        return extra + 1, src_pos + 1
    if extra == 60:
        return src[src_pos + 1] + 1, src_pos + 2
    if extra == 61:
        return (src[src_pos + 1] | src[src_pos + 2] << 8) + 1, src_pos + 3
    if extra == 62:
        length = src[src_pos + 1] | src[src_pos + 2] << 8 | src[src_pos + 3] << 16
        return length + 1, src_pos + 4
    length = (
        src[src_pos + 1] | src[src_pos + 2] << 8 | src[src_pos + 3] << 16 | src[src_pos + 4] << 24
    )
    return length + 1, src_pos + 5


def _s2_copy(src: bytes, src_pos: int, tag: int, offset: int) -> tuple[int, int, int]:
    if tag == 1:
        length = (src[src_pos] >> 2) & 0x07
        copy_offset = ((src[src_pos] & 0xE0) << 3) | src[src_pos + 1]
        src_pos += 2
        if copy_offset == 0:
            if length == 5:
                length = src[src_pos] + 4
                src_pos += 1
            elif length == 6:
                length = (src[src_pos] | src[src_pos + 1] << 8) + 256
                src_pos += 2
            elif length == 7:
                length = (src[src_pos] | src[src_pos + 1] << 8 | src[src_pos + 2] << 16) + 65536
                src_pos += 3
        else:
            offset = copy_offset
        return length + 4, offset, src_pos
    if tag == 2:
        length = 1 + (src[src_pos] >> 2)
        offset = src[src_pos + 1] | src[src_pos + 2] << 8
        return length, offset, src_pos + 3
    length = 1 + (src[src_pos] >> 2)
    offset = (
        src[src_pos + 1] | src[src_pos + 2] << 8 | src[src_pos + 3] << 16 | src[src_pos + 4] << 24
    )
    return length, offset, src_pos + 5


def decode_s2(src: bytes) -> bytes:
    """Decode one klauspost S2 block (no dictionary)."""
    decoded_len, header_len = _uvarint(src)
    if decoded_len > S2_MAX_DECODED:
        raise OddinCryptoError("s2 decoded block is too large")
    src = src[header_len:]
    dst = bytearray(decoded_len)
    dst_pos = 0
    src_pos = 0
    offset = 0
    while src_pos < len(src):
        tag = src[src_pos] & 0x03
        if tag == 0:
            length, src_pos = _s2_literal(src, src_pos)
            dst[dst_pos : dst_pos + length] = src[src_pos : src_pos + length]
            dst_pos += length
            src_pos += length
            continue
        length, offset, src_pos = _s2_copy(src, src_pos, tag, offset)
        if offset <= 0 or length > decoded_len - dst_pos:
            raise OddinCryptoError("s2 copy is corrupt")
        for index in range(length):
            dst[dst_pos + index] = dst[dst_pos - offset + index]
        dst_pos += length
    if dst_pos != decoded_len:
        raise OddinCryptoError("s2 decoded length mismatch")
    return bytes(dst)


def encode_s2(data: bytes) -> bytes:
    """Literal-only S2 block, for fixtures."""
    out = bytearray(_put_uvarint(len(data)))
    offset = 0
    while offset < len(data):
        take = min(len(data) - offset, 1 << 16)
        extra = take - 1
        if extra < 60:
            out.append(extra << 2)
        elif extra < 256:
            out.append(60 << 2)
            out.append(extra)
        else:
            out.append(61 << 2)
            out.append(extra & 0xFF)
            out.append(extra >> 8)
        out.extend(data[offset : offset + take])
        offset += take
    return bytes(out)


def open_envelope(envelope: str, key: bytes) -> dict[str, object]:
    """Decrypt a `.js2cc*` scoreboard payload to a JSON object."""
    if not envelope.startswith(ENVELOPE_PREFIX):
        raise OddinCryptoError("Oddin envelope must start with .js2cc*")
    try:
        boxed = base64.b64decode(envelope[len(ENVELOPE_PREFIX) :], validate=True)
    except ValueError as exc:
        raise OddinCryptoError("Oddin envelope is not valid Base64") from exc
    if len(boxed) < MIN_BOX_SIZE:
        raise OddinCryptoError("Oddin envelope is too short")
    nonce = boxed[:NONCE_SIZE]
    tag = boxed[-TAG_SIZE:]
    ciphertext = boxed[NONCE_SIZE:-TAG_SIZE]
    try:
        compressed = decrypt_xchacha20_poly1305(key, nonce, ciphertext, tag)
    except Exception as exc:
        if isinstance(exc, OddinCryptoError):
            raise
        raise OddinCryptoError(AUTH_FAILED) from exc
    try:
        plaintext = decode_s2(compressed)
    except OddinCryptoError:
        raise
    except Exception as exc:
        raise OddinCryptoError("s2 decoded block is corrupt") from exc
    try:
        parsed = json.loads(plaintext)
    except ValueError as exc:
        raise OddinCryptoError("Oddin plaintext is not JSON") from exc
    if type(parsed) is not dict:
        raise OddinCryptoError("Oddin plaintext JSON must be an object")
    return cast(dict[str, object], parsed)


def seal_envelope(payload: dict[str, object], key: bytes, nonce: bytes) -> str:
    """Build a `.js2cc*` envelope. Test helper."""
    ciphertext, tag = encrypt_xchacha20_poly1305(
        key, nonce, encode_s2(json.dumps(payload).encode())
    )
    boxed = nonce + ciphertext + tag
    return ENVELOPE_PREFIX + base64.b64encode(boxed).decode("ascii")
