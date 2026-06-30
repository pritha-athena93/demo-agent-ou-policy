import datetime

import pytest

import broker


def test_assert_account_boundary_rejects_unknown_account():
    with pytest.raises(broker.BrokerDenied):
        broker._assert_account_boundary("some-account-that-does-not-exist")

    # known accounts should not raise
    for account in broker.VALID_ACCOUNTS:
        broker._assert_account_boundary(account)


def test_request_scoped_credential_denies_protected_policy_and_stores_nothing():
    before = dict(broker._exceptions)
    with pytest.raises(broker.BrokerDenied):
        broker.request_scoped_credential("no-public-ip-ec2", "prod-core", 3600)
    # no exception record should have been stored for a denied request
    assert broker._exceptions == before


def test_request_scoped_credential_caps_duration():
    ninety_days_seconds = 90 * 24 * 3600
    grant = broker.request_scoped_credential("max-ebs-volume-size-dev", "dev-sandbox", ninety_days_seconds)

    expires_at = datetime.datetime.fromisoformat(grant["expires_at"])
    now = datetime.datetime.now(datetime.timezone.utc)
    granted_seconds = (expires_at - now).total_seconds()

    assert granted_seconds <= broker.MAX_DURATION_SECONDS
    assert granted_seconds > 0


def test_request_scoped_credential_unknown_account_raises():
    with pytest.raises(broker.BrokerDenied):
        broker.request_scoped_credential("max-ebs-volume-size-dev", "not-a-real-account", 3600)


def test_is_exception_active_before_and_after_expiry():
    active_id = "active-credential"
    broker._exceptions[active_id] = {
        "credential_id": active_id,
        "expires_at": (datetime.datetime.now(datetime.timezone.utc)
                       + datetime.timedelta(hours=1)).isoformat(),
    }
    assert broker.is_exception_active(active_id) is True

    expired_id = "expired-credential"
    broker._exceptions[expired_id] = {
        "credential_id": expired_id,
        "expires_at": (datetime.datetime.now(datetime.timezone.utc)
                       - datetime.timedelta(hours=1)).isoformat(),
    }
    assert broker.is_exception_active(expired_id) is False

    assert broker.is_exception_active("never-granted") is False
