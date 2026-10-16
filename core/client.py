import asyncio
import threading
from datetime import datetime, timezone, timedelta

import httpx

from .auth import refresh_tokens, parse_not_after

_REFRESH_LOCK = threading.Lock()


class AuthExpiredError(RuntimeError):
    """Raised when tokens expired and could not be refreshed; user must sign in again."""


class XALLiveClient:
    """Authenticated Xbox Live client: signs requests with the XSTS token and refreshes it when needed."""
    def __init__(self, sisu_auth: dict):
        self.generation = -1  # bumped on every token refresh so concurrent callers can detect it
        self._apply(sisu_auth)

    def _apply(self, sisu_auth: dict):
        self.sisu_auth = sisu_auth
        self.generation += 1
        auth_tok = sisu_auth.get("AuthorizationToken") or {}
        self.auth_token = auth_tok
        self.xsts_jwt = auth_tok.get("Token", "")
        xui_list = (auth_tok.get("DisplayClaims") or {}).get("xui") or [{}]
        xui = xui_list[0]
        self.xuid = str(xui.get("xid", ""))
        self.gamertag = str(xui.get("gtg", "Xbox User"))
        self.uhs = str(xui.get("uhs", ""))

    @property
    def auth_header(self):
        return f"XBL3.0 x={self.uhs};{self.xsts_jwt}"

    def is_expiring(self, within_seconds=300):
        """True if the XSTS token is expired or about to expire."""
        exp = parse_not_after(self.auth_token.get("NotAfter", ""))
        if exp is None:
            return False
        return datetime.now(timezone.utc) + timedelta(seconds=within_seconds) >= exp

    async def refresh(self):
        """Refreshes tokens in-place. Raises AuthExpiredError if that is not possible."""
        seen = self.generation
        # Other worker threads may be refreshing too; refresh tokens rotate, so only one may do it.
        with _REFRESH_LOCK:
            if self.generation != seen:
                return
            try:
                new_data = await refresh_tokens(self.sisu_auth)
            except Exception as e:
                print(f"[Auth] Token refresh failed: {e}")
                raise AuthExpiredError("Your Xbox session expired. Please sign out and sign in again.") from e
            self._apply(new_data)

    async def ensure_fresh(self):
        if self.is_expiring():
            await self.refresh()

    async def get(self, url, headers=None, params=None, retries=2):
        await self.ensure_fresh()
        refreshed = False
        attempt = 0
        while True:
            h = {
                "Authorization": self.auth_header,
                "x-xbl-contract-version": "2",
                "x-xbl-client-name": "XboxApp",
                "x-xbl-client-type": "UWA",
                "x-xbl-client-version": "39.39.22001.0",
                "Accept-Language": "en-US",
                "User-Agent": "XboxApp/39.39.22001.0"
            }
            if headers:
                h.update(headers)
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=h, params=params)
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                await self.refresh()
                continue
            if resp.status_code == 429 and attempt < retries:
                attempt += 1
                try:
                    wait = float(resp.headers.get("Retry-After", 2.0))
                except ValueError:
                    wait = 2.0
                await asyncio.sleep(min(wait, 4.0))
                continue
            resp.raise_for_status()
            return resp.json()

    async def get_profile_gamerpic(self, xuid=None):
        target_xuid = xuid or self.xuid
        if not target_xuid:
            return ""
        try:
            url = f"https://profile.xboxlive.com/users/xuid({target_xuid})/profile/settings?settings=GameDisplayPicRaw,Gamertag"
            data = await self.get(url)
            users = data.get("profileUsers", [])
            if users:
                for s in users[0].get("settings", []):
                    if s.get("id") == "GameDisplayPicRaw":
                        return s.get("value", "")
        except Exception as e:
            print(f"[Profile] Failed to fetch gamerpic: {e}")
        return ""
