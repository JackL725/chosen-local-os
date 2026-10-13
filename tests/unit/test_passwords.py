from __future__ import annotations

import pytest

from review import passwords


def test_hash_and_verify(monkeypatch):
    monkeypatch.setattr(passwords, "DEFAULT_LOG2_N", 10)
    h = passwords.hash_password("correct horse battery")
    assert h.startswith("scrypt$10$8$1$")
    assert passwords.verify("correct horse battery", h)
    assert not passwords.verify("correct horse batterz", h)
    assert h != passwords.hash_password("correct horse battery")  # salted


def test_the_default_cost_is_the_owasp_one():
    assert (passwords.DEFAULT_LOG2_N, passwords.DEFAULT_R, passwords.DEFAULT_P) == (17, 8, 1)


@pytest.mark.parametrize("bad", ["", "scrypt$5$8$1$AA==$AA==", "md5$x", "scrypt$x$8$1$a$b"])
def test_malformed_hashes_never_verify(bad):
    assert passwords.verify("anything at all", bad) is False


def test_short_passwords_are_refused():
    with pytest.raises(passwords.WeakPassword):
        passwords.hash_password("too short")
