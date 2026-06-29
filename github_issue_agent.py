"""Parses a GitHub issue opened from the policy-block issue form, runs the
Bedrock+Guardrails agent against it, and writes a comment body to a file for
the workflow to post back. This is the only variant wired to GitHub issues --
see GITHUB_INTEGRATION.md for why claude-direct is deliberately not exposed
to arbitrary issue input.

Reads the issue body from the ISSUE_BODY env var (set by the workflow from
github.event.issue.body), not a CLI arg, to avoid shell-injection surface
from issue content.
"""

import os
import re

import agent_bedrock
import logger

VALID_ACCOUNTS = {"dev-sandbox", "dev-shared", "prod-core"}
COMMENT_OUTPUT_PATH = "issue_comment.md"


def parse_issue_form(body: str) -> dict:
    """GitHub issue forms render as '### <label>\\n\\n<value>\\n\\n...'."""
    sections = re.split(r"^###\s+", body, flags=re.MULTILINE)
    fields = {}
    for section in sections[1:]:
        label, _, rest = section.partition("\n")
        value = rest.strip()
        fields[label.strip()] = value
    return fields


def build_mandate(fields: dict) -> str:
    blocked = fields.get("What is blocked?", "").strip()
    goal = fields.get("What are you trying to do?", "").strip()
    policy_hint = fields.get("Policy ID (if known)", "").strip()

    mandate = f"{goal} {blocked} is currently blocked."
    if policy_hint and policy_hint.lower() not in ("_no response_", ""):
        mandate += f" The policy involved may be {policy_hint}."
    mandate += (
        " This is an async request with no one available to answer follow-up "
        "questions. Decide and act using the tools available -- do not ask "
        "clarifying questions or offer to help further. End with a definitive "
        "outcome: either the change is made, it's blocked with a workaround, "
        "or it's routed to human approval."
    )
    return mandate.strip()


def summarize(captured_lines: list, final_text: str, hit_turn_limit: bool) -> str:
    if hit_turn_limit:
        return (
            "**Outcome: human intervention required.** The agent hit its turn "
            "limit without reaching a final answer -- it did not resolve this, "
            "block it, or route it to approval on its own. Labeled `HITL`; "
            "please review the trace below and take it from here."
        )
    if any("BLOCKED" in line for line in captured_lines):
        header = "**Outcome: blocked.** The requested change touches a protected policy and was not applied. See workaround below."
    elif any("ALLOWED-CHECK-PASSED" in line for line in captured_lines):
        header = "**Outcome: temporary override granted.** Checks passed; a scoped, time-limited exception was issued (see trace for expiry)."
    elif any("pending-approval" in line.lower() for line in captured_lines) or "pending-approval" in final_text.lower():
        header = "**Outcome: routed to human approval.** No automatic change was made."
    else:
        header = "**Outcome: no policy change attempted.** See agent's response below."
    return header


def _set_workflow_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return  # running locally, outside a workflow -- nothing to write to
    with open(output_path, "a") as f:
        f.write(f"{name}={value}\n")


def main():
    issue_body = os.environ["ISSUE_BODY"]
    fields = parse_issue_form(issue_body)

    account = fields.get("Account", "").strip()
    if account not in VALID_ACCOUNTS:
        comment = (
            f"Could not run the agent: account `{account}` is not one of "
            f"{sorted(VALID_ACCOUNTS)}. Please edit the issue and select a valid account."
        )
        with open(COMMENT_OUTPUT_PATH, "w") as f:
            f.write(comment)
        return

    mandate = build_mandate(fields)

    logger.begin_capture()
    result = agent_bedrock.run(mandate, account)
    captured_lines = logger.end_capture()

    final_text = result.get("final_text", "")
    hit_turn_limit = result.get("hit_turn_limit", False)
    header = summarize(captured_lines, final_text, hit_turn_limit)
    _set_workflow_output("needs_hitl", "true" if hit_turn_limit else "false")

    trace_block = "\n".join(captured_lines) if captured_lines else "(no policy-write attempts logged)"

    comment = (
        f"{header}\n\n"
        f"---\n\n"
        f"**Agent response:**\n\n{final_text}\n\n"
        f"---\n\n"
        f"<details><summary>Trace log</summary>\n\n```\n{trace_block}\n```\n</details>\n\n"
        f"_Run by the Bedrock+Guardrails agent variant. Policy/account checks are enforced in code, "
        f"not by this comment or the request's wording._"
    )

    with open(COMMENT_OUTPUT_PATH, "w") as f:
        f.write(comment)


if __name__ == "__main__":
    main()
