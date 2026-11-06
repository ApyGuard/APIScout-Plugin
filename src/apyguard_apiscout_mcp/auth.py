from __future__ import annotations

import json
import logging
import os
import stat
import ssl
import time
from dataclasses import asdict, dataclass, replace
from http.client import HTTPException
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import certifi

from apyguard_apiscout_mcp import __version__
from apyguard_apiscout_mcp.errors import (
    AuthenticationPendingError,
    AuthenticationRequiredError,
    AuthenticationServiceError,
    AuthenticationStorageError,
)
from apyguard_apiscout_mcp.types import (
    AuthStatus,
    LoginCompletionReport,
    LoginStartReport,
    SignOutReport,
)


DEFAULT_API_URL = "https://api.apyguard.com"
# What ApyGuard's tokens for each client start with.
TOKEN_PREFIXES = {
    "codex": "apg_cdx_",
    "claude_code": "apg_cc_",
}
DEVICE_PATH = "/api/api_discovery/integrations/device"
TOKEN_PATH = "/api/api_discovery/integrations/token"
REVOKE_PATH = "/api/api_discovery/integrations/revoke"
DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
AUTH_FILE_ENVIRONMENT_VARIABLE = "APYGUARD_APISCOUT_AUTH_FILE"
CLIENT_ENVIRONMENT_VARIABLE = "APYGUARD_APISCOUT_CLIENT"
API_URL_ENVIRONMENT_VARIABLE = "APYGUARD_APISCOUT_API_URL"
AUTH_FILE_PERMISSIONS = 0o600
AUTH_DIRECTORY_PERMISSIONS = 0o700
AUTH_REQUEST_TIMEOUT_SECONDS = 10.0
# RFC 8628: a poller told to slow down waits five seconds longer from then on.
SLOW_DOWN_INCREMENT_SECONDS = 5
MAX_ERROR_RESPONSE_CHARACTERS = 4_096
AUTH_USER_AGENT = f"ApyGuard-APIScout-MCP/{__version__}"
AUTH_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingLogin:
    """A device sign-in waiting for its user to approve the code in the browser."""

    device_code: str
    user_code: str
    verification_uri_complete: str
    expires_at: float
    interval: int
    last_polled_at: float | None = None
    # The API the sign-in was started at: only it can finish it.
    api_url: str | None = None


@dataclass(frozen=True)
class StoredAuthState:
    """This installation's id, its access token once signed in, and a sign-in in progress.

    The installation id names the installation to ApyGuard and is not secret; it is kept when the
    user signs out, so signing in again replaces this installation's token. The access token is the
    credential and is stored only in this owner-only file, with the API that issued it: it is sent
    nowhere else, so after APYGUARD_APISCOUT_API_URL changes the user signs in there.
    """

    client_type: str
    installation_id: str
    access_token: str | None = None
    pending: PendingLogin | None = None
    api_url: str | None = None


def get_client_type() -> str:
    client_type = os.environ.get(CLIENT_ENVIRONMENT_VARIABLE, "").strip()
    if client_type not in TOKEN_PREFIXES:
        raise AuthenticationStorageError(
            f'APIScout client must be "codex" or "claude_code": client_type="{client_type}".'
        )
    return client_type


def get_auth_file_path() -> Path:
    configured_path = os.environ.get(AUTH_FILE_ENVIRONMENT_VARIABLE)
    if configured_path is not None and configured_path.strip():
        return Path(configured_path).expanduser().resolve()
    return (Path.home() / ".config" / "apyguard" / "apiscout" / f"{get_client_type()}.json").resolve()


def get_api_url() -> str:
    """The ApyGuard API the plugin signs in to and sends its requests to, e.g. an on-prem appliance's."""
    configured_url = os.environ.get(API_URL_ENVIRONMENT_VARIABLE)
    api_url = (
        configured_url.strip()
        if configured_url is not None and configured_url.strip()
        else DEFAULT_API_URL
    ).rstrip("/")
    parsed_url = urlsplit(api_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise AuthenticationServiceError(
            f'ApyGuard API URL must be HTTP or HTTPS: api_url="{api_url}".'
        )
    return api_url


def _parse_pending_login(payload: object, auth_file_path: Path) -> PendingLogin | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise AuthenticationStorageError(
            f'Authentication file contains an invalid pending sign-in: path="{auth_file_path}".'
        )
    device_code = payload.get("device_code")
    user_code = payload.get("user_code")
    verification_uri_complete = payload.get("verification_uri_complete")
    expires_at = payload.get("expires_at")
    interval = payload.get("interval")
    last_polled_at = payload.get("last_polled_at")
    api_url = payload.get("api_url")
    if (
        not isinstance(device_code, str)
        or not device_code
        or not isinstance(user_code, str)
        or not isinstance(verification_uri_complete, str)
        or not isinstance(expires_at, (int, float))
        or isinstance(interval, bool)
        or not isinstance(interval, int)
        or not (last_polled_at is None or isinstance(last_polled_at, (int, float)))
        or not (api_url is None or isinstance(api_url, str))
    ):
        raise AuthenticationStorageError(
            f'Authentication file contains an invalid pending sign-in: path="{auth_file_path}".'
        )
    return PendingLogin(
        device_code=device_code,
        user_code=user_code,
        verification_uri_complete=verification_uri_complete,
        expires_at=float(expires_at),
        interval=interval,
        last_polled_at=None if last_polled_at is None else float(last_polled_at),
        api_url=api_url,
    )


def _parse_auth_state(payload: object, auth_file_path: Path) -> StoredAuthState:
    if not isinstance(payload, Mapping):
        raise AuthenticationStorageError(
            f'Authentication file must contain an object: path="{auth_file_path}".'
        )
    client_type = payload.get("client_type")
    installation_id = payload.get("installation_id")
    access_token = payload.get("access_token")
    api_url = payload.get("api_url")
    if client_type != get_client_type():
        raise AuthenticationStorageError(
            f'Authentication file belongs to a different APIScout client: path="{auth_file_path}".'
        )
    if not isinstance(installation_id, str) or not installation_id.strip():
        raise AuthenticationStorageError(
            f'Authentication file contains an invalid installation id: path="{auth_file_path}".'
        )
    if access_token is not None and (not isinstance(access_token, str) or not access_token.strip()):
        raise AuthenticationStorageError(
            f'Authentication file contains an invalid access token: path="{auth_file_path}".'
        )
    if api_url is not None and not isinstance(api_url, str):
        raise AuthenticationStorageError(
            f'Authentication file contains an invalid API URL: path="{auth_file_path}".'
        )
    return StoredAuthState(
        client_type=client_type,
        installation_id=installation_id.strip(),
        access_token=access_token.strip() if access_token is not None else None,
        pending=_parse_pending_login(payload=payload.get("pending"), auth_file_path=auth_file_path),
        api_url=api_url,
    )


def load_auth_state(auth_file_path: Path) -> StoredAuthState | None:
    if not auth_file_path.exists():
        return None
    if not auth_file_path.is_file():
        raise AuthenticationStorageError(
            f'Authentication path is not a file: path="{auth_file_path}".'
        )
    permissions = stat.S_IMODE(auth_file_path.stat().st_mode)
    if permissions & 0o077:
        raise AuthenticationStorageError(
            f'Authentication file permissions are too broad: path="{auth_file_path}" '
            f'permissions="{oct(permissions)}" expected="0o600".'
        )
    try:
        payload = json.loads(auth_file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuthenticationStorageError(
            f'Authentication file could not be read: path="{auth_file_path}" '
            f'error="{error}".'
        ) from error
    if isinstance(payload, Mapping) and "api_key" in payload:
        # The key earlier versions signed in with is not used again: its user signs in anew.
        _remove_auth_file(auth_file_path=auth_file_path)
        logger.info(
            "Removed the integration key of an earlier APIScout version",
            extra={"auth_file_path": str(auth_file_path)},
        )
        return None
    return _parse_auth_state(payload=payload, auth_file_path=auth_file_path)


def save_auth_state(auth_file_path: Path, state: StoredAuthState) -> None:
    auth_file_path.parent.mkdir(
        mode=AUTH_DIRECTORY_PERMISSIONS,
        parents=True,
        exist_ok=True,
    )
    os.chmod(auth_file_path.parent, AUTH_DIRECTORY_PERMISSIONS)
    temporary_path = auth_file_path.with_name(f".{auth_file_path.name}.{uuid4().hex}.tmp")
    try:
        file_descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            AUTH_FILE_PERMISSIONS,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as auth_file:
            json.dump(asdict(state), auth_file, ensure_ascii=False)
            auth_file.write("\n")
        os.replace(temporary_path, auth_file_path)
        os.chmod(auth_file_path, AUTH_FILE_PERMISSIONS)
    except OSError as error:
        raise AuthenticationStorageError(
            f'Authentication state could not be stored: path="{auth_file_path}" '
            f'error="{error}".'
        ) from error
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _remove_auth_file(auth_file_path: Path) -> None:
    try:
        auth_file_path.unlink()
    except OSError as error:
        raise AuthenticationStorageError(
            f'Authentication state could not be removed: path="{auth_file_path}" '
            f'error="{error}".'
        ) from error


def _post_json(
    url: str,
    request_body: Mapping[str, Any] | None,
    access_token: str | None = None,
) -> tuple[int, object]:
    """POST to ApyGuard and return the status and the JSON body, or None for a body that is not JSON."""
    headers = {"Accept": "application/json", "User-Agent": AUTH_USER_AGENT}
    encoded_body = None
    if request_body is not None:
        encoded_body = json.dumps(request_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if access_token is not None:
        headers["Authorization"] = f"Bearer {access_token}"
    request = Request(url, data=encoded_body, method="POST", headers=headers)
    try:
        with urlopen(
            request,
            timeout=AUTH_REQUEST_TIMEOUT_SECONDS,
            context=AUTH_SSL_CONTEXT,
        ) as response:
            status_code = response.status
            response_body = response.read()
    except HTTPError as error:
        status_code = error.code
        response_body = error.read()[:MAX_ERROR_RESPONSE_CHARACTERS]
    except (OSError, HTTPException) as error:
        # urllib wraps only connection errors in URLError: a read timeout or a dropped connection
        # arrives as it is.
        raise AuthenticationServiceError(
            f'ApyGuard sign-in request failed before receiving a complete response: '
            f'url="{url}" error="{error}".'
        ) from error
    try:
        return status_code, json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status_code, None


def _revoke_access_token(api_url: str, access_token: str) -> bool:
    """Tell the API that issued the token that it is no longer used. Whether that worked or not, the plugin has dropped it."""
    try:
        status_code, _ = _post_json(
            url=f"{api_url}{REVOKE_PATH}",
            request_body=None,
            access_token=access_token,
        )
    except AuthenticationServiceError as error:
        logger.warning("ApyGuard token revocation failed", extra={"error": str(error)})
        return False
    return status_code == 200


def _current_token(state: StoredAuthState | None) -> str | None:
    """The stored token if the API it is for is the one configured now; it is sent nowhere else."""
    if state is None or state.access_token is None or state.api_url != get_api_url():
        return None
    return state.access_token


def get_apyguard_auth_status() -> AuthStatus:
    state = load_auth_state(auth_file_path=get_auth_file_path())
    authenticated = _current_token(state) is not None
    return {
        "authenticated": authenticated,
        "login_required": not authenticated,
        "login_pending": state is not None and state.pending is not None,
    }


def start_apyguard_login() -> LoginStartReport:
    """Start a device sign-in (RFC 8628): the user approves its code on ApyGuard's device page."""
    auth_file_path = get_auth_file_path()
    client_type = get_client_type()
    existing_state = load_auth_state(auth_file_path=auth_file_path)
    installation_id = existing_state.installation_id if existing_state is not None else str(uuid4())
    api_url = get_api_url()
    status_code, payload = _post_json(
        url=f"{api_url}{DEVICE_PATH}",
        request_body={"client_type": client_type, "installation_id": installation_id},
    )
    if status_code != 200 or not isinstance(payload, Mapping):
        raise AuthenticationServiceError(
            f'ApyGuard could not start the sign-in: status={status_code} response_body="{payload}".'
        )
    device_code = payload.get("device_code")
    user_code = payload.get("user_code")
    verification_uri_complete = payload.get("verification_uri_complete")
    expires_in = payload.get("expires_in")
    interval = payload.get("interval")
    if (
        not isinstance(device_code, str)
        or not device_code
        or not isinstance(user_code, str)
        or not user_code
        or not isinstance(verification_uri_complete, str)
        or urlsplit(verification_uri_complete).scheme not in {"http", "https"}
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, int)
        or isinstance(interval, bool)
        or not isinstance(interval, int)
    ):
        raise AuthenticationServiceError(
            f'ApyGuard answered the sign-in with an unexpected response: response_body="{payload}".'
        )
    save_auth_state(
        auth_file_path=auth_file_path,
        state=StoredAuthState(
            client_type=client_type,
            installation_id=installation_id,
            # A connection already made keeps working until the new sign-in finishes.
            access_token=existing_state.access_token if existing_state is not None else None,
            api_url=existing_state.api_url if existing_state is not None else None,
            pending=PendingLogin(
                device_code=device_code,
                user_code=user_code,
                verification_uri_complete=verification_uri_complete,
                expires_at=time.time() + expires_in,
                interval=interval,
                api_url=api_url,
            ),
        ),
    )
    return {
        "authenticated": False,
        "login_url": verification_uri_complete,
        "user_code": user_code,
        "expires_in": expires_in,
        # The link is in the instructions too, for a client that relays only them.
        "instructions": (
            f"Open {verification_uri_complete}, complete ApyGuard login, 2FA, or email verification, "
            f"check that the page shows the code {user_code}, and approve it. Then return to this "
            "conversation and repeat the APIScout request; the sign-in finishes automatically."
        ),
    }


def _redeem_pending_login(auth_file_path: Path, state: StoredAuthState) -> str:
    """Ask once whether the user approved the pending sign-in, and store the token if so."""
    pending = state.pending
    if pending is None:
        raise AuthenticationRequiredError(
            "No ApyGuard sign-in has been started. Call start_apyguard_login first."
        )
    approve_message = (
        f"Approve the code {pending.user_code} at {pending.verification_uri_complete}, then "
        "repeat the APIScout request."
    )
    if pending.api_url is None or pending.api_url != get_api_url():
        save_auth_state(auth_file_path=auth_file_path, state=replace(state, pending=None))
        raise AuthenticationRequiredError(
            "The ApyGuard API URL changed after the sign-in started. Call start_apyguard_login to start again."
        )
    now = time.time()
    if now >= pending.expires_at:
        save_auth_state(auth_file_path=auth_file_path, state=replace(state, pending=None))
        raise AuthenticationRequiredError(
            "The ApyGuard sign-in code expired. Call start_apyguard_login to start again."
        )
    if pending.last_polled_at is not None and now - pending.last_polled_at < pending.interval:
        # Sooner than ApyGuard allows: it would answer slow_down.
        raise AuthenticationPendingError(approve_message)
    save_auth_state(
        auth_file_path=auth_file_path,
        state=replace(state, pending=replace(pending, last_polled_at=now)),
    )
    status_code, payload = _post_json(
        url=f"{pending.api_url}{TOKEN_PATH}",
        request_body={
            "grant_type": DEVICE_CODE_GRANT_TYPE,
            "device_code": pending.device_code,
            "client_type": state.client_type,
        },
    )
    if status_code == 200:
        access_token = payload.get("access_token") if isinstance(payload, Mapping) else None
        if not isinstance(access_token, str) or not access_token.startswith(TOKEN_PREFIXES[state.client_type]):
            raise AuthenticationServiceError(
                "ApyGuard answered the sign-in with a token the plugin could not read."
            )
        save_auth_state(
            auth_file_path=auth_file_path,
            state=StoredAuthState(
                client_type=state.client_type,
                installation_id=state.installation_id,
                access_token=access_token,
                api_url=pending.api_url,
            ),
        )
        if state.access_token is not None and state.access_token != access_token and state.api_url is not None:
            # A sign-in to another account or ApyGuard leaves the earlier token live otherwise.
            _revoke_access_token(api_url=state.api_url, access_token=state.access_token)
        return access_token
    error = payload.get("error") if isinstance(payload, Mapping) else None
    if error == "authorization_pending":
        raise AuthenticationPendingError(approve_message)
    if error == "slow_down":
        save_auth_state(
            auth_file_path=auth_file_path,
            state=replace(
                state,
                pending=replace(
                    pending,
                    interval=pending.interval + SLOW_DOWN_INCREMENT_SECONDS,
                    last_polled_at=now,
                ),
            ),
        )
        raise AuthenticationPendingError(approve_message)
    endings = {
        "access_denied": "The ApyGuard sign-in was denied.",
        "expired_token": "The ApyGuard sign-in code expired.",
        "invalid_grant": "The ApyGuard sign-in can no longer be finished.",
    }
    if error in endings:
        save_auth_state(auth_file_path=auth_file_path, state=replace(state, pending=None))
        raise AuthenticationRequiredError(
            f"{endings[error]} Call start_apyguard_login to start again."
        )
    raise AuthenticationServiceError(
        f'ApyGuard token request failed: status={status_code} response_body="{payload}".'
    )


def complete_apyguard_login() -> LoginCompletionReport:
    auth_file_path = get_auth_file_path()
    state = load_auth_state(auth_file_path=auth_file_path)
    if state is not None and state.pending is None and _current_token(state) is not None:
        return {
            "authenticated": True,
            "message": "ApyGuard is already connected.",
        }
    if state is None or state.pending is None:
        raise AuthenticationRequiredError(
            "No ApyGuard sign-in has been started. Call start_apyguard_login first."
        )
    _redeem_pending_login(auth_file_path=auth_file_path, state=state)
    return {
        "authenticated": True,
        "message": "ApyGuard account connection completed successfully.",
    }


def get_authenticated_apyguard_token() -> str:
    """Return this installation's access token, finishing a sign-in the user has approved."""
    auth_file_path = get_auth_file_path()
    state = load_auth_state(auth_file_path=auth_file_path)
    if state is not None and state.pending is not None:
        try:
            return _redeem_pending_login(auth_file_path=auth_file_path, state=state)
        except AuthenticationPendingError:
            if _current_token(state) is None:
                raise
    current_token = _current_token(state)
    if current_token is not None:
        return current_token
    raise AuthenticationRequiredError(
        "ApyGuard login is required before using APIScout. Call "
        "start_apyguard_login and open the returned URL."
    )


def require_apyguard_authentication() -> None:
    get_authenticated_apyguard_token()


def discard_rejected_token(access_token: str) -> None:
    """ApyGuard refused this token: revoked, unused for 90 days, or its user can no longer sign in."""
    auth_file_path = get_auth_file_path()
    state = load_auth_state(auth_file_path=auth_file_path)
    # Only the token the refused request carried: a sign-in since then keeps its own.
    if state is not None and state.access_token == access_token:
        save_auth_state(auth_file_path=auth_file_path, state=replace(state, access_token=None))


def sign_out_apyguard() -> SignOutReport:
    auth_file_path = get_auth_file_path()
    state = load_auth_state(auth_file_path=auth_file_path)
    if state is None:
        return {
            "authenticated": False,
            "credential_removed": False,
            "token_revoked": False,
        }
    # The installation id stays: signing in again replaces this installation's token.
    save_auth_state(
        auth_file_path=auth_file_path,
        state=StoredAuthState(client_type=state.client_type, installation_id=state.installation_id),
    )
    # Revoked where it was issued, even if APYGUARD_APISCOUT_API_URL has changed since.
    token_revoked = (
        _revoke_access_token(api_url=state.api_url, access_token=state.access_token)
        if state.access_token is not None and state.api_url is not None
        else False
    )
    return {
        "authenticated": False,
        "credential_removed": state.access_token is not None or state.pending is not None,
        "token_revoked": token_revoked,
    }
