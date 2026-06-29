"""Adapter so agent_common.run_agent_loop can drive any Bedrock model via the
Converse API, using the same call shape as anthropic.Anthropic().messages.create().
Converse normalizes tool-use across providers, so swapping model_id (open-source
now, Claude later) needs no changes to the agent loop or tool schemas.
"""

import json
import os
from types import SimpleNamespace

# Optional, for cutting down phrasing/path variance on demo takes -- not a
# substitute for the deterministic checks elsewhere in this repo, just turns
# the dial down on which of several valid next steps the model picks. Unset
# by default (uses the model's normal sampling); read fresh on every call
# rather than cached at import time, so a test can flip it mid-process.
_AGENT_TEMPERATURE_ENV = "AGENT_TEMPERATURE"


def _anthropic_tools_to_converse(tool_schemas):
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": {"json": t["input_schema"]},
                }
            }
            for t in tool_schemas
        ]
    }


def _block_to_converse(block):
    if isinstance(block, dict) and block.get("type") == "tool_result":
        return {
            "toolResult": {
                "toolUseId": block["tool_use_id"],
                "content": [{"text": str(block["content"])}],
            }
        }
    if isinstance(block, SimpleNamespace):
        if block.type == "text":
            return {"text": block.text}
        if block.type == "tool_use":
            return {"toolUse": {"toolUseId": block.id, "name": block.name, "input": block.input}}
    raise ValueError(f"unhandled content block: {block!r}")


def _messages_to_converse(messages):
    out = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            out.append({"role": msg["role"], "content": [{"text": content}]})
        else:
            out.append({"role": msg["role"], "content": [_block_to_converse(b) for b in content]})
    return out


def _converse_content_to_blocks(content):
    blocks = []
    for c in content:
        if "text" in c:
            blocks.append(SimpleNamespace(type="text", text=c["text"]))
        elif "toolUse" in c:
            tu = c["toolUse"]
            blocks.append(SimpleNamespace(type="tool_use", id=tu["toolUseId"], name=tu["name"], input=tu["input"]))
    return blocks


class _Messages:
    def __init__(self, runtime, model_id):
        self._runtime = runtime
        self._model_id = model_id

    def create(self, model, max_tokens, system, tools, messages):
        inference_config = {"maxTokens": max_tokens}
        temperature = os.environ.get(_AGENT_TEMPERATURE_ENV)
        if temperature:
            inference_config["temperature"] = float(temperature)

        resp = self._runtime.converse(
            modelId=model,
            messages=_messages_to_converse(messages),
            system=[{"text": system}],
            toolConfig=_anthropic_tools_to_converse(tools),
            inferenceConfig=inference_config,
        )
        content = resp["output"]["message"]["content"]
        return SimpleNamespace(content=_converse_content_to_blocks(content), stop_reason=resp.get("stopReason"))


class BedrockMessagesClient:
    def __init__(self, runtime, model_id):
        self.messages = _Messages(runtime, model_id)
