"""Shared tool-calling loop. Both variants drive the same loop against the
same model family with the same prompt/tools -- only the messages.create
call site and what happens around update_org_policy differ.
"""

from tools import SYSTEM_PROMPT, TOOL_SCHEMAS, validate_tool_input
from broker import BrokerDenied
import logger


def run_agent_loop(client, model_id, mandate_text, dispatch, variant_name, account, before_tool=None,
                    max_turns=12, allowed_tools=None):
    """Generic tool-use loop. before_tool(tool_name, tool_input, last_reasoning_text)
    may return a dict to substitute for the real tool result (i.e. a block);
    returning None means let the dispatch call run normally.

    allowed_tools, if given, is a coarse code-level allowlist checked before
    validate_tool_input/before_tool/dispatch -- e.g. the GitHub-issue round-1
    assessment step must not be able to call update_org_policy or
    request_human_approval no matter what the model decides, regardless of
    what the round-1 mandate text says. Same "the prompt doesn't enforce,
    code does" principle as everything else in this file, just applied to
    which tools are reachable at all rather than whether a specific call is
    allowed."""
    messages = [{"role": "user", "content": mandate_text}]
    last_reasoning_text = ""
    last_tool_call = None
    policy_write_attempts = []

    for _ in range(max_turns):
        response = client.messages.create(
            model=model_id,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        text_parts = [b.text for b in response.content if b.type == "text"]
        if text_parts:
            last_reasoning_text = "\n".join(text_parts)

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": response.content})

        if not tool_uses:
            final_text = _prepend_verified_outcome(last_reasoning_text, policy_write_attempts)
            return {"final_text": final_text, "messages": messages, "hit_turn_limit": False,
                    "policy_write_attempts": policy_write_attempts}

        tool_results = []
        for tool_use in tool_uses:
            # account is already known with certainty -- the caller parsed it
            # deterministically (the GitHub issue's "Account" field, or
            # run_scenario.py's ACCOUNTS map) before the model ever ran. There
            # is never a legitimate reason for a tool call's target_account to
            # be anything else, so correct it here, before validate_tool_input
            # even runs -- otherwise a wrong value (e.g. "org-wide", a typo)
            # gets rejected by validation before this correction would get a
            # chance to fix it, and the request falls through to human
            # escalation for no real reason. The model's belief about which
            # account it's targeting doesn't matter; only the known fact does.
            if "target_account" in tool_use.input:
                model_value = tool_use.input["target_account"]
                if model_value != account:
                    logger.log(variant_name, account, f"{tool_use.name}(target_account)", "INFO",
                               f"corrected target_account from model's '{model_value}' to '{account}'")
                    tool_use.input["target_account"] = account

            # Structural/allowlist validation runs before before_tool and
            # dispatch, on every variant. The model's tool-call arguments may
            # already be attacker-influenced (a prompt-injected mandate), so
            # they get the same skepticism the broker applies to writes --
            # just earlier and broader, so an invalid call never even reaches
            # (and never gets logged as a spurious attempt by) the broker or
            # guardrail layer.
            if allowed_tools is not None and tool_use.name not in allowed_tools:
                result = {"error": f"tool '{tool_use.name}' is not permitted in this round"}
            else:
                validation_error = validate_tool_input(tool_use.name, tool_use.input)
                if validation_error is not None:
                    result = {"error": validation_error}
                else:
                    result = None
                    if before_tool is not None:
                        result = before_tool(tool_use.name, tool_use.input, last_reasoning_text)
                    if result is None:
                        try:
                            result = dispatch[tool_use.name](**tool_use.input)
                        except BrokerDenied as e:
                            # Real AWS lookups (account/policy name resolution,
                            # SCP statement lookup) can fail closed here too, not
                            # just the protected-policy check -- surface it to the
                            # model as a denial it can react to (e.g. escalate to
                            # request_human_approval) instead of crashing the run.
                            result = {"error": f"denied: {e}"}
            last_tool_call = (tool_use.name, tool_use.input, result)
            if tool_use.name == "update_org_policy":
                # The ground truth for whether a write actually happened is
                # the tool result's own `executed` flag (every code path that
                # can produce a result for this tool -- raw, brokered,
                # validation error, broker denial, before_tool's
                # propose_workaround substitution -- sets it, see tools.py).
                # The model's prose is not consulted here on purpose: it has
                # been observed narrating success after a blocked call, so
                # nothing about "what actually happened" can depend on what
                # it says next turn.
                executed = isinstance(result, dict) and result.get("executed") is True
                policy_write_attempts.append({
                    "policy_id": tool_use.input.get("policy_id"),
                    "target_account": tool_use.input.get("target_account"),
                    "executed": executed,
                    "result": result,
                })
            tool_results.append(
                {"type": "tool_result", "tool_use_id": tool_use.id, "content": str(result)}
            )

        messages.append({"role": "user", "content": tool_results})

    if not last_reasoning_text and last_tool_call is not None:
        name, tool_input, result = last_tool_call
        last_reasoning_text = (
            f"(Agent hit the {max_turns}-turn limit without a final summary. "
            f"Last tool called: {name}({tool_input}) -> {result})"
        )

    last_reasoning_text = _prepend_verified_outcome(last_reasoning_text, policy_write_attempts)

    # Falling out of the loop (rather than returning early above) only
    # happens by exhausting max_turns -- the agent never reached a stopping
    # point on its own, so this needs a human to pick up where it left off.
    return {"final_text": last_reasoning_text, "messages": messages, "hit_turn_limit": True,
            "policy_write_attempts": policy_write_attempts}


def _prepend_verified_outcome(final_text: str, policy_write_attempts: list) -> str:
    """Prepends a code-derived, non-negotiable statement of what the last
    update_org_policy attempt actually did, ahead of the model's own prose.

    This exists because the model has been observed claiming a change
    succeeded in its final summary even when the tool result it just
    received showed the call was blocked (see DESIGN.md's "Narration vs
    verified outcome" section). Detecting that contradiction by pattern-
    matching the model's success-claiming language would itself be the kind
    of fragile, model-output-trusting check this whole project argues
    against -- so instead of trying to catch the lie, this just always
    states the verified fact first, from the tool result's own `executed`
    flag, regardless of what the model says afterward.
    """
    if not policy_write_attempts:
        return final_text

    last = policy_write_attempts[-1]
    if last["executed"]:
        banner = (
            f"**Verified outcome (from the tool's own result, not the model's narration): "
            f"EXECUTED.** `update_org_policy` for `{last['policy_id']}` in "
            f"`{last['target_account']}` ran and returned `executed: True`."
        )
    else:
        banner = (
            f"**Verified outcome (from the tool's own result, not the model's narration): "
            f"NOT EXECUTED.** The last `update_org_policy` attempt for `{last['policy_id']}` "
            f"in `{last['target_account']}` did not run (result: {last['result']}). "
            f"Disregard any claim of success below -- it did not happen."
        )
    return f"{banner}\n\n{final_text}"
