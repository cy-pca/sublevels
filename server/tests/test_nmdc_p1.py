"""P1: the code page, escaping, $Lock and $Key, and the per-connection limits."""

import pytest

from nmdc_fakes import World
from sublevels.nmdc import HubConfig, Limits
from sublevels.nmdc.codec import (CodePage, dcn_escape, dcn_unescape, escape, key_matches,
                                  lock_to_key, make_lock, unescape)


def test_cp1252_round_trips_every_byte():
    cp = CodePage("cp1252")
    every = bytes(range(256))
    assert cp.encode(cp.decode(every)) == every


def test_cp1252_unassigned_bytes_decode_to_their_own_code_point():
    cp = CodePage("cp1252")
    assert cp.decode(bytes([0x81, 0x8D, 0x8F, 0x90, 0x9D])) == "\x81\x8d\x8f\x90\x9d"
    assert cp.decode(b"\x80") == "€"


def test_cp1252_writes_question_marks_for_what_it_cannot_hold():
    cp = CodePage("cp1252")
    assert cp.encode("é€ ok") == b"\xe9\x80 ok"
    assert cp.encode("snow ☃ and 漢字") == b"snow ? and ??"
    assert cp.encode("\x80") == b"?"  # U+0080 is not in Windows-1252 (0x80 is the euro sign)
    assert b"&#" not in cp.encode("☃")


def test_other_single_byte_code_pages_and_no_multibyte():
    assert CodePage("cp1251").decode(b"\xc0") == "А"
    with pytest.raises(ValueError):
        CodePage("utf-8")


def test_escape_and_unescape():
    assert escape("a$b|c&d") == "a&#36;b&#124;c&amp;d"
    assert unescape("a&#36;b&#124;c&amp;d") == "a$b|c&d"
    assert unescape("/%DCN036%/ /%DCN124%/") == "$ |"
    assert unescape("&lt;kept&gt; /%DCN005%/") == "&lt;kept&gt; /%DCN005%/"  # nothing else
    assert unescape(escape("&#36; literal")) == "&#36; literal"


def test_dcn_round_trip():
    raw = bytes([0, 5, 36, 96, 124, 126, 65])
    assert dcn_escape(raw) == (b"/%DCN000%//%DCN005%//%DCN036%//%DCN096%//%DCN124%/"
                               b"/%DCN126%/A")
    assert dcn_unescape(dcn_escape(raw)) == raw


def test_lock_shape():
    lock = make_lock()
    assert lock.startswith(b"EXTENDEDPROTOCOL_sublevels_")
    tail = lock[len(b"EXTENDEDPROTOCOL_sublevels_"):]
    assert len(tail) == 24 and tail.isalnum()
    assert all(37 <= b <= 122 for b in lock) and b" " not in lock
    assert make_lock() != lock


def _key_by_hand(lock: bytes) -> bytes:
    """P1 §5's four steps, written out separately from the hub's code."""
    n = len(lock)
    k = [0] * n
    k[0] = lock[0] ^ lock[n - 1] ^ lock[n - 2] ^ 5
    for i in range(1, n):
        k[i] = lock[i] ^ lock[i - 1]
    out = b""
    for v in k:
        v = ((v & 0x0F) << 4) | ((v & 0xF0) >> 4)
        out += b"/%%DCN%03d%%/" % v if v in (0, 5, 36, 96, 124, 126) else bytes([v])
    return out


def test_key_follows_the_four_steps():
    for lock in (make_lock() for _ in range(50)):
        assert lock_to_key(lock) == _key_by_hand(lock)
    assert lock_to_key(b"AB") == _key_by_hand(b"AB")


def test_key_needs_the_whole_key():
    lock = make_lock()
    key = lock_to_key(lock)
    assert key_matches(lock, key)
    assert key_matches(lock, dcn_unescape(key))  # a client that escapes less still matches
    assert not key_matches(lock, key[:-1])


def test_wrong_key_is_counted_and_tolerated_by_default():
    w = World()
    s, conn = w.connect()
    w.send(s, "$Supports NoHello|", "$Key wrong|", "$ValidateNick alice|")
    assert s.state == "info"
    assert w.hub.counters.totals["key_mismatch"] == 1


def test_wrong_key_closes_when_strict():
    w = World(HubConfig(strict_key=True))
    s, conn = w.connect()
    w.send(s, "$Key wrong|")
    assert conn.closed


def test_keepalive_and_unknown_commands_are_ignored():
    w = World()
    s, conn = w.login("alice")
    conn.take()
    w.send(s, "|", "$UnknownExtension x y|", "$Supports Later|")
    assert conn.take() == [] and not conn.closed


def test_malformed_messages_close_after_the_limit():
    w = World(HubConfig(limits=Limits(malformed_per_minute=3)))
    s, conn = w.login("alice")
    for _ in range(3):
        w.send(s, "junk|")
    assert not conn.closed
    w.send(s, "junk|")
    assert conn.closed
    assert w.hub.counters.drops["malformed"] == 4


def test_received_bytes_limit_online():
    w = World(HubConfig(limits=Limits(received_bytes=1000)))
    s, conn = w.login("alice")
    assert w.hub.received(s, 600)
    assert not w.hub.received(s, 600)
    assert conn.closed
    w2 = World(HubConfig(limits=Limits(received_bytes=1000)))
    s2, conn2 = w2.login("bob")
    w2.hub.received(s2, 600)
    w2.clock.advance(11)
    assert w2.hub.received(s2, 600)  # the window moved on


def test_message_length_limits_by_state():
    w = World()
    s, _ = w.connect()
    assert w.hub.max_message(s) == 4096
    s2, _ = w.login("bob")
    assert w.hub.max_message(s2) == 65536
