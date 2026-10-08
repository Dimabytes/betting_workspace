"""Oddin envelope: XChaCha20-Poly1305, S2, and `.js2cc*` framing."""

import pytest

from trader.oddin_crypto import (
    AUTH_FAILED,
    DEFAULT_KEY_HEX,
    OddinCryptoError,
    decode_s2,
    decrypt_xchacha20_poly1305_aad,
    encode_s2,
    open_envelope,
    parse_feed_key,
    seal_envelope,
)

RFC_KEY = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
RFC_NONCE = bytes.fromhex("404142434445464748494a4b4c4d4e4f5051525354555657")
RFC_AAD = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
RFC_PLAINTEXT = (
    b"Ladies and Gentlemen of the class of '99: If I could offer you only one "
    b"tip for the future, sunscreen would be it."
)
RFC_CIPHERTEXT = bytes.fromhex(
    "bd6d179d3e83d43b9576579493c0e939572a1700252bfaccbed2902c21396cbb"
    "731c7f1b0b4aa6440bf3a82f4eda7e39ae64c6708c54c216cb96b72e1213b452"
    "2f8c9ba40db5d945b11b69b982c1bb9e3f3fac2bc369488f76b2383565d3fff9"
    "21f9664c97637da9768812f615c68b13b52e"
)
RFC_TAG = bytes.fromhex("c0875924c1c7987947deafd8780acf49")


def test_rfc_xchacha20_poly1305_vector() -> None:
    assert (
        decrypt_xchacha20_poly1305_aad(RFC_KEY, RFC_NONCE, RFC_CIPHERTEXT, RFC_TAG, RFC_AAD)
        == RFC_PLAINTEXT
    )


def test_corrupted_tag_is_authentication_error() -> None:
    bad_tag = RFC_TAG[:-1] + bytes([RFC_TAG[-1] ^ 1])
    with pytest.raises(OddinCryptoError, match="authentication failed"):
        decrypt_xchacha20_poly1305_aad(RFC_KEY, RFC_NONCE, RFC_CIPHERTEXT, bad_tag, RFC_AAD)


def test_s2_literal_roundtrip() -> None:
    assert decode_s2(encode_s2(b'{"ok":true}')) == b'{"ok":true}'


def test_s2_copy_tag() -> None:
    """Uncompressed `abcabc`: literal `abc` then a 3-byte copy at offset 3."""
    block = bytes([6, (2 << 2), 97, 98, 99, (2 << 2) | 2, 3, 0])
    assert decode_s2(block) == b"abcabc"


def test_envelope_roundtrip() -> None:
    key = parse_feed_key(DEFAULT_KEY_HEX)
    nonce = bytes(range(24))
    envelope = seal_envelope({"matchStatus": "LIVE", "homeScore": 1}, key, nonce)
    assert envelope.startswith(".js2cc*")
    assert open_envelope(envelope, key) == {"matchStatus": "LIVE", "homeScore": 1}


def test_wrong_prefix() -> None:
    with pytest.raises(OddinCryptoError, match=r"\.js2cc\*"):
        open_envelope("not-an-envelope", parse_feed_key(DEFAULT_KEY_HEX))


def test_short_envelope() -> None:
    with pytest.raises(OddinCryptoError, match="too short"):
        open_envelope(".js2cc*AAAA", parse_feed_key(DEFAULT_KEY_HEX))


def test_invalid_base64() -> None:
    with pytest.raises(OddinCryptoError, match="Base64"):
        open_envelope(".js2cc*@@@@", parse_feed_key(DEFAULT_KEY_HEX))


def test_wrong_key_length() -> None:
    with pytest.raises(OddinCryptoError, match="32 bytes"):
        parse_feed_key("aa")


def test_corrupted_envelope_tag() -> None:
    key = parse_feed_key(DEFAULT_KEY_HEX)
    envelope = seal_envelope({"a": 1}, key, bytes(range(24)))
    flipped = envelope[:-1] + ("A" if envelope[-1] != "A" else "B")
    with pytest.raises(OddinCryptoError, match=AUTH_FAILED.split(";")[0]):
        open_envelope(flipped, key)
