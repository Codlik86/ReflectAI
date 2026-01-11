from datetime import datetime, timedelta, timezone

from app.services.access_state import _calc_access_state


def test_expired_subscription_overrides_status() -> None:
    now = datetime.now(timezone.utc)
    state = _calc_access_state(
        now=now,
        trial_started_at=None,
        trial_expires_at=None,
        subscription_until=now - timedelta(days=1),
        subscription_status="active",
    )
    assert state["has_access"] is False
    assert state["reason"] == "none"


def test_active_subscription_ignores_user_cache() -> None:
    now = datetime.now(timezone.utc)
    state = _calc_access_state(
        now=now,
        trial_started_at=None,
        trial_expires_at=None,
        subscription_until=now + timedelta(days=3),
        subscription_status="active",
    )
    assert state["has_access"] is True
    assert state["reason"] == "subscription"


def test_active_trial_grants_access() -> None:
    now = datetime.now(timezone.utc)
    state = _calc_access_state(
        now=now,
        trial_started_at=now - timedelta(days=1),
        trial_expires_at=None,
        subscription_until=None,
        subscription_status=None,
    )
    assert state["has_access"] is True
    assert state["reason"] == "trial"
