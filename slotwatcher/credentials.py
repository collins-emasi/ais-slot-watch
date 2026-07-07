from __future__ import annotations

from dataclasses import dataclass
import getpass
import platform
import shutil
import subprocess

from .config import WatchConfig


@dataclass(frozen=True)
class LoginCredentials:
    email: str
    password: str


def keychain_account(config: WatchConfig, email: str | None = None) -> str | None:
    return config.keychain_account or email or config.login_email


def keychain_supported() -> bool:
    return platform.system() == "Darwin" and bool(shutil.which("security"))


def read_keychain_password(service: str, account: str) -> str | None:
    if not keychain_supported():
        return None

    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return None
    password = result.stdout.rstrip("\n")
    return password or None


def write_keychain_password(service: str, account: str, password: str) -> None:
    if not keychain_supported():
        raise RuntimeError("macOS Keychain is not available on this machine")

    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", password],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
    )


def load_login_credentials(config: WatchConfig) -> tuple[LoginCredentials | None, list[str]]:
    notes: list[str] = []
    if not config.login_email:
        notes.append("Auto-login is enabled, but auth.login_email or AIS_LOGIN_EMAIL is not set.")
        return None, notes

    password = config.login_password
    account = keychain_account(config, config.login_email)
    if not password and account:
        password = read_keychain_password(config.keychain_service, account)

    if not password:
        notes.append(
            "Auto-login is enabled, but no password was found. Set AIS_LOGIN_PASSWORD "
            "or run: python -m slotwatcher store-password --config config.toml"
        )
        return None, notes

    return LoginCredentials(email=config.login_email, password=password), notes


def prompt_and_store_password(config: WatchConfig, email: str | None = None) -> str:
    account = keychain_account(config, email)
    if not account:
        raise ValueError("Provide --email or set auth.login_email before storing a password.")

    password = getpass.getpass(f"AIS password for {account}: ")
    if not password:
        raise ValueError("No password entered.")

    write_keychain_password(config.keychain_service, account, password)
    return account
