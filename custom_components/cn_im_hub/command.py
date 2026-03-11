"""Natural language parser and executor for IM providers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .conversation import ask_home_assistant
from .models import Command


def parse_command(text: str) -> Command | None:
    """Parse inbound text; only natural language conversation is supported."""
    text = text.strip()
    if not text:
        return None
    return Command(kind="conversation", target=text, payload={})


async def execute_command(
    hass: HomeAssistant,
    command: Command,
    *,
    conversation_id: str,
    agent_id: str | None,
) -> str:
    """Execute parsed command against Home Assistant."""
    if command.kind == "conversation":
        return await ask_home_assistant(
            hass,
            command.target,
            conversation_id=conversation_id,
            agent_id=agent_id,
        )

    return "当前仅支持自然语言对话。"
