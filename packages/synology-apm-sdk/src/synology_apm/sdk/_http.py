"""Low-level HTTP session management (private module).

Handles APM authentication, _SSID cookie maintenance, and all REST API request dispatch.
APMClient holds one WebAPISession instance; all HTTP operations go through this class.

Authentication flow (connect):
  1. GET /webapi/entry.cgi — SYNO.API.Auth v6 login: obtain session cookies
  2. 401 re-auth: automatically redo Step 1 and retry once on any 401 response
"""
from __future__ import annotations

import json
import os
import ssl
import sys
from collections.abc import Callable
from typing import Any

import aiohttp

from .exceptions import (
    APIError,
    APMError,
    AuthenticationError,
    BackupServerDisconnectedError,
    ConnectionTimeoutError,
    NotSupportedError,
    PermissionDeniedError,
    ResourceNotFoundError,
)

# Synology SYNO.API.Auth error codes
_SYNO_AUTH_ERROR_MESSAGES: dict[int, str] = {
    119: "Session expired",
    400: "Incorrect username or password",
    401: "Account is disabled",
    402: "Permission denied",
    403: "Two-step verification required",
    404: "Two-step verification code incorrect",
    406: "Account is locked or expired",
    407: "Email verification required",
    430: "Login challenge token missing (ik_message required)",
}

# APM REST API error codes that map to specific exception subclasses
_AUTH_ERROR_CODES: frozenset[int] = frozenset(_SYNO_AUTH_ERROR_MESSAGES)
_PERMISSION_DENIED_CODES: frozenset[int] = frozenset({105})
_NOT_FOUND_CODES: frozenset[int] = frozenset({7000, 14000})
_DISCONNECTED_SERVER_CODE = 2003
_BACKUP_SERVER_NOT_FOUND_CODE = 1402


_CONNECTION_ERRORS = (
    aiohttp.ClientConnectorCertificateError,
    aiohttp.ClientConnectorError,
    aiohttp.ServerDisconnectedError,
    ssl.SSLError,
    aiohttp.ServerTimeoutError,
    TimeoutError,
)


def _map_connection_error(exc: BaseException, base_url: str, *, context: str = "") -> APMError:
    """Map an aiohttp/ssl connection-level exception to the SDK exception to raise.

    context overrides the connect/timeout wording for non-request flows (e.g. downloads).
    """
    if isinstance(exc, aiohttp.ClientConnectorCertificateError):
        return APIError(
            f"SSL certificate verification failed for {base_url}. "
            "Use verify_ssl=False for self-signed certificates."
        )
    if isinstance(exc, (aiohttp.ServerTimeoutError, TimeoutError)):
        msg = f"{context} timed out: {exc}" if context else f"Request to {base_url} timed out: {exc}"
        return ConnectionTimeoutError(msg)
    target = f"{context} URL" if context else base_url
    return APIError(f"Cannot connect to {target}: {exc}")


class WebAPISession:
    """Low-level HTTP session for APM connections.

    Args:
        host: APM hostname or IP, supports host:port, e.g. "apm.corp.com" or "apm.corp.com:10443".
              APM requires HTTPS; the SDK prepends the scheme automatically.
        username: Login account.
        password: Login password.
        verify_ssl: Whether to verify the SSL certificate. Defaults to True.
            Set to False for self-signed certificates in test environments.
        timeout: Per-request timeout in seconds. Defaults to 300.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = True,
        timeout: float = 300.0,
        debug: bool = False,
    ) -> None:
        self._base_url = f"https://{host.rstrip('/')}"
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._debug = debug
        self._session: aiohttp.ClientSession | None = None
        self._connected: bool = False

    # ── Public interface ───────────────────────────────────────────────────

    async def connect(self) -> None:
        """Perform the login flow and establish the session.

        GET /webapi/entry.cgi (SYNO.API.Auth v6 login) — obtain session cookies.

        Raises:
            AuthenticationError: Incorrect credentials, account locked, etc.
            APIError: Cannot connect to APM.
        """
        if self._session is not None:
            await self._session.close()

        # unsafe=True allows storing cookies for IP-based URLs (aiohttp disallows this by default)
        self._session = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=self._timeout,
        )
        self._connected = False

        try:
            await self._do_login()
        except _CONNECTION_ERRORS as exc:
            await self._session.close()
            self._session = None
            raise _map_connection_error(exc, self._base_url) from exc
        except BaseException:
            await self._session.close()
            self._session = None
            raise

        self._connected = True

    async def disconnect(self) -> None:
        """Log out and clean up the session. Safe to call multiple times (idempotent)."""
        if self._session is None:
            return

        if self._connected:
            try:
                await self._request("GET", "/api/v1/preference/logout", _reauth=False)
            except Exception:
                pass  # best-effort: logout failure does not affect cleanup

        await self._session.close()
        self._session = None
        self._connected = False

    async def get(
        self,
        path: str,
        params: dict[str, Any] | list[tuple[str, str | int]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a GET request with the session cookie attached.

        Args:
            path: API path, e.g. "/api/v1/workload/device_workload".
            params: URL query parameters.
            headers: Additional request headers merged with the session defaults.

        Returns:
            Parsed JSON object (dict or list).

        Raises:
            AuthenticationError: Session expired and automatic re-login failed.
            PermissionDeniedError: HTTP 403 or error_code=105.
            ResourceNotFoundError: HTTP 404 or error_code=7000/14000.
            NotSupportedError: HTTP 501 (feature not supported).
            APIError: Other HTTP errors or non-zero errorCode in the JSON body.
        """
        return await self._request("GET", path, params=params, headers=headers)

    async def post(
        self,
        path: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a POST request.

        Args:
            path: API path.
            json: Request body (automatically serialized to JSON).
            headers: Additional request headers merged with the session defaults.

        Returns:
            Parsed JSON object.
        """
        return await self._request("POST", path, json=json, headers=headers)

    async def put(self, path: str, json: Any = None) -> Any:
        """Send a PUT request.

        Args:
            path: API path.
            json: Request body (automatically serialized to JSON).

        Returns:
            Parsed JSON object.
        """
        return await self._request("PUT", path, json=json)

    async def delete(
        self,
        path: str,
        json: Any = None,
        params: dict[str, Any] | list[tuple[str, str | int]] | None = None,
    ) -> Any:
        """Send a DELETE request.

        Args:
            path: API path.
            json: Optional JSON body (required by batch-delete endpoints).
            params: Optional query parameters (dict or list of (key, value) pairs).

        Returns:
            Parsed JSON object (usually an empty `{}`).
        """
        return await self._request("DELETE", path, json=json, params=params)

    async def download_file(
        self,
        url: str,
        dest_path: str,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> None:
        """Download a binary file from a full URL and write it to dest_path (streaming).

        Args:
            url:         Full download URL (as returned by the entries:download endpoint).
            dest_path:   Local filesystem path to write the file to.
            on_progress: Optional callback invoked after each chunk is written.
                         Signature: on_progress(bytes_downloaded, total_bytes_or_none).
                         total_bytes_or_none is None when the server omits Content-Length.

        Raises:
            AuthenticationError: Session is not connected.
            APIError: Server returned an error status.
        """
        if not self._session or not self._connected:
            raise AuthenticationError("Session is not connected. Call connect() first.")

        if self._debug:
            _debug_print_request("GET", url)

        try:
            async with self._session.get(
                url,
                ssl=self._ssl_param(),
                timeout=aiohttp.ClientTimeout(total=None),  # no timeout: file size is unpredictable
            ) as resp:
                if self._debug:
                    _debug_print_response(resp.status, None)
                if resp.status >= 400:
                    raise APIError(
                        f"Download failed: HTTP {resp.status}",
                        error_code=resp.status,
                    )
                total: int | None = int(resp.headers["Content-Length"]) if "Content-Length" in resp.headers else None
                downloaded = 0
                try:
                    with open(dest_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if on_progress is not None:
                                on_progress(downloaded, total)
                except BaseException:
                    # Remove the partial file so a subsequent attempt starts clean.
                    try:
                        os.unlink(dest_path)
                    except OSError:
                        pass
                    raise
                if downloaded == 0:
                    os.unlink(dest_path)
                    raise APIError(
                        "Downloaded file is empty; the export may not be ready for download yet."
                    )
        except _CONNECTION_ERRORS as exc:
            raise _map_connection_error(exc, self._base_url, context="Download") from exc

    # ── Login helpers ──────────────────────────────────────────────────────

    async def _do_login(self) -> None:
        """Perform DSM login and obtain session cookies.

        Raises:
            AuthenticationError: Incorrect credentials or account locked.
        """
        assert self._session is not None
        url = f"{self._base_url}/webapi/entry.cgi"
        params = {
            "api": "SYNO.API.Auth",
            "version": "6",
            "method": "login",
            "account": self._username,
            "passwd": "***",
            "session": "webui",
            "client": "browser",
            "enable_syno_token": "yes",
        }

        if self._debug:
            _debug_print_request("GET", url, params=params)

        async with self._session.get(
            url,
            params={**params, "passwd": self._password},
            ssl=self._ssl_param(),
        ) as resp:
            try:
                data: dict[str, Any] = await resp.json(content_type=None)
            except Exception as exc:
                raise APIError(
                    f"Cannot connect to {self._base_url}: unexpected response format. "
                    "Verify the host is running Synology ActiveProtect Manager."
                ) from exc

        if self._debug:
            _debug_print_response(resp.status, data)

        if not data.get("success"):
            error = data.get("error", {})
            raw_code = error.get("code") if isinstance(error, dict) else None
            code: int | None = raw_code if isinstance(raw_code, int) else None
            msg = (
                _SYNO_AUTH_ERROR_MESSAGES[code]
                if code is not None and code in _SYNO_AUTH_ERROR_MESSAGES
                else f"Login failed (error code {raw_code})"
            )
            raise AuthenticationError(msg, error_code=code, response_body=data)

    # ── Request dispatcher ─────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        _reauth: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Unified HTTP request dispatcher with automatic 401 re-login logic.

        Args:
            method: HTTP method ("GET" / "POST" / "PUT" / "DELETE").
            path: API path (must start with "/").
            _reauth: Whether to allow an automatic retry on 401 (prevents infinite loops; callers should not pass this).
            **kwargs: Extra arguments forwarded to aiohttp (params / json, etc.).

        Returns:
            Parsed JSON object.

        Raises:
            AuthenticationError: Not connected, 401 retry failed, or re-login itself failed.
            PermissionDeniedError: HTTP 403.
            ResourceNotFoundError: HTTP 404.
            NotSupportedError: HTTP 501.
            APIError: HTTP 5xx or non-zero errorCode in the JSON body.
        """
        if not self._session or not self._connected:
            raise AuthenticationError(
                "Session is not connected. Call connect() first.",
            )

        url = f"{self._base_url}{path}"

        if self._debug:
            _debug_print_request(
                method, url,
                params=kwargs.get("params"), body=kwargs.get("json"), headers=kwargs.get("headers"),
            )

        try:
            async with self._session.request(
                method, url, ssl=self._ssl_param(), **kwargs
            ) as resp:
                body = await _safe_json(resp)

                if self._debug:
                    _debug_print_response(resp.status, body)

                if resp.status == 401:
                    if _reauth:
                        await self._do_login()
                        return await self._request(method, path, _reauth=False, **kwargs)
                    raise AuthenticationError(
                        "Session expired and re-authentication failed.",
                        response_body=body,
                    )

                if resp.status == 403:
                    raise PermissionDeniedError(
                        _extract_error_message(body, "Permission denied"),
                        error_code=403, response_body=body,
                    )

                if resp.status == 404:
                    raise ResourceNotFoundError(
                        _extract_error_message(body, "Resource not found"),
                        resource_type="unknown", resource_id="", error_code=404, response_body=body,
                    )

                if resp.status == 501:
                    raise NotSupportedError(
                        "Feature not supported by this APM version.",
                        error_code=501,
                        response_body=body,
                    )

                if resp.status >= 500:
                    _detail_code = _get_detail_error_code(body)
                    if _detail_code == _DISCONNECTED_SERVER_CODE:
                        raise BackupServerDisconnectedError(
                            "The designated backup server is disconnected.",
                            error_code=_DISCONNECTED_SERVER_CODE,
                            response_body=body,
                        )
                    if _detail_code == _BACKUP_SERVER_NOT_FOUND_CODE:
                        raise ResourceNotFoundError(
                            "Backup server not found.",
                            resource_type="BackupServer",
                            resource_id="",
                            error_code=_BACKUP_SERVER_NOT_FOUND_CODE,
                            response_body=body,
                        )
                    raise APIError(
                        f"Server error: HTTP {resp.status}",
                        error_code=_detail_code if _detail_code else resp.status,
                        response_body=body,
                    )

                if resp.status >= 400:
                    raise APIError(
                        _extract_error_message(body, f"HTTP error {resp.status}"),
                        error_code=resp.status, response_body=body,
                    )

                if isinstance(body, dict):
                    self._check_api_error(body)
                return body
        except _CONNECTION_ERRORS as exc:
            raise _map_connection_error(exc, self._base_url) from exc

    def _check_api_error(self, data: dict[str, Any]) -> None:
        """Parse the JSON body and raise the appropriate exception if it contains an API error code.

        Called on HTTP 2xx responses because APM returns HTTP 200 even for certain errors
        (e.g. name conflicts on create/update) and signals them via a non-zero errorCode in
        the body instead of an HTTP error status.

        APM REST API uses three error formats:
        - {"error": {"code": N, "message": "..."}}          (Synology WebAPI format)
        - {"errorCode": N, "message": "..."}                 (APM REST format)
        - {"success": false, "error": {"errorCode": N, ...}} (APM REST nested format)
        All three may return errorCode 0 for success; only non-zero codes are raised.
        """
        code: int | None = None
        msg: str = "API error"

        error_obj = data.get("error")
        if isinstance(error_obj, dict):
            # Format 1: error.code (Synology WebAPI)
            c = error_obj.get("code")
            if c is not None and int(c) != 0:
                code = int(c)
                msg = error_obj.get("message", "API error")
            # Format 3: error.errorCode (APM REST nested)
            if code is None:
                c = error_obj.get("errorCode")
                if c is not None and int(c) != 0:
                    code = int(c)
                    msg = error_obj.get("message", "API error")

        # Format 2: top-level errorCode (APM REST)
        if code is None:
            c = data.get("errorCode")
            if c is not None and int(c) != 0:
                code = int(c)
                msg = data.get("message", "API error")

        if code is not None:
            self._raise_for_error_code(code, msg, response_body=data)

    def _raise_for_error_code(self, code: int, message: str, response_body: Any = None) -> None:
        """Map a known error code to the correct Exception subclass and raise it."""
        if code in _AUTH_ERROR_CODES:
            raise AuthenticationError(message, error_code=code, response_body=response_body)

        if code in _PERMISSION_DENIED_CODES:
            raise PermissionDeniedError(message, error_code=code, response_body=response_body)

        if code in _NOT_FOUND_CODES:
            raise ResourceNotFoundError(
                message, resource_type="unknown", resource_id="", error_code=code, response_body=response_body
            )

        raise APIError(message, error_code=code, response_body=response_body)

    def _ssl_param(self) -> bool:
        """Return the ssl parameter value for aiohttp.

        False → disable SSL verification (self-signed certificates in test environments)
        True  → use aiohttp default SSL verification
        """
        return self._verify_ssl


def _extract_error_message(body: Any, default: str) -> str:
    """Extract the most specific error message from an API response body.

    Checks body["error"]["message"] first (nested format), then body["message"]
    (flat format), then falls back to *default*.
    """
    if not isinstance(body, dict):
        return default
    error_obj = body.get("error")
    if isinstance(error_obj, dict) and error_obj.get("message"):
        return str(error_obj["message"])
    return str(body.get("message")) if body.get("message") else default


async def _safe_json(resp: aiohttp.ClientResponse) -> Any:
    """Attempt to parse the response as JSON; return an empty dict on failure or empty body."""
    try:
        return await resp.json(content_type=None)
    except Exception:
        return {}


def _get_detail_error_code(body: Any) -> int | None:
    """Extract error.details[0].errorCode from an APM error response body, if present."""
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    details = error.get("details")
    if not isinstance(details, list) or not details:
        return None
    first = details[0]
    if not isinstance(first, dict):
        return None
    code = first.get("errorCode")
    return int(code) if isinstance(code, int) else None


def _get_all_detail_codes(body: Any) -> set[int]:
    """Extract all errorCode values from error.details[*].errorCode."""
    if not isinstance(body, dict):
        return set()
    error = body.get("error")
    if not isinstance(error, dict):
        return set()  # pragma: no cover - the API never emits a non-object "error" field
    details = error.get("details")
    if not isinstance(details, list):
        return set()  # pragma: no cover - the API never emits a non-list "details" field
    result: set[int] = set()
    for entry in details:
        if isinstance(entry, dict):
            code = entry.get("errorCode")
            if isinstance(code, int):
                result.add(code)
    return result


def _has_detail_code(body: Any, code: int) -> bool:
    """Return True if the given errorCode appears in error.details[*].errorCode."""
    return code in _get_all_detail_codes(body)


_DEBUG_MAX_BODY = 4096  # response bodies longer than this are truncated


def _debug_print_request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    body: Any = None,
    headers: dict[str, Any] | None = None,
) -> None:
    """Print HTTP request details to stderr."""
    print(f"\n\033[1;36m→ {method} {url}\033[0m", file=sys.stderr)
    if headers:
        print(f"  \033[36mheaders:\033[0m {json.dumps(headers, ensure_ascii=False)}", file=sys.stderr)
    if params:
        print(f"  \033[36mparams:\033[0m {json.dumps(params, ensure_ascii=False, indent=2)}", file=sys.stderr)
    if body is not None:
        body_str = json.dumps(body, ensure_ascii=False, indent=2)
        print(f"  \033[36mbody:\033[0m {body_str}", file=sys.stderr)


def _debug_print_response(status: int, data: Any) -> None:
    """Print HTTP response details to stderr."""
    color = "\033[1;32m" if status < 400 else "\033[1;31m"
    print(f"  {color}← {status}\033[0m", file=sys.stderr)
    if data is not None:
        body_str = json.dumps(data, ensure_ascii=False, indent=2)
        if len(body_str) > _DEBUG_MAX_BODY:
            body_str = body_str[:_DEBUG_MAX_BODY] + f"\n  ... (truncated, {len(body_str)} chars total)"
        print(f"  \033[36mresponse:\033[0m {body_str}", file=sys.stderr)
