"""设备密码安全存储 (B7)。

macOS 用 Keychain (`security` CLI)，Windows 用 Credential Manager
(ctypes CredWrite/CredRead)，其他平台 (Linux 等) 返回不可用，
调用方回退到明文保存。

本模块无 Qt 依赖，可在 CLI/测试中直接使用。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from typing import Dict

SERVICE = "NVRStatus"

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


def available() -> bool:
    """当前平台是否支持安全凭证存储。"""
    if sys.platform == "darwin":
        return os.path.isfile("/usr/bin/security")
    if sys.platform == "win32":
        return True
    return False


def _account(profile: str, device: Dict[str, object]) -> str:
    ip = (device.get("ip") or "").strip()
    name = (device.get("name") or "").strip()
    return f"{profile}::{ip or name}"


def _win_target(account: str) -> str:
    """Windows Credential Manager 的 TargetName：每设备唯一。

    旧实现曾用固定 TargetName=SERVICE，多设备会互相覆盖。
    """
    return f"{SERVICE}/{account}"


def set_password(profile: str, device: Dict[str, object], password: str) -> bool:
    """写入密码; 返回是否成功。空密码视为删除并返回 False。"""
    if not password:
        delete_password(profile, device)
        return False
    account = _account(profile, device)
    if sys.platform == "darwin":
        return _macos_set_password(account, password)
    if sys.platform == "win32":
        return _win_set_password(account, password)
    return False


def get_password(profile: str, device: Dict[str, object]) -> str:
    """读取密码; 不存在或不可用时返回空串。"""
    account = _account(profile, device)
    if sys.platform == "darwin":
        return _macos_get_password(account)
    if sys.platform == "win32":
        return _win_get_password(account)
    return ""


def delete_password(profile: str, device: Dict[str, object]) -> None:
    account = _account(profile, device)
    if sys.platform == "darwin":
        subprocess.run(
            ["security", "delete-generic-password", "-s", SERVICE, "-a", account],
            capture_output=True,
            text=True,
            timeout=15,
        )
    elif sys.platform == "win32":
        _win_delete_password(account)


def rekey_password(old_profile: str, new_profile: str, device: Dict[str, object]) -> None:
    """档案重命名 / 克隆时把凭证迁到新 profile 键。"""
    if old_profile == new_profile:
        return
    pw = get_password(old_profile, device)
    if not pw:
        return
    if set_password(new_profile, device, pw):
        delete_password(old_profile, device)


# ---- macOS: security CLI ----

def _macos_set_password(account: str, password: str) -> bool:
    proc = subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-s", SERVICE,
            "-a", account,
            "-w", password,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.returncode == 0


def _macos_get_password(account: str) -> str:
    proc = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-s", SERVICE,
            "-a", account,
            "-w",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.rstrip("\r\n\x00")


# ---- Windows: Credential Manager ----

class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_ulong),
        ("Type", ctypes.c_ulong),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", ctypes.c_ulonglong),
        ("CredentialBlobSize", ctypes.c_ulong),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", ctypes.c_ulong),
        ("AttributeCount", ctypes.c_ulong),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


def _win_set_password(account: str, password: str) -> bool:
    buf = ctypes.create_unicode_buffer(password)
    blob = ctypes.cast(buf, ctypes.c_void_p)
    target = _win_target(account)
    cred = _Credential(
        Type=CRED_TYPE_GENERIC,
        TargetName=target,
        CredentialBlobSize=(len(password) + 1) * 2,
        CredentialBlob=blob,
        Persist=CRED_PERSIST_LOCAL_MACHINE,
        UserName=account,
    )
    ok = bool(ctypes.windll.advapi32.CredWriteW(ctypes.byref(cred), 0))
    if ok:
        # 清理旧版单槽 TargetName=SERVICE 残留，避免读到过期密码
        try:
            ctypes.windll.advapi32.CredDeleteW(SERVICE, CRED_TYPE_GENERIC, 0)
        except Exception:
            pass
    return ok


def _win_read_blob(target: str) -> str:
    pcred = ctypes.POINTER(_Credential)()
    if not ctypes.windll.advapi32.CredReadW(
        target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)
    ):
        return ""
    try:
        size = pcred.contents.CredentialBlobSize
        data = ctypes.string_at(pcred.contents.CredentialBlob, size)
        return data.decode("utf-16-le").rstrip("\x00")
    finally:
        ctypes.windll.advapi32.CredFree(pcred)


def _win_get_password(account: str) -> str:
    # 优先 per-device TargetName
    pw = _win_read_blob(_win_target(account))
    if pw:
        return pw
    # 兼容旧版：唯一 TargetName=SERVICE，UserName=account
    pcred = ctypes.POINTER(_Credential)()
    if not ctypes.windll.advapi32.CredReadW(
        SERVICE, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)
    ):
        return ""
    try:
        user = pcred.contents.UserName or ""
        if user != account:
            return ""
        size = pcred.contents.CredentialBlobSize
        data = ctypes.string_at(pcred.contents.CredentialBlob, size)
        return data.decode("utf-16-le").rstrip("\x00")
    finally:
        ctypes.windll.advapi32.CredFree(pcred)


def _win_delete_password(account: str) -> None:
    ctypes.windll.advapi32.CredDeleteW(_win_target(account), CRED_TYPE_GENERIC, 0)
    # 若旧版槽位正好是本 account，一并删掉
    pcred = ctypes.POINTER(_Credential)()
    if ctypes.windll.advapi32.CredReadW(
        SERVICE, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)
    ):
        try:
            if (pcred.contents.UserName or "") == account:
                ctypes.windll.advapi32.CredDeleteW(SERVICE, CRED_TYPE_GENERIC, 0)
        finally:
            ctypes.windll.advapi32.CredFree(pcred)
