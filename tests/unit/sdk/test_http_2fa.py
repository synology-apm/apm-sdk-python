"""Unit tests for two-factor authentication (TOTP) / trusted-device login support in
synology_apm.sdk._http.WebAPISession.

Uses aiointercept to mock all HTTP requests; no real APM connection required.
"""
from __future__ import annotations

from typing import Any

import pytest
from aiointercept import aiointercept

from synology_apm.sdk.exceptions import AuthenticationError, OTPIncorrectError, OTPRequiredError
from tests.unit.sdk.conftest import BASE_URL, LOGIN_OK, TESTUSER_LOGIN_URL
from tests.unit.sdk.conftest import (
    connect_testuser_session as connect_session,
)
from tests.unit.sdk.conftest import (
    disconnect_testuser_session as disconnect_session,
)
from tests.unit.sdk.conftest import (
    make_testuser_session as make_session,
)

LOGIN_URL_WITH_OTP = (
    f"{BASE_URL}/webapi/entry.cgi"
    "?account=testuser&api=SYNO.API.Auth&client=browser"
    "&enable_device_token=yes&enable_syno_token=yes&method=login"
    "&otp_code=123456&passwd=testpass&session=webui&version=6"
)
LOGIN_URL_WITH_OTP_WRONG_CODE = (
    f"{BASE_URL}/webapi/entry.cgi"
    "?account=testuser&api=SYNO.API.Auth&client=browser"
    "&enable_device_token=yes&enable_syno_token=yes&method=login"
    "&otp_code=000000&passwd=testpass&session=webui&version=6"
)
LOGIN_URL_WITH_DEVICE_ID = (
    f"{BASE_URL}/webapi/entry.cgi"
    "?account=testuser&api=SYNO.API.Auth&client=browser&device_id=did-abc"
    "&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6"
)
LOGIN_URL_WITH_NEW_DEVICE_ID = (
    f"{BASE_URL}/webapi/entry.cgi"
    "?account=testuser&api=SYNO.API.Auth&client=browser&device_id=did-new"
    "&enable_syno_token=yes&method=login&passwd=testpass&session=webui&version=6"
)
LOGIN_OK_WITH_DID: dict[str, Any] = {"success": True, "data": {"sid": "abc", "synotoken": "tok", "did": "did-new"}}
LOGIN_FAIL_OTP_REQUIRED: dict[str, Any] = {"success": False, "error": {"code": 403}}
LOGIN_FAIL_OTP_INCORRECT: dict[str, Any] = {"success": False, "error": {"code": 404}}


# ── Login param shape per scenario ──────────────────────────────────────────


async def test_login_sends_otp_code_and_enables_device_token() -> None:
    """A session constructed with otp_code sends otp_code + enable_device_token=yes."""
    session = make_session(otp_code="123456")
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL_WITH_OTP, payload=LOGIN_OK)
        await session.connect()
        await session.disconnect()


async def test_login_sends_device_id_without_otp_code_when_known() -> None:
    """A session constructed with device_id (no otp_code) sends device_id, never otp_code."""
    session = make_session(device_id="did-abc")
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL_WITH_DEVICE_ID, payload=LOGIN_OK)
        await session.connect()
        await session.disconnect()


async def test_login_omits_2fa_params_by_default() -> None:
    """Regression: a plain session (no otp_code/device_id) logs in exactly as before."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_OK)
        await session.connect()
        await session.disconnect()


async def test_successful_otp_login_captures_new_device_id() -> None:
    """A successful otp_code-bearing login whose response includes "did" exposes it via .device_id."""
    session = make_session(otp_code="123456")
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL_WITH_OTP, payload=LOGIN_OK_WITH_DID)
        await session.connect()
        assert session.device_id == "did-new"
        await session.disconnect()


async def test_debug_mode_masks_otp_code_in_printed_request(capsys: pytest.CaptureFixture[str]) -> None:
    """With debug=True, the real otp_code must never appear in the printed request line —
    only "***", mirroring how passwd is already masked."""
    session = make_session(otp_code="123456", debug=True)
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL_WITH_OTP, payload=LOGIN_OK)
        await session.connect()
        await session.disconnect()
    captured = capsys.readouterr()
    assert "123456" not in captured.err
    assert '"otp_code": "***"' in captured.err


async def test_401_reauth_reuses_device_id_not_otp_code() -> None:
    """An automatic 401 re-auth resends the (now-known) device_id, never the original one-shot
    otp_code — otherwise a session-expiry re-login for a two-factor account would need a fresh
    OTP with no human present to supply one. Registering a login mock that only matches a
    device_id-only request shape (no otp_code/enable_device_token) means the test would fail
    with an unmatched-request error, not a silent false pass, if the stale otp_code were resent.
    """
    session = make_session(otp_code="123456")
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL_WITH_OTP, payload=LOGIN_OK_WITH_DID)
        await session.connect()
        assert session.device_id == "did-new"

        m.get(f"{BASE_URL}/api/v1/workload/device_workload", status=401)
        m.get(LOGIN_URL_WITH_NEW_DEVICE_ID, payload=LOGIN_OK)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", payload={"total": 0, "items": []})

        result = await session.get("/api/v1/workload/device_workload")
        assert result == {"total": 0, "items": []}
        await session.disconnect()


# ── Error-code → exception mapping ──────────────────────────────────────────


async def test_connect_without_otp_raises_otp_required_error() -> None:
    """error_code=403 (two-factor code required) raises OTPRequiredError, not bare AuthenticationError."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload=LOGIN_FAIL_OTP_REQUIRED)
        with pytest.raises(OTPRequiredError) as exc_info:
            await session.connect()
    assert exc_info.value.error_code == 403


async def test_connect_with_wrong_otp_raises_otp_incorrect_error() -> None:
    """error_code=404 (two-factor code incorrect) raises OTPIncorrectError, not bare AuthenticationError."""
    session = make_session(otp_code="000000")
    async with aiointercept(mock_external_urls=True) as m:
        m.get(LOGIN_URL_WITH_OTP_WRONG_CODE, payload=LOGIN_FAIL_OTP_INCORRECT)
        with pytest.raises(OTPIncorrectError) as exc_info:
            await session.connect()
    assert exc_info.value.error_code == 404


@pytest.mark.parametrize("code", [119, 400, 401, 402, 406, 407, 430])
async def test_other_auth_error_codes_still_raise_plain_authentication_error(code: int) -> None:
    """Codes other than 403/404 keep raising bare AuthenticationError, unaffected by this feature."""
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        m.get(TESTUSER_LOGIN_URL, payload={"success": False, "error": {"code": code}})
        with pytest.raises(AuthenticationError) as exc_info:
            await session.connect()
    assert type(exc_info.value) is AuthenticationError
    assert exc_info.value.error_code == code


@pytest.mark.parametrize("code", [403, 404])
async def test_business_api_errorcode_403_404_still_raise_plain_authentication_error(code: int) -> None:
    """_raise_for_error_code() — the generic path for an auth errorCode found in *any* 2xx
    response body, not just login — must NOT special-case 403/404 into
    OTPRequiredError/OTPIncorrectError. Those codes only mean "two-factor" in an actual
    SYNO.API.Auth login response; _do_login() never routes through this function at all (it
    has its own separate inline check), so an unrelated business-API call that happens to
    carry errorCode 403/404 in its body must keep raising plain AuthenticationError, exactly
    like every other code in this shared set — otherwise an unrelated failure gets actively
    misreported as "two-factor authentication is required".
    """
    session = make_session()
    async with aiointercept(mock_external_urls=True) as m:
        await connect_session(m, session)
        m.get(f"{BASE_URL}/api/v1/workload/device_workload", payload={"errorCode": code, "message": "..."})
        with pytest.raises(AuthenticationError) as exc_info:
            await session.get("/api/v1/workload/device_workload")
        await disconnect_session(m, session)
    assert type(exc_info.value) is AuthenticationError
    assert exc_info.value.error_code == code
