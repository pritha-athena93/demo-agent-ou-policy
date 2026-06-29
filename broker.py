"""JIT access broker. Deterministic. Stands between any agent and the real write.

The agent-ops role has no standing permission to call update_org_policy anywhere.
This broker is the only path to a short-lived, scoped credential. Even if the
broker's own logic above this line has a bug, the role's trust-policy condition
(account-ID match) is the real backstop -- enforced again here as a hard assert
to mirror what IAM would refuse regardless of what the broker decided.
"""

import datetime
import uuid

from policy_registry import check_policy_registry

VALID_ACCOUNTS = {"dev-sandbox", "dev-shared", "prod-core"}
MAX_DURATION_SECONDS = 3600

_exceptions: dict = {}


class BrokerDenied(Exception):
    pass


def _assert_account_boundary(target_account: str) -> None:
    if target_account not in VALID_ACCOUNTS:
        raise BrokerDenied(f"trust-policy condition rejected unknown account: {target_account}")


def request_scoped_credential(policy_id: str, target_account: str, duration_seconds: int | None) -> dict:
    _assert_account_boundary(target_account)

    check = check_policy_registry(policy_id)
    if check["protected"]:
        raise BrokerDenied(f"policy '{policy_id}' is protected, no grant issued")

    capped_duration = min(duration_seconds, MAX_DURATION_SECONDS) if duration_seconds else MAX_DURATION_SECONDS
    credential_id = str(uuid.uuid4())
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=capped_duration)

    grant = {
        "credential_id": credential_id,
        "scoped_to_policy": policy_id,
        "scoped_to_account": target_account,
        "expires_at": expires_at.isoformat(),
        "permanent": duration_seconds is None,
    }

    if duration_seconds is not None:
        _exceptions[credential_id] = grant

    return grant


def is_exception_active(credential_id: str) -> bool:
    grant = _exceptions.get(credential_id)
    if grant is None:
        return False
    expires_at = datetime.datetime.fromisoformat(grant["expires_at"])
    return datetime.datetime.now(datetime.timezone.utc) < expires_at
