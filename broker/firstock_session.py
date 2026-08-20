"""Firstock login (hash-only) + SDK session seeding.

Ported unchanged from fno_stock_orb / heiken_ashi_option_bot. POSTs the
pre-computed SHA-256 password hash (never plaintext) plus a fresh TOTP to
/V1/login, then writes the session token into the CWD-relative config.json
that both the `firstock` SDK and our direct-requests client read.

Env (.env in project root):
    FIRSTOCK_USER_ID, FIRSTOCK_API_KEY, FIRSTOCK_VENDOR_CODE,
    FIRSTOCK_TOTP_SECRET, FIRSTOCK_PASSWORD_SHA256
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pyotp
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

LOGIN_URL = "https://api.firstock.in/V1/login"
CONFIG_FILE = "config.json"      # SDK + data client read jKey from here (CWD-relative)


class FirstockSession:
    def __init__(self):
        self.user_id = os.getenv("FIRSTOCK_USER_ID", "")
        self.api_key = os.getenv("FIRSTOCK_API_KEY", "")
        self.vendor_code = os.getenv("FIRSTOCK_VENDOR_CODE", "")
        self.totp_secret = os.getenv("FIRSTOCK_TOTP_SECRET", "")
        self.pwd_sha256 = os.getenv("FIRSTOCK_PASSWORD_SHA256", "")
        self._logged_in = False

    def require_creds(self) -> None:
        missing = [k for k, v in {
            "FIRSTOCK_USER_ID": self.user_id, "FIRSTOCK_API_KEY": self.api_key,
            "FIRSTOCK_VENDOR_CODE": self.vendor_code,
            "FIRSTOCK_TOTP_SECRET": self.totp_secret,
            "FIRSTOCK_PASSWORD_SHA256": self.pwd_sha256,
        }.items() if not v]
        if missing:
            raise RuntimeError("Missing Firstock credentials in .env: " + ", ".join(missing))

    def existing_jkey(self) -> str | None:
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f).get(self.user_id, {}).get("jKey")
        except Exception:
            return None

    def login(self) -> dict:
        self.require_creds()
        payload = {
            "userId": self.user_id,
            "password": self.pwd_sha256,      # already hashed; no plaintext handled
            "TOTP": pyotp.TOTP(self.totp_secret).now(),
            "vendorCode": self.vendor_code,
            "apiKey": self.api_key,
        }
        r = requests.post(LOGIN_URL, json=payload, timeout=20)
        try:
            resp = r.json()
        except ValueError:
            raise RuntimeError(
                f"Login did not return JSON (HTTP {r.status_code}). "
                f"First 120 chars: {r.text[:120]!r}")
        if not isinstance(resp, dict) or resp.get("status") != "success":
            raise RuntimeError(f"Firstock login failed: {resp}")
        token = resp["data"]["susertoken"]
        cfg = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
        cfg[self.user_id] = {"jKey": token}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        self._logged_in = True
        return resp

    def ensure(self) -> "FirstockSession":
        """Log in once per process, reusing an existing config.json jKey if any
        (avoids a redundant same-window TOTP, which Firstock can reject)."""
        if not self._logged_in and not self.existing_jkey():
            self.login()
        self._logged_in = True
        return self

    def jkey(self) -> str:
        j = self.existing_jkey()
        if not j:
            self.login()
            j = self.existing_jkey()
        if not j:
            raise RuntimeError("Could not obtain Firstock jKey after login")
        return j
