import json

import httpx

from dark_mcp.config import Settings


class AILError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

    def result(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "http_status": self.status}}


class AILClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def request(self, method: str, path: str, *, params: dict | None = None, payload: dict | None = None):
        if not path.startswith("api/v1/") or ".." in path or "?" in path or "#" in path:
            raise ValueError("Internal AIL route is invalid")
        try:
            key = self.settings.api_key()
        except ValueError as exc:
            raise AILError("ail_not_configured", str(exc)) from None
        except OSError:
            raise AILError("ail_not_configured", "Unable to read the locally configured API key file") from None
        try:
            async with httpx.AsyncClient(
                verify=self.settings.ail_verify_tls,
                timeout=self.settings.request_timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    method,
                    self.settings.ail_url + "/" + path,
                    headers={"X-AIL-AUTH": key, "Accept": "application/json"},
                    params=params,
                    json=payload,
                ) as response:
                    if response.is_redirect:
                        raise AILError("ail_redirect_rejected", "AIL redirected the request; check its base URL")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.settings.max_response_bytes:
                            raise AILError("ail_response_too_large", "AIL response exceeds the configured byte bound")
                    try:
                        data = json.loads(body)
                    except (ValueError, UnicodeDecodeError):
                        raise AILError(
                            "ail_invalid_response", "AIL did not return valid JSON", response.status_code
                        ) from None
                    if response.is_error:
                        codes = {401: "ail_authentication_failed", 403: "ail_access_denied", 404: "ail_not_found"}
                        raise AILError(
                            codes.get(response.status_code, "ail_backend_error"),
                            "AIL rejected the request; inspect its local logs and API configuration",
                            response.status_code,
                        )
                    if isinstance(data, dict) and data.get("status") == "error":
                        raise AILError("ail_backend_error", "AIL returned an application error", response.status_code)
                    return data
        except httpx.TimeoutException:
            raise AILError("ail_timeout", "AIL did not respond within the configured deadline") from None
        except httpx.HTTPError:
            raise AILError("ail_unreachable", "Unable to reach the configured AIL backend") from None
