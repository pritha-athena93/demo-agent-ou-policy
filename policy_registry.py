"""Deterministic policy registry. No LLM involved in any lookup here."""

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
}


def check_policy_registry(policy_id: str) -> dict:
    entry = REGISTRY.get(policy_id)
    if entry is None:
        return {"policy_id": policy_id, "found": False, "protected": True, "current_state": None}
    return {
        "policy_id": policy_id,
        "found": True,
        "protected": entry["protected"],
        "current_state": entry["current_state"],
        "description": entry["description"],
    }
