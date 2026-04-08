"""
Anthropic Claude API wrapper.

Reads ANTHROPIC_API_KEY from:
  1. Environment variable (local .env via python-dotenv)
  2. st.secrets["ANTHROPIC_API_KEY"] (Streamlit Cloud deployment)

Usage:
    client = ClaudeClient()
    text = client.ask(system="...", user="...")        # full response
    for chunk in client.stream(system="...", user="..."): # streaming
        print(chunk, end="", flush=True)
"""

import os
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")


def _get_api_key() -> str:
    """Resolve API key from env or Streamlit secrets (cloud)."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        # Try Streamlit secrets (only available when running under Streamlit)
        try:
            import streamlit as st  # noqa: PLC0415
            key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:
            pass
    if not key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. "
            "Add it to your .env file or Streamlit Cloud secrets."
        )
    return key


class ClaudeClient:
    """Thin wrapper around the Anthropic Messages API."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._client: Optional[anthropic.Anthropic] = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=_get_api_key())
        return self._client

    def ask(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
    ) -> str:
        """
        Send a single-turn message and return the full response text.
        Suitable for report generation and dataset summarisation.
        """
        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text

    def stream(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
    ):
        """
        Stream a response as text delta chunks.

        Compatible with Streamlit's st.write_stream():
            st.write_stream(claude.stream(system, user))

        Also works for plain Python:
            for chunk in claude.stream(system, user):
                print(chunk, end="", flush=True)
        """
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            for text in stream.text_stream:
                yield text

    def stream_with_history(
        self,
        system: str,
        messages: list,
        max_tokens: int = 2048,
    ):
        """
        Multi-turn streaming chat.
        messages: [{"role": "user"|"assistant", "content": "..."}]
        Compatible with st.write_stream().
        """
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
