import os
import json
import uuid
import base64
import hashlib
import struct
from pathlib import Path
from datetime import datetime, timezone
from urllib import parse
import ecdsa
import httpx

from .secure_store import save_secret, load_secret, delete_secret

BASE_DIR = Path(__file__).resolve().parent.parent
LEGACY_TOKENS_FILE = BASE_DIR / "tokens.json"

APP_ID = "000000004c12ae6f"
TITLE_ID = "328178078"
REDIRECT_URI = "ms-xal-000000004c12ae6f://auth"

def dt_to_filetime(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    EPOCH_AS_FILETIME = 116444736000000000
    HUNDREDS_OF_NANOSECONDS = 10000000
    return EPOCH_AS_FILETIME + (int(dt.timestamp()) * HUNDREDS_OF_NANOSECONDS) + (dt.microsecond * 10)

def encode_coord(c) -> str:
    raw = c.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")

class XALAuthContext:
    def __init__(self):
        self.signing_key = ecdsa.SigningKey.generate(curve=ecdsa.NIST256p)
        pk = self.signing_key.verifying_key.pubkey.point
        self.proof_field = {
            "use": "sig",
            "alg": "ES256",
            "kty": "EC",
            "crv": "P-256",
            "x": encode_coord(pk.x()),
            "y": encode_coord(pk.y()),
        }
        self.device_id = str(uuid.uuid4())
        self.code_verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
        code_challenge = hashlib.sha256(self.code_verifier.encode()).digest()
        self.code_challenge = base64.urlsafe_b64encode(code_challenge).decode().rstrip("=")
        self.state = base64.b64encode(str(uuid.uuid4()).encode()).decode()
        self.device_token = None
        self.sisu_session_id = None
        self.redirect_uri = REDIRECT_URI

    def sign_request(self, method: str, url: str, body: bytes = b"", auth_header: str = "") -> str:
        u = parse.urlsplit(url)
        path_and_query = u.path + (("?" + u.query) if u.query else "")
        dt = datetime.now(timezone.utc)
        ts_bytes = struct.pack("!Q", dt_to_filetime(dt))
        sig_ver_bytes = struct.pack("!I", 1)
        body_bytes = body if isinstance(body, bytes) else body.encode("utf-8")
        to_sign = (
            sig_ver_bytes + b"\x00" +
            ts_bytes + b"\x00" +
            method.upper().encode("ascii") + b"\x00" +
            path_and_query.encode("ascii") + b"\x00" +
            auth_header.encode("ascii") + b"\x00" +
            body_bytes[:8192] + b"\x00"
        )
        digest = hashlib.sha256(to_sign).digest()
        sig = self.signing_key.sign_digest_deterministic(digest)
        return base64.b64encode(sig_ver_bytes + ts_bytes + sig).decode("ascii")

    async def prepare_login(self) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Device Token Authentication
            dev_url = "https://device.auth.xboxlive.com/device/authenticate"
            dev_body = {
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
                "Properties": {
                    "AuthMethod": "ProofOfPossession",
                    "Id": "{" + self.device_id + "}",
                    "DeviceType": "Android",
                    "Version": "14.0.0",
                    "ProofKey": self.proof_field,
                },
            }
            body_bytes = json.dumps(dev_body).encode("utf-8")
            sig = self.sign_request("POST", dev_url, body_bytes)
            headers = {
                "x-xbl-contract-version": "1",
                "Signature": sig,
                "Content-Type": "application/json",
            }
            resp = await client.post(dev_url, headers=headers, content=body_bytes)
            resp.raise_for_status()
            self.device_token = resp.json()["Token"]

            # 2. SISU Authentication
            sisu_url = "https://sisu.xboxlive.com/authenticate"
            sisu_body = {
                "AppId": APP_ID,
                "TitleId": TITLE_ID,
                "RedirectUri": REDIRECT_URI,
                "DeviceToken": self.device_token,
                "Sandbox": "RETAIL",
                "TokenType": "code",
                "Offers": ["service::user.auth.xboxlive.com::MBI_SSL"],
                "Query": {
                    "code_challenge": self.code_challenge,
                    "code_challenge_method": "S256",
                    "state": self.state,
                    "prompt": "select_account",
                },
            }
            body_bytes = json.dumps(sisu_body).encode("utf-8")
            sig = self.sign_request("POST", sisu_url, body_bytes)
            headers = {
                "x-xbl-contract-version": "1",
                "Signature": sig,
                "Content-Type": "application/json",
            }
            resp = await client.post(sisu_url, headers=headers, content=body_bytes)
            resp.raise_for_status()
            data = resp.json()
            self.sisu_session_id = resp.headers.get("X-SessionId") or resp.headers.get("x-sessionid")
            redirect_url = data.get("MsaOauthRedirect") or data.get("msa_oauth_redirect")
            
            if redirect_url:
                parsed = parse.urlsplit(redirect_url)
                q = parse.parse_qs(parsed.query, keep_blank_values=True)
                # Remove legacy display and mobile formatting params that trigger old design
                q.pop("display", None)
                # Ensure select_account is enforced so user can switch accounts
                q["prompt"] = ["select_account"]
                new_query = parse.urlencode(q, doseq=True)
                redirect_url = parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))

            return redirect_url

    async def complete_login(self, redirect_uri: str) -> dict:
        query_params = dict(parse.parse_qsl(parse.urlsplit(redirect_uri).query))
        resp_code = query_params.get("code")
        if not resp_code:
            raise ValueError(f"No authorization code found in redirect URL: {redirect_uri}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            # 3. Exchange code for OAuth token
            token_url = "https://login.live.com/oauth20_token.srf"
            token_post = {
                "client_id": APP_ID,
                "code": resp_code,
                "code_verifier": self.code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
                "scope": "service::user.auth.xboxlive.com::MBI_SSL",
            }
            token_resp = await client.post(token_url, data=token_post)
            if token_resp.status_code >= 400:
                print(f"[OAuth Token Error] {token_resp.status_code}: {token_resp.text}")
            token_resp.raise_for_status()
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")

            # 4. Do SISU authorization
            auth_url = "https://sisu.xboxlive.com/authorize"
            auth_body = {
                "AccessToken": f"t={access_token}",
                "AppId": APP_ID,
                "DeviceToken": self.device_token,
                "Sandbox": "RETAIL",
                "SiteName": "user.auth.xboxlive.com",
                "SessionId": self.sisu_session_id,
                "ProofKey": self.proof_field,
            }
            body_bytes = json.dumps(auth_body).encode("utf-8")
            sig = self.sign_request("POST", auth_url, body_bytes)
            headers = {
                "x-xbl-contract-version": "1",
                "Signature": sig,
                "Content-Type": "application/json",
            }
            auth_resp = await client.post(auth_url, headers=headers, content=body_bytes)
            if auth_resp.status_code >= 400:
                print(f"[SISU Authorize Error] {auth_resp.status_code}: {auth_resp.text}")
            auth_resp.raise_for_status()
            sisu_data = auth_resp.json()
            
            # Store tokens cleanly
            sisu_data["oauth_refresh_token"] = refresh_token
            sisu_data["oauth_access_token"] = access_token
            
            save_tokens(sisu_data)
            return sisu_data

def save_tokens(data: dict):
    save_secret(data)

def _valid_tokens(data) -> bool:
    return isinstance(data, dict) and ("AuthorizationToken" in data or "authorization_token" in data)

def load_cached_tokens():
    try:
        data = load_secret()
        if _valid_tokens(data):
            return data
        # One-time migration from the old plain-text tokens.json next to the app
        if LEGACY_TOKENS_FILE.is_file():
            with open(LEGACY_TOKENS_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            if _valid_tokens(legacy):
                save_secret(legacy)
                try:
                    os.remove(LEGACY_TOKENS_FILE)
                except OSError:
                    pass
                return legacy
    except Exception as e:
        print(f"[Load Tokens Warning] {e}")
    return None

def clear_cached_tokens():
    delete_secret()
    try:
        if LEGACY_TOKENS_FILE.is_file():
            os.remove(LEGACY_TOKENS_FILE)
    except OSError:
        pass

def parse_not_after(value: str):
    """Parses Xbox 'NotAfter' timestamps (7 fractional digits, trailing Z)."""
    try:
        s = value.rstrip("Z")
        if "." in s:
            head, frac = s.split(".", 1)
            s = f"{head}.{frac[:6]}"
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except Exception:
        return None

async def refresh_tokens(sisu_data: dict) -> dict:
    """
    Exchanges the saved OAuth refresh token for fresh Xbox Live tokens
    (MSA access token -> user token -> XSTS) and persists them.
    Raises on failure (caller should then ask the user to sign in again).
    """
    refresh_token = sisu_data.get("oauth_refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh token available; please sign in again.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            "https://login.live.com/oauth20_token.srf",
            data={
                "client_id": APP_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "service::user.auth.xboxlive.com::MBI_SSL",
            },
        )
        r.raise_for_status()
        tok = r.json()
        access_token = tok["access_token"]
        new_refresh = tok.get("refresh_token") or refresh_token

        r = await client.post(
            "https://user.auth.xboxlive.com/user/authenticate",
            headers={"x-xbl-contract-version": "1", "Accept": "application/json"},
            json={
                "RelyingParty": "http://auth.xboxlive.com",
                "TokenType": "JWT",
                "Properties": {
                    "AuthMethod": "RPS",
                    "SiteName": "user.auth.xboxlive.com",
                    "RpsTicket": f"t={access_token}",
                },
            },
        )
        r.raise_for_status()
        user_token = r.json()

        r = await client.post(
            "https://xsts.auth.xboxlive.com/xsts/authorize",
            headers={"x-xbl-contract-version": "1", "Accept": "application/json"},
            json={
                "RelyingParty": "http://xboxlive.com",
                "TokenType": "JWT",
                "Properties": {"UserTokens": [user_token["Token"]], "SandboxId": "RETAIL"},
            },
        )
        r.raise_for_status()
        xsts = r.json()

    new_data = dict(sisu_data)
    new_data["UserToken"] = user_token
    new_data["AuthorizationToken"] = xsts
    new_data["oauth_access_token"] = access_token
    new_data["oauth_refresh_token"] = new_refresh
    save_tokens(new_data)
    return new_data
