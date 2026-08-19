"""Deterministic Seller Central authentication-state detection.

This module never logs in, fills credentials, solves CAPTCHA/2FA, or retries a
blocked page.  Callers must stop browser actions and hand the exact AdsPower
profile to a human when :func:`ensure_not_auth_blocked` raises.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

AUTH_BLOCK_TYPES = {
    "login_required",
    "captcha_required",
    "two_factor_required",
    "account_risk",
    "unknown_auth_state",
}

_LOGIN_MARKERS = (
    "sign in to your account",
    "sign-in",
    "email or mobile phone number",
    "enter your email or mobile phone number",
)
_CAPTCHA_MARKERS = (
    "enter the characters you see",
    "type the characters you see in this image",
    "solve this puzzle",
    "captcha",
)
_TWO_FACTOR_MARKERS = (
    "two-step verification",
    "two factor authentication",
    "verification code",
    "one time password",
    "enter the code",
)
_RISK_MARKERS = (
    "account has been locked",
    "unusual activity",
    "verify your identity",
    "temporarily locked",
    "suspicious activity",
    "additional verification required",
)


@dataclass(frozen=True)
class AuthState:
    state: str
    blocked: bool
    reason: str
    url: str
    text_available: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuthBlockedError(RuntimeError):
    """Typed stop signal; never feed this into server-error recovery."""

    def __init__(self, state: AuthState, *, phase: str = "unknown"):
        self.auth_state = state
        self.phase = str(phase or "unknown")
        self.block_id: int | None = None
        self.submit_fenced = False
        super().__init__(f"{state.state}:{self.phase}")


def _bounded_page_text(page: Any, limit: int = 20_000) -> tuple[str, bool]:
    try:
        text = page.evaluate(
            """limit => {
              const body = document.body;
              if (!body) return '';
              let text = body.innerText || body.textContent || '';
              for (const node of document.querySelectorAll('*')) {
                if (!node.shadowRoot) continue;
                text += ' ' + (node.shadowRoot.innerText || node.shadowRoot.textContent || '');
                if (text.length >= limit) break;
              }
              return text.slice(0, limit);
            }""",
            int(limit),
        )
        return str(text or ""), True
    except Exception:
        try:
            text = page.locator("body").inner_text(timeout=3_000)
            return str(text or "")[:limit], True
        except Exception:
            return "", False


def detect_auth_state(page: Any, *, text: str | None = None) -> AuthState:
    """Classify only authentication state; business page state stays separate."""

    url = str(getattr(page, "url", "") or "")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = (parsed.path or "").casefold()
    if text is None:
        page_text, text_available = _bounded_page_text(page)
    else:
        page_text, text_available = str(text), True
    lowered = page_text.casefold()

    if "captcha" in path or any(marker in lowered for marker in _CAPTCHA_MARKERS):
        return AuthState("captcha_required", True, "Seller Central requires CAPTCHA", url, text_available)
    if any(marker in lowered for marker in _TWO_FACTOR_MARKERS):
        return AuthState("two_factor_required", True, "Seller Central requires two-step verification", url, text_available)
    if any(marker in lowered for marker in _RISK_MARKERS):
        return AuthState("account_risk", True, "Seller Central requires account-risk review", url, text_available)

    password_present = False
    try:
        password_present = bool(
            page.evaluate(
                """() => !!document.querySelector(
                  'input[type="password"], input[name="password"], input#ap_password'
                )"""
            )
        )
    except Exception:
        pass
    if "/ap/signin" in path or password_present or any(marker in lowered for marker in _LOGIN_MARKERS):
        return AuthState("login_required", True, "Seller Central login is required", url, text_available)

    amazon_auth_path = bool(host.endswith("amazon.com") or ".amazon." in host) and path.startswith("/ap/")
    if amazon_auth_path:
        return AuthState(
            "unknown_auth_state",
            True,
            "Unrecognised Amazon authentication checkpoint",
            url,
            text_available,
        )
    if "sellercentral.amazon." in host or host == "sellercentral.amazon.com":
        return AuthState("authenticated", False, "Seller Central session is authenticated", url, text_available)
    return AuthState("not_seller_central", False, "Page is not a Seller Central authentication page", url, text_available)


def ensure_not_auth_blocked(page: Any, *, phase: str) -> AuthState:
    state = detect_auth_state(page)
    if state.blocked:
        raise AuthBlockedError(state, phase=phase)
    return state


def auth_block_reason(page: Any, text: str = "") -> str | None:
    """Compatibility adapter for the existing Case/approval flows."""

    state = detect_auth_state(page, text=text or None)
    return state.state if state.blocked else None
