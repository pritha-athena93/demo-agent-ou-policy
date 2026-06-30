import agent_common


def test_verified_outcome_banner_says_not_executed_on_blocked_attempt():
    # The exact bug class DESIGN.md's "Narration vs verified outcome" section
    # describes: the model claims success in final_text even though the
    # tool result it received showed the call was blocked. The banner must
    # say NOT EXECUTED regardless of what final_text itself claims.
    policy_write_attempts = [{
        "policy_id": "no-public-ip-ec2",
        "target_account": "prod-core",
        "executed": False,
        "result": {"acknowledged": True, "workaround": "use SSM instead"},
    }]
    final_text = "The policy has been successfully updated. You can now proceed."

    result = agent_common._prepend_verified_outcome(final_text, policy_write_attempts)

    assert "NOT EXECUTED" in result
    assert "Disregard any claim of success" in result
    assert result.endswith(final_text)


def test_verified_outcome_banner_says_executed_on_real_success():
    policy_write_attempts = [{
        "policy_id": "max-ebs-volume-size-dev",
        "target_account": "dev-sandbox",
        "executed": True,
        "result": {"executed": True, "via": "broker"},
    }]
    final_text = "The override was applied successfully."

    result = agent_common._prepend_verified_outcome(final_text, policy_write_attempts)

    assert "EXECUTED" in result
    assert "NOT EXECUTED" not in result
    assert result.endswith(final_text)


def test_verified_outcome_banner_absent_when_no_write_attempts():
    final_text = "Escalated to human approval, no policy change attempted."
    result = agent_common._prepend_verified_outcome(final_text, [])
    assert result == final_text


def test_verified_outcome_banner_uses_last_attempt_when_multiple():
    policy_write_attempts = [
        {"policy_id": "p1", "target_account": "a1", "executed": False, "result": {}},
        {"policy_id": "p2", "target_account": "a2", "executed": True, "result": {"executed": True}},
    ]
    result = agent_common._prepend_verified_outcome("final text", policy_write_attempts)
    assert "EXECUTED" in result
    assert "p2" in result
    assert "a2" in result
