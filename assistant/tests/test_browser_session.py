import pytest

import assistant.security as security


@pytest.fixture(autouse=True)
def operator_session(monkeypatch):
    monkeypatch.setenv("THURSDAY_API_TOKEN", "synthetic-operator-token")
    security.api_token.cache_clear()
    monkeypatch.setattr(security, "_session_now", lambda: 1_700_000_000)
    yield
    security.api_token.cache_clear()


def cookie(value):
    return {"Cookie": f"Thursday-Session={value}"}


def test_optional_auth_still_allows_requests_without_configured_token(monkeypatch):
    monkeypatch.delenv("THURSDAY_API_TOKEN")
    security.api_token.cache_clear()
    assert security.check_request_auth({})
    with pytest.raises(RuntimeError, match="Configure THURSDAY_API_TOKEN"):
        security.create_browser_session()


def test_missing_or_wrong_operator_token_is_rejected():
    assert not security.check_request_auth({})
    assert not security.check_request_auth({"Authorization": "Bearer wrong"})
    assert not security.check_request_auth({"X-Thursday-Token": "wrong"})
    assert not security.check_request_auth({}, "wrong")


@pytest.mark.parametrize(("headers", "query"), [
    ({"Authorization": "Bearer synthetic-operator-token"}, None),
    ({"authorization": "bearer synthetic-operator-token"}, None),
    ({"X-Thursday-Token": "synthetic-operator-token"}, None),
    ({"x-thursday-token": "synthetic-operator-token"}, None),
    ({}, "synthetic-operator-token"),
])
def test_existing_token_auth_is_preserved(headers, query):
    assert security.check_request_auth(headers, query)


def test_new_cookie_authenticates_without_exposing_operator_token():
    session = security.create_browser_session()
    assert "synthetic-operator-token" not in session
    assert security.check_request_auth(cookie(session))
    assert security.check_request_auth({"cookie": f"other=abc; Thursday-Session={session}"})
    assert security.create_browser_session() != session


def test_cookie_can_authorize_when_nonmatching_legacy_auth_is_present():
    headers = {**cookie(security.create_browser_session()), "Authorization": "Bearer old"}
    assert security.check_request_auth(headers)


@pytest.mark.parametrize(
    ("age", "accepted"), [(3599, True), (3599.999, True), (3600, False), (3601, False)]
)
def test_expiry_is_strictly_one_hour(monkeypatch, age, accepted):
    session = security.create_browser_session()
    monkeypatch.setattr(security, "_session_now", lambda: 1_700_000_000 + age)
    assert security.check_request_auth(cookie(session)) is accepted


@pytest.mark.parametrize(("skew", "accepted"), [(29, True), (30, True), (31, False)])
def test_future_timestamp_tolerance_is_at_most_thirty_seconds(monkeypatch, skew, accepted):
    session = security.create_browser_session()
    monkeypatch.setattr(security, "_session_now", lambda: 1_700_000_000 - skew)
    assert security.check_request_auth(cookie(session)) is accepted


@pytest.mark.parametrize("part", [0, 1, 2, 3])
def test_any_payload_or_signature_tampering_is_rejected(part):
    pieces = security.create_browser_session().split(".")
    text = pieces[part]
    pieces[part] = ("b" if text[0] != "b" else "c") + text[1:]
    assert not security.check_request_auth(cookie(".".join(pieces)))


def test_operator_token_rotation_revokes_existing_browser_sessions(monkeypatch):
    old = security.create_browser_session()
    monkeypatch.setenv("THURSDAY_API_TOKEN", "rotated-operator-token")
    security.api_token.cache_clear()
    assert not security.check_request_auth(cookie(old))
    assert security.check_request_auth(cookie(security.create_browser_session()))


@pytest.mark.parametrize("raw", [
    "", "Thursday-Session=garbage", 'Thursday-Session="unterminated',
    "Thursday-Session=v1.1700000000.bad.bad", "Thursday-Session=☃",
    "Thursday-Session=" + "x" * 4096, "broken header; Thursday-Session=broken",
    "Thursday-Session=v1." + "9" * 500 + ".bad.bad",
])
def test_malformed_or_oversized_cookie_is_rejected_without_exception(raw):
    assert not security.check_request_auth({"Cookie": raw})


def test_oversized_header_is_rejected_even_with_valid_cookie():
    session = security.create_browser_session()
    headers = {"Cookie": f"Thursday-Session={session}; unrelated=" + "x" * 4096}
    assert not security.check_request_auth(headers)
    headers["Authorization"] = "Bearer synthetic-operator-token"
    assert security.check_request_auth(headers)


def test_provider_key_cannot_issue_or_authenticate_browser_sessions(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "provider-key-fixture")
    session = security.create_browser_session()
    assert security.check_request_auth(cookie(session))
    assert not security.check_request_auth({"Authorization": "Bearer provider-key-fixture"})
    monkeypatch.setenv("OPENAI_API_KEY", "other-provider-key")
    assert security.check_request_auth(cookie(session))


@pytest.mark.parametrize(("headers", "query"), [
    ({"Authorization": "Bearer ☃"}, None),
    ({"X-Thursday-Token": "☃"}, None),
    ({}, "☃"),
])
def test_malformed_legacy_token_input_is_rejected_without_exception(headers, query):
    assert not security.check_request_auth(headers, query)
