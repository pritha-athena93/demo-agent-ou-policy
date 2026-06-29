"""Shared tool-calling loop. Both variants drive the same loop against the
same model family with the same prompt/tools -- only the messages.create
call site and what happens around update_org_policy differ.
"""

from tools import SYSTEM_PROMPT, TOOL_SCHEMAS, validate_tool_input
from broker import BrokerDenied


def run_agent_loop(client, model_id, mandate_text, dispatch, variant_name, account, before_tool=None, max_turns=12):
    """Generic tool-use loop. before_tool(tool_name, tool_input, last_reasoning_text)
    may return a dict to substitute for the real tool result (i.e. a block);
    returning None means let the dispatch call run normally."""
    messages = [{"role": "user", "content": mandate_text}]
    last_reasoning_text = ""
    last_tool_call = None

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
            return {"final_text": last_reasoning_text, "messages": messages, "hit_turn_limit": False}

        tool_results = []
        for tool_use in tool_uses:
            # Structural/allowlist validation runs before before_tool and
            # dispatch, on every variant. The model's tool-call arguments may
            # already be attacker-influenced (a prompt-injected mandate), so
            # they get the same skepticism the broker applies to writes --
            # just earlier and broader, so an invalid call never even reaches
            # (and never gets logged as a spurious attempt by) the broker or
            # guardrail layer.
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

    # Falling out of the loop (rather than returning early above) only
    # happens by exhausting max_turns -- the agent never reached a stopping
    # point on its own, so this needs a human to pick up where it left off.
    return {"final_text": last_reasoning_text, "messages": messages, "hit_turn_limit": True}
