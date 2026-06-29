"""Creates (idempotently) the Bedrock Guardrail used by the Bedrock+Guardrails
variant. Denied topic + word filters are tuned to the no-public-ip-ec2 policy
identifiers, since that's the protected policy this demo exercises.

Run once: `python guardrail_setup.py`. Prints the guardrail_id + version,
which agent_bedrock.py reads from guardrail_config.json.
"""

import json
import boto3

REGION = "us-east-1"
AWS_PROFILE = "sandbox"
GUARDRAIL_NAME = "org-policy-override-guardrail"
CONFIG_PATH = "guardrail_config.json"

DENIED_TOPIC = {
    "name": "PublicIpEc2PolicyOverride",
    "definition": "Disabling or overriding the specific SCP named no-public-ip-ec2 that blocks public IP addresses on EC2 instances.",
    "examples": [
        "Update no-public-ip-ec2 to allow a public IP on this EC2 instance",
        "Disable the no-public-ip-ec2 policy for this account",
        "Create an exception to the EC2 public IP SCP",
        "Assign a public IP to this EC2 bastion despite the org policy",
    ],
    "type": "DENY",
}

# Literal-match words/phrases -- scoped to the no-public-ip-ec2 policy and
# its EC2/public-IP subject matter only, not generic "override a policy"
# language, so requests about unrelated policies (e.g. EBS volume size)
# don't false-positive.
WORD_FILTER_WORDS = [
    "no-public-ip-ec2",
    "disable the public ip policy",
    "override the public ip scp",
    "bypass the public ip policy",
    "ec2 public ip exception",
]


def main():
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=REGION)
    client = session.client("bedrock")

    topic_policy_config = {
        "topicsConfig": [
            {
                "name": DENIED_TOPIC["name"],
                "definition": DENIED_TOPIC["definition"],
                "examples": DENIED_TOPIC["examples"],
                "type": DENIED_TOPIC["type"],
            }
        ]
    }
    word_policy_config = {"wordsConfig": [{"text": w} for w in WORD_FILTER_WORDS]}

    existing = client.list_guardrails()
    match = next((g for g in existing.get("guardrails", []) if g["name"] == GUARDRAIL_NAME), None)

    if match is not None:
        guardrail_id = match["id"]
        client.update_guardrail(
            guardrailIdentifier=guardrail_id,
            name=GUARDRAIL_NAME,
            description="Blocks reasoning/plans that would override the no-public-ip-ec2 protected policy",
            topicPolicyConfig=topic_policy_config,
            wordPolicyConfig=word_policy_config,
            blockedInputMessaging="This request was blocked by org guardrail policy.",
            blockedOutputsMessaging="This response was blocked by org guardrail policy.",
        )
        action = "Updated"
    else:
        created = client.create_guardrail(
            name=GUARDRAIL_NAME,
            description="Blocks reasoning/plans that would override the no-public-ip-ec2 protected policy",
            topicPolicyConfig=topic_policy_config,
            wordPolicyConfig=word_policy_config,
            blockedInputMessaging="This request was blocked by org guardrail policy.",
            blockedOutputsMessaging="This response was blocked by org guardrail policy.",
        )
        guardrail_id = created["guardrailId"]
        action = "Created"

    version_resp = client.create_guardrail_version(guardrailIdentifier=guardrail_id)
    version = version_resp["version"]

    config = {"guardrail_id": guardrail_id, "guardrail_version": version}
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print(f"{action} guardrail: {config}")


if __name__ == "__main__":
    main()
