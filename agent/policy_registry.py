"""Deterministic policy registry. No LLM involved in any lookup here.

protected is the base/default status. protected_in overrides it to True for
specific accounts (e.g. a policy that's open in dev but locked in prod).
Both fields are static local data -- no account-aware logic lives anywhere
else; check_policy_registry is the only place that combines them.
"""

REGISTRY = {
    "no-public-ip-ec2": {
        "description": "Org-wide SCP blocking EC2 instances with public IPs",
        "current_state": "enforced",
        "protected": True,
    },
    "max-ebs-volume-size-dev": {
        "description": "Dev-scoped cap on EBS volume size",
        "current_state": "500GB",
        "protected": False,
    },
    "allow-large-instance-types": {
        "description": "Permits instance types above the default size cap",
        "current_state": "capped-at-large",
        "protected": False,
        "protected_in": ["prod-core"],
    },
    "require-resource-tags": {
        "description": "Requires cost-allocation tags (Owner, Department) on new resources",
        "current_state": "enforced",
        "protected": False,
    },
    "allow-only-t3-instance-types": {
        "description": "Real org-wide SCP restricting EC2 launches to the t3 instance family",
        "current_state": "t3-only",
        "protected": False,
    },
}


def is_known_policy_id(policy_id: str) -> bool:
    return policy_id in REGISTRY


def check_policy_registry(policy_id: str, target_account: str | None = None) -> dict:
    entry = REGISTRY.get(policy_id)
    if entry is None:
        return {"policy_id": policy_id, "found": False, "protected": True,
                "current_state": None, "target_account": target_account}

    protected = entry["protected"] or (target_account in entry.get("protected_in", []))
    return {
        "policy_id": policy_id,
        "found": True,
        "protected": protected,
        "current_state": entry["current_state"],
        "description": entry["description"],
        "target_account": target_account,
    }
