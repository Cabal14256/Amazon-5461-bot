from src.failure_classifier import classify_failure


def test_telemetry_429_and_aborted_chunk_do_not_mark_account_rate_limited():
    result = {
        "status": "draft",
        "note": "无法进入5461表单 | Dashboard found Draft",
        "dashboard_check": {"status": "draft"},
    }
    network_errors = [
        {
            "type": "requestfailed",
            "url": "https://d2u5ksl78hejbt.cloudfront.net/abis/main.js",
            "error_text": "net::ERR_ABORTED",
        },
        {
            "type": "response",
            "status": 429,
            "url": "https://sellercentral.amazon.com/quicklist/katalLogs",
        },
    ]

    got = classify_failure(result, network_errors)

    assert got["severity"] == "draft"
    assert "draft_detected" in got["tags"]
    assert "account_rate_limited" not in got["tags"]
    assert "cloudfront_chunk_failed" not in got["tags"]


def test_restriction_endpoint_429_remains_rate_limited():
    result = {"status": "failed", "note": "无法进入5461表单"}
    network_errors = [
        {
            "type": "response",
            "status": 429,
            "url": "https://sellercentral.amazon.com/abis/ajax/getRestrictionEndpoint",
        }
    ]

    got = classify_failure(result, network_errors)

    assert got["severity"] == "rate_limited"
    assert "restriction_endpoint_429" in got["tags"]
    assert "account_rate_limited" in got["tags"]
