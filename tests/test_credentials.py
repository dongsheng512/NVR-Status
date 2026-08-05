from __future__ import annotations

import sys

from config_store import ConfigStore
from services import credentials


class FakeKeyring:
    """内存版 keyring, 用于在任意平台模拟可用后端。"""

    def __init__(self, fail_set: bool = False):
        self.store = {}
        self.set_calls = []
        self.delete_calls = []
        self.fail_set = fail_set

    def set_password(self, profile, device, password):
        self.set_calls.append((profile, device.get("ip"), password))
        if self.fail_set:
            return False
        self.store[credentials._account(profile, device)] = password
        return True

    def get_password(self, profile, device):
        return self.store.get(credentials._account(profile, device), "")

    def delete_password(self, profile, device):
        self.delete_calls.append((profile, device.get("ip")))
        self.store.pop(credentials._account(profile, device), None)


def _profile_with_pw(ip="1.2.3.4", pw="s3cret", name="NVR-A"):
    return {
        "name": "默认",
        "devices": [{"name": name, "ip": ip, "username": "admin", "password": pw}],
    }


def _store(tmp_path) -> ConfigStore:
    return ConfigStore(str(tmp_path / "profiles.json"))


def _enable(monkeypatch, kr: FakeKeyring) -> None:
    monkeypatch.setattr(credentials, "available", lambda: True)
    monkeypatch.setattr(credentials, "set_password", kr.set_password)
    monkeypatch.setattr(credentials, "get_password", kr.get_password)
    monkeypatch.setattr(credentials, "delete_password", kr.delete_password)
    # rekey_password 保持真实实现，内部调用上面的 get/set/delete mock


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


def test_migrate_keeps_plaintext_when_keyring_write_fails(tmp_path, monkeypatch):
    """写入 keyring 失败时不得清空 JSON 明文（防丢密）。"""
    kr = FakeKeyring(fail_set=True)
    _enable(monkeypatch, kr)
    store = _store(tmp_path)
    store.update_profile("默认", _profile_with_pw())
    assert store.get_profile("默认")["devices"][0]["password"] == "s3cret"
    assert kr.store == {}


def test_rename_profile_rekeys_passwords(tmp_path, monkeypatch):
    kr = FakeKeyring()
    _enable(monkeypatch, kr)
    store = _store(tmp_path)
    store.update_profile("默认", _profile_with_pw())
    assert "默认::1.2.3.4" in kr.store
    assert store.rename_profile("默认", "机房A")
    assert "默认::1.2.3.4" not in kr.store
    assert kr.store["机房A::1.2.3.4"] == "s3cret"
    assert store.resolve_devices("机房A")[0]["password"] == "s3cret"


def test_delete_profile_purges_keyring(tmp_path, monkeypatch):
    kr = FakeKeyring()
    _enable(monkeypatch, kr)
    store = _store(tmp_path)
    store.update_profile("默认", _profile_with_pw())
    store.create_profile("备份")
    assert store.delete_profile("默认")
    assert "默认::1.2.3.4" not in kr.store


def test_clone_profile_copies_keyring_passwords(tmp_path, monkeypatch):
    kr = FakeKeyring()
    _enable(monkeypatch, kr)
    store = _store(tmp_path)
    store.update_profile("默认", _profile_with_pw())
    created = store.create_profile("副本", clone_from="默认")
    assert created == "副本"
    # 源仍在
    assert kr.store["默认::1.2.3.4"] == "s3cret"
    assert kr.store["副本::1.2.3.4"] == "s3cret"
    assert store.resolve_devices("副本")[0]["password"] == "s3cret"


def test_stale_device_keyring_purged_on_update(tmp_path, monkeypatch):
    kr = FakeKeyring()
    _enable(monkeypatch, kr)
    store = _store(tmp_path)
    store.update_profile(
        "默认",
        {
            "name": "默认",
            "devices": [
                {"name": "A", "ip": "1.1.1.1", "username": "admin", "password": "p1"},
                {"name": "B", "ip": "2.2.2.2", "username": "admin", "password": "p2"},
            ],
        },
    )
    assert "默认::1.1.1.1" in kr.store and "默认::2.2.2.2" in kr.store
    store.update_profile(
        "默认",
        {
            "name": "默认",
            "devices": [
                {"name": "A", "ip": "1.1.1.1", "username": "admin", "password": ""},
            ],
        },
    )
    assert "默认::1.1.1.1" in kr.store
    assert "默认::2.2.2.2" not in kr.store


def test_win_target_is_per_device():
    """Windows TargetName 必须按设备唯一，避免多设备互相覆盖。"""
    a = credentials._account("p", {"ip": "1.1.1.1"})
    b = credentials._account("p", {"ip": "2.2.2.2"})
    assert credentials._win_target(a) != credentials._win_target(b)
    assert credentials._win_target(a).startswith("NVRStatus/")


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
