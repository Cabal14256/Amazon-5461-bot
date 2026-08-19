from __future__ import annotations

import pytest

from src.auth_guard import AuthBlockedError, detect_auth_state, ensure_not_auth_blocked


class FakePage:
    def __init__(self, url: str, text: str, password: bool = False):
        self.url = url
        self.text = text
        self.password = password

    def evaluate(self, script, *_args):
        if 'input[type="password"]' in script:
            return self.password
        return self.text


@pytest.mark.parametrize(
    ("url", "text", "expected"),
    [
        ("https://sellercentral.amazon.com/ap/signin", "Sign in to your account", "login_required"),
        ("https://sellercentral.amazon.com/captcha", "Enter the characters you see", "captcha_required"),
        ("https://sellercentral.amazon.com/ap/challenge", "Enter the verification code", "two_factor_required"),
        ("https://sellercentral.amazon.com/ap/check", "Unusual activity. Verify your identity", "account_risk"),
        ("https://sellercentral.amazon.com/home", "Seller Central", "authenticated"),
    ],
)
def test_detects_authentication_boundaries(url, text, expected):
    assert detect_auth_state(FakePage(url, text)).state == expected


def test_auth_stop_is_typed_and_carries_phase():
    with pytest.raises(AuthBlockedError) as caught:
        ensure_not_auth_blocked(
            FakePage("https://sellercentral.amazon.com/ap/signin", "Sign in"),
            phase="before_submit",
        )
    assert caught.value.phase == "before_submit"
    assert caught.value.auth_state.state == "login_required"
