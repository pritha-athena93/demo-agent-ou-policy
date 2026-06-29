from policy_registry import check_policy_registry, is_known_policy_id


def test_known_protected_policy():
    result = check_policy_registry("no-public-ip-ec2")
    assert result["found"] is True
    assert result["protected"] is True


def test_known_unprotected_policy():
    result = check_policy_registry("max-ebs-volume-size-dev")
    assert result["found"] is True
    assert result["protected"] is False


def test_unknown_policy_fails_closed():
    result = check_policy_registry("totally-made-up-policy-id")
    assert result["found"] is False
    assert result["protected"] is True  # fail closed, not open
    assert is_known_policy_id("totally-made-up-policy-id") is False


def test_protected_in_scopes_to_specific_account():
    protected_in_prod = check_policy_registry("allow-large-instance-types", "prod-core")
    assert protected_in_prod["protected"] is True

    open_elsewhere = check_policy_registry("allow-large-instance-types", "dev-sandbox")
    assert open_elsewhere["protected"] is False

    # base default with no target_account given should fall back to the
    # policy's own unscoped `protected` flag, not the account-scoped one
    no_account_given = check_policy_registry("allow-large-instance-types")
    assert no_account_given["protected"] is False


def test_is_known_policy_id():
    assert is_known_policy_id("no-public-ip-ec2") is True
    assert is_known_policy_id("not-a-real-policy") is False
