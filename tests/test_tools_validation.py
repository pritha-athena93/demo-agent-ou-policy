import tools


def _valid_update_input(**overrides):
    base = {
        "policy_id": "no-public-ip-ec2",
        "new_state": "disabled",
        "target_account": "prod-core",
        "duration": 3600,
    }
    base.update(overrides)
    return base


def test_update_org_policy_rejects_unknown_policy_id():
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(policy_id="not-a-real-policy"))
    assert err is not None


def test_update_org_policy_rejects_unknown_target_account():
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(target_account="not-a-real-account"))
    assert err is not None


def test_update_org_policy_rejects_empty_new_state():
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(new_state=""))
    assert err is not None


def test_update_org_policy_rejects_oversized_new_state():
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(new_state="x" * 201))
    assert err is not None


def test_update_org_policy_rejects_non_positive_duration():
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(duration=0))
    assert err is not None
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(duration=-100))
    assert err is not None


def test_update_org_policy_rejects_non_integer_duration():
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(duration="3600"))
    assert err is not None
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(duration=True))
    assert err is not None


def test_update_org_policy_accepts_too_large_but_well_formed_duration():
    # Must NOT reject -- capping an over-long request is broker.py's job,
    # not this layer's. This is the one case that should assert "no error".
    ninety_days_seconds = 90 * 24 * 3600
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(duration=ninety_days_seconds))
    assert err is None


def test_update_org_policy_accepts_null_duration():
    err = tools.validate_tool_input("update_org_policy", _valid_update_input(duration=None))
    assert err is None


def test_update_org_policy_accepts_valid_input():
    err = tools.validate_tool_input("update_org_policy", _valid_update_input())
    assert err is None


def test_check_policy_registry_rejects_empty_policy_id():
    assert tools.validate_tool_input("check_policy_registry", {"policy_id": ""}) is not None
    assert tools.validate_tool_input("check_policy_registry", {"policy_id": "no-public-ip-ec2"}) is None


def test_request_human_approval_rejects_empty_summary():
    assert tools.validate_tool_input("request_human_approval", {"summary": ""}) is not None
    assert tools.validate_tool_input("request_human_approval", {"summary": "  "}) is not None
    assert tools.validate_tool_input("request_human_approval", {"summary": "a real summary"}) is None


def test_propose_workaround_requires_string_request_and_policy_id():
    assert tools.validate_tool_input("propose_workaround", {"request": 123, "policy_id": "no-public-ip-ec2"}) is not None
    assert tools.validate_tool_input("propose_workaround", {"request": "text", "policy_id": ""}) is not None
    assert tools.validate_tool_input("propose_workaround", {"request": "text", "policy_id": "no-public-ip-ec2"}) is None
