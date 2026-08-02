from __future__ import annotations

import sys

from config_store import ConfigStore
from services import credentials


class FakeKeyring:
    """内存版 keyring, 用于在任意平台模拟可用后端。"""

    def __init__(self):
        self.store = {}
        self.set_calls = []

    def set_password(self, profile, device, password):
        self.set_calls.append((profile, device.get("ip"), password))
        self.store[credentials._account(profile, device)] = password
        return True

    def get_password(self, profile, device):
        return self.store.get(credentials._account(profile, device), "")

    def delete_password(self, profile, device):
        self.store.pop(credentials._account(profile, device), None)


def _profile_with_pw(ip="1.2.3.4", pw="s3cret"):
    return {
        "name": "默认",
        "devices": [{"name": "NVR-A", "ip": ip, "username": "admin", "password": pw}],
    }


def _store(tmp_path) -> ConfigStore:
    return ConfigStore(str(tmp_path / "profiles.json"))


def _enable(monkeypatch, kr: FakeKeyring) -> None:
    monkeypatch.setattr(credentials, "available", lambda: True)
    monkeypatch.setattr(credentials, "set_password", kr.set_password)
    monkeypatch.setattr(credentials, "get_password", kr.get_password)
    monkeypatch.setattr(credentials, "delete_password", kr.delete_password)


def test_update_profile_migrates_password_to_keyring(tmp_path, monkeypatch):
    kr = FakeKeyring()
    _enable(monkeypatch, kr)
    store = _store(tmp_path)
    store.update_profile("默认", _profile_with_pw())
    dev = store.get_profile("默认")["devices"][0]
    assert dev["password"] == ""
    assert kr.store["默认::1.2.3.4"] == "s3cret"


def test_resolve_devices_fills_password_from_keyring(tmp_path, monkeypatch):
    kr = FakeKeyring()
    _enable(monkeypatch, kr)
    store = _store(tmp_path)
    store.update_profile("默认", _profile_with_pw())
    devs = store.resolve_devices("默认")
    assert devs[0]["password"] == "s3cret"
    assert store.get_profile("默认")["devices"][0]["password"] == ""


def test_plaintext_kept_when_keyring_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "available", lambda: False)
    store = _store(tmp_path)
    store.update_profile("默认", _profile_with_pw())
    assert store.get_profile("默认")["devices"][0]["password"] == "s3cret"
    assert store.resolve_devices("默认")[0]["password"] == "s3cret"


def test_empty_password_not_written(tmp_path, monkeypatch):
    kr = FakeKeyring()
    _enable(monkeypatch, kr)
    store = _store(tmp_path)
    store.update_profile("默认", _profile_with_pw(pw=""))
    assert kr.set_calls == []


class _Proc:
    returncode = 0
    stdout = ""


class _ProcWithPw(_Proc):
    stdout = "keychain-pw\n"


def test_darwin_uses_security_cli(monkeypatch):
    captured = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(credentials.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(credentials.subprocess, "run", lambda *a, **k: captured.append(a[0]) or _ProcWithPw())
    assert credentials.available()
    assert credentials.set_password("p", {"ip": "9.9.9.9"}, "pw1")
    assert credentials.get_password("p", {"ip": "9.9.9.9"}) == "keychain-pw"
    credentials.delete_password("p", {"ip": "9.9.9.9"})
    add = [c for c in captured if c[1] == "add-generic-password"]
    find = [c for c in captured if c[1] == "find-generic-password"]
    assert add and find
    assert add[0][2:5] == ["-U", "-s", "NVRStatus"]
    assert find[0][2:4] == ["-s", "NVRStatus"]
    assert all("-w" in c for c in add)
