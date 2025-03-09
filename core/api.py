import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Literal, Any, Optional

import names
from curl_cffi.requests import AsyncSession, Response

from models import Account
from .exceptions.base import APIError, SessionRateLimited, ServerError
from loader import config, headers_manager as HeadersManager


class APIClient:
    def __init__(self, base_url: str, account: Account):
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
        )
        self.base_url = base_url
        self.account_data = account
        self.session = self._create_session()

    def _create_session(self) -> AsyncSession:
        """Create and configure an AsyncSession with proxy support."""
        session = AsyncSession(
            impersonate="chrome134",
            verify=config.get("verify_ssl", False)  # Configurable SSL verification
        )
        session.timeout = config.get("timeout", 60)  # Configurable timeout
        session.headers = HeadersManager.get_base_headers()

        if self.account_data.proxy:
            try:
                proxy_url = self.account_data.proxy.as_url
                if not isinstance(proxy_url, str):
                    raise ValueError("Proxy URL must be a string")
                session.proxies = {"http": proxy_url, "https": proxy_url}
            except (AttributeError, ValueError) as e:
                raise ValueError(f"Invalid proxy configuration: {e}")

        return session

    async def clear_request(self, url: str) -> Response:
        """Perform a simple GET request with a fresh session."""
        session = self._create_session()  # New session for clear_request
        try:
            return await session.get(url, allow_redirects=True, verify=config.get("verify_ssl", False))
        finally:
            await session.aclose()

    @staticmethod
    async def _verify_response(response_data: dict | list):
        """Verify API response and raise errors if needed."""
        if isinstance(response_data, dict):
            if response_data.get("status", True) is False or response_data.get("success", True) is False:
                raise APIError(f"API returned an error: {response_data}", response_data)

    async def send_request(
        self,
        request_type: Literal["POST", "GET", "OPTIONS"] = "POST",
        method: str = None,
        json_data: dict = None,
        params: dict = None,
        url: str = None,
        headers: dict = None,
        cookies: dict = None,
        validate_response: bool = True,  # Renamed from verify to avoid confusion
        max_retries: int = 3,
        retry_delay: float = 3.0,
    ) -> Any:
        """Send an HTTP request with retry logic."""
        url = url if url else f"{self.base_url}{method}"

        for attempt in range(max_retries):
            try:
                if request_type == "POST":
                    response = await self.session.post(
                        url,
                        json=json_data,
                        params=params,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )
                elif request_type == "OPTIONS":
                    response = await self.session.options(
                        url,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )
                else:
                    response = await self.session.get(
                        url,
                        params=params,
                        headers=headers if headers else self.session.headers,
                        cookies=cookies,
                    )

                if response.status_code == 403:
                    raise SessionRateLimited("Session is rate limited")
                if response.status_code in (500, 502, 503, 504):
                    raise ServerError(f"Server error - {response.status_code}")

                try:
                    response_json = response.json()
                    if validate_response:
                        await self._verify_response(response_json)
                    return response_json
                except json.JSONDecodeError:
                    return response.text

            except ServerError as error:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff

            except (APIError, SessionRateLimited):
                raise

            except Exception as error:
                if attempt == max_retries - 1:
                    raise ServerError(f"Failed after {max_retries} attempts: {error}")
                await asyncio.sleep(retry_delay * (2 ** attempt))

        raise ServerError(f"Failed after {max_retries} attempts")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.aclose()


class DawnExtensionAPI(APIClient):
    def __init__(self, account: Account):
        super().__init__("https://www.aeropres.in/chromeapi/dawn", account)
        self.wallet_data: dict[str, Any] = {}
        self.bearer_token: Optional[str] = None  # Instance-specific token

    async def get_puzzle_id(self) -> str:
        response = await self.send_request(
            method="/v1/puzzle/get-puzzle",
            request_type="GET",
            params={"appid": self.account_data.appid},
        )
        return response["puzzle_id"]

    async def get_puzzle_image(self, puzzle_id: str) -> str:
        response = await self.send_request(
            method="/v1/puzzle/get-puzzle-image",
            request_type="GET",
            params={"puzzle_id": puzzle_id, "appid": self.account_data.appid},
        )
        return response.get("imgBase64")

    async def register(self, puzzle_id: str, answer: str) -> dict:
        headers = HeadersManager.get_base_headers()
        headers["content-type"] = "application/json"

        json_data = {
            "firstname": names.get_first_name(),
            "lastname": names.get_last_name(),
            "email": self.account_data.email,
            "mobile": "",
            "password": self.account_data.password,
            "country": "+91",
            "referralCode": random.choice(config.referral_codes or []),
            "puzzle_id": puzzle_id,
            "ans": answer,
            "ismarketing": True,
            "browserName": "Chrome",
        }

        return await self.send_request(
            method="/v1/puzzle/validate-register",
            json_data=json_data,
            params={"appid": self.account_data.appid},
            headers=headers,
        )

    async def keepalive(self) -> dict | str:
        headers = HeadersManager.get_base_headers()
        headers.update({
            "authorization": f"Bearer {self.bearer_token or HeadersManager.BEARER_TOKEN}",
            "content-type": "application/json",
        })

        json_data = {
            "username": self.account_data.email,
            "extensionid": "fpdkjdnhkakefebpekbdhillbhonfjjp",
            "numberoftabs": 0,
            "_v": "1.1.2",
        }

        return await self.send_request(
            method="/v1/userreward/keepalive",
            json_data=json_data,
            validate_response=False,
            headers=headers,
            params={"appid": self.account_data.appid},
        )

    async def user_info(self) -> dict:
        headers = HeadersManager.get_base_headers()
        headers.update({
            "authorization": f"Bearer {self.bearer_token or HeadersManager.BEARER_TOKEN}",
            "content-type": "application/json",
        })

        response = await self.send_request(
            url="https://www.aeropres.in/api/atom/v1/userreferral/getpoint",
            request_type="GET",
            headers=headers,
            params={"appid": self.account_data.appid},
        )
        return response["data"]

    async def verify_registration(self, key: str, cloudflare_token: str):
        headers = HeadersManager.get_base_headers()
        headers.update({
            "content-type": "application/json",
            "origin": "https://www.aeropres.in",
        })

        return await self.send_request(
            method="/v1/userverify/verifycheck",
            json_data={"token": cloudflare_token},
            headers=headers,
            params={"key": key},
        )

    async def resend_verify_link(self, puzzle_id: str, answer: str) -> dict:
        headers = HeadersManager.get_base_headers()
        headers["content-type"] = "application/json"

        json_data = {
            "username": self.account_data.email,
            "puzzle_id": puzzle_id,
            "ans": answer,
        }

        return await self.send_request(
            method="/v1/user/resendverifylink/v2",
            json_data=json_data,
            params={"appid": self.account_data.appid},
            headers=headers,
        )

    async def complete_tasks(self, tasks: list[str] = None, delay: int = 1) -> None:
        tasks = tasks or ["telegramid", "discordid", "twitter_x_id"]
        headers = HeadersManager.get_base_headers()
        headers.update({
            "authorization": f"Bearer {self.bearer_token or HeadersManager.BEARER_TOKEN}",
            "content-type": "application/json",
        })

        for task in tasks:
            await self.send_request(
                method="/v1/profile/update",
                json_data={task: f"{task}_value"},  # More meaningful value
                headers=headers,
                params={"appid": self.account_data.appid},
            )
            await asyncio.sleep(delay)

    async def verify_session(self) -> tuple[bool, str]:
        try:
            await self.user_info()
            return True, "Session is valid"
        except ServerError as e:
            return True, f"Server error: {e}"
        except APIError as e:
            return False, str(e)

    async def login(self, puzzle_id: str, answer: str):
        headers = HeadersManager.get_base_headers()
        headers["content-type"] = "application/json"

        current_time = datetime.now(timezone.utc)
        formatted_datetime_str = current_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        json_data = {
            "username": self.account_data.email,
            "password": self.account_data.password,
            "logindata": {
                "_v": {"version": "1.1.2"},
                "datetime": formatted_datetime_str,
            },
            "puzzle_id": puzzle_id,
            "ans": answer,
        }

        response = await self.send_request(
            method="/v1/user/login/v2",
            json_data=json_data,
            params={"appid": self.account_data.appid},
            headers=headers,
        )

        bearer = response.get("data", {}).get("token")
        if bearer:
            self.bearer_token = bearer.replace("Bearer ", "")
            HeadersManager.BEARER_TOKEN = self.bearer_token  # Keep global for backward compatibility
        else:
            raise APIError(f"Failed to login: {response}")