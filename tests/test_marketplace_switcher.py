from src.marketplace_switcher import MarketplaceSwitcher


class _MarketplacePage:
    def __init__(
        self,
        url: str = "https://sellercentral.amazon.com/home",
        *,
        marketplace_id: str = "",
        label: str = "",
    ):
        self.url = url
        self.marketplace_id = marketplace_id
        self.label = label

    def evaluate(self, _script):
        return {"marketplace_id": self.marketplace_id, "label": self.label}


def test_amazon_com_mexico_header_is_not_misclassified_as_us():
    page = _MarketplacePage(label="Mexico")

    assert MarketplaceSwitcher(page).get_current_marketplace() == "MX"


def test_amazon_com_mexico_marketplace_id_is_not_misclassified_as_us():
    page = _MarketplacePage(marketplace_id="A1AM78C64UM0Y8")

    assert MarketplaceSwitcher(page).get_current_marketplace() == "MX"


def test_shared_domain_without_active_market_signal_is_unknown():
    page = _MarketplacePage()

    assert MarketplaceSwitcher(page).get_current_marketplace() is None


def test_url_mkid_and_page_signal_must_not_conflict():
    page = _MarketplacePage(
        "https://sellercentral.amazon.com/home?mons_sel_mkid=amzn1.mp.o.ATVPDKIKX0DER",
        marketplace_id="A1AM78C64UM0Y8",
        label="Mexico",
    )

    assert MarketplaceSwitcher(page).get_current_marketplace() is None


def test_mexico_page_uses_existing_account_switcher_for_us(monkeypatch):
    page = _MarketplacePage(label="Mexico")
    switcher = MarketplaceSwitcher(page)
    switched = []

    def fake_switch(target, target_info):
        switched.append((target, target_info.name))
        page.marketplace_id = "ATVPDKIKX0DER"
        page.label = "United States"
        return True

    monkeypatch.setattr(switcher, "_do_switch", fake_switch)

    assert switcher.switch_to_marketplace("US", max_retries=1) == (True, "US")
    assert switched == [("US", "United States")]


def test_verified_us_page_can_skip_account_switcher(monkeypatch):
    page = _MarketplacePage(
        marketplace_id="ATVPDKIKX0DER",
        label="United States",
    )
    switcher = MarketplaceSwitcher(page)

    def unexpected_switch(*_args, **_kwargs):
        raise AssertionError("verified US page should not open account-switcher")

    monkeypatch.setattr(switcher, "_do_switch", unexpected_switch)

    assert switcher.switch_to_marketplace("US", max_retries=1) == (True, "US")


def test_switch_verification_rejects_mexico_on_shared_domain():
    page = _MarketplacePage(
        marketplace_id="A1AM78C64UM0Y8",
        label="Mexico",
    )

    assert MarketplaceSwitcher(page)._verify_switch("US") == (False, "MX")
