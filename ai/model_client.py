"""
Unified AI model client — supports Anthropic Claude, OpenAI GPT, and Google Gemini.
Exports: ModelClient, PROVIDERS, BEST_MODELS, DEFAULT_PROVIDER, DEFAULT_MODEL, fetch_available_models

Exposes the same interface as ClaudeClient so the rest of the app doesn't change:
    client.ask(system, user) → str
    client.stream(system, user) → generator of str chunks
    client.stream_with_history(system, messages) → generator of str chunks

Provider libraries are imported lazily so missing packages only raise errors
when that provider is actually used.
"""

import time

# ---------------------------------------------------------------------------
# Provider catalogue — shown in the settings UI
# ---------------------------------------------------------------------------
PROVIDERS = {
    "Anthropic (Claude)": {
        # Best model shown by default; rest available under "More models"
        "best":   "claude-opus-4-7",
        "models": [
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ],
        "env_key": "ANTHROPIC_API_KEY",
        "package": "anthropic",
        "docs_url": "https://console.anthropic.com/",
    },
    "OpenAI (GPT)": {
        "best":   "gpt-4o",
        "models": [
            "gpt-4o",
            "gpt-4o-mini",
        ],
        "env_key": "OPENAI_API_KEY",
        "package": "openai",
        "docs_url": "https://platform.openai.com/api-keys",
    },
    "Google (Gemini)": {
        "best":   "gemini-2.0-flash",
        "models": [
            "gemini-2.0-flash",
            "gemini-1.5-pro",
        ],
        "env_key": "GOOGLE_API_KEY",
        "package": "google-generativeai",
        "docs_url": "https://aistudio.google.com/app/apikey",
    },
}

# The three best models — one per provider — shown by default
BEST_MODELS = [
    ("Anthropic (Claude)", "claude-opus-4-7"),
    ("OpenAI (GPT)",        "gpt-4o"),
    ("Google (Gemini)",     "gemini-2.0-flash"),
]

DEFAULT_PROVIDER = "Anthropic (Claude)"
DEFAULT_MODEL    = "claude-opus-4-7"


def fetch_available_models(provider, api_key):
    """
    Try to fetch the live model list from the provider API.
    Falls back to the hardcoded list in PROVIDERS if the call fails or
    the library is not installed.
    """
    fallback = PROVIDERS[provider]["models"]
    if not api_key:
        return fallback
    try:
        if provider == "Anthropic (Claude)":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            data = client.models.list(limit=50)
            ids = [m.id for m in data.data]
            return ids if ids else fallback

        if provider == "OpenAI (GPT)":
            import openai
            client = openai.OpenAI(api_key=api_key)
            data = client.models.list()
            # Keep only chat/GPT models, newest first
            ids = sorted(
                [m.id for m in data.data if m.id.startswith(("gpt-4", "gpt-3.5", "o1", "o3"))],
                reverse=True,
            )
            return ids[:15] if ids else fallback

        if provider == "Google (Gemini)":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            ids = [
                m.name.replace("models/", "")
                for m in genai.list_models()
                if "generateContent" in (m.supported_generation_methods or [])
            ]
            return ids if ids else fallback

    except Exception:
        pass
    return fallback


class ModelClient:
    """
    Provider-agnostic AI client.  Instantiate with a provider name, model name,
    and API key — then call ask() / stream() / stream_with_history() exactly as
    you would ClaudeClient.
    """

    def __init__(self, provider: str, model: str, api_key: str):
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider '{provider}'. Choose from: {list(PROVIDERS)}")
        self.provider = provider
        self.model    = model
        self.api_key  = api_key.strip()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def ask(self, system: str, user: str, max_tokens: int = 2048) -> str:
        """Send a single-turn message and return the full response text."""
        if self.provider == "Anthropic (Claude)":
            return self._anthropic_ask(system, user, max_tokens)
        if self.provider == "OpenAI (GPT)":
            return self._openai_ask(system, user, max_tokens)
        if self.provider == "Google (Gemini)":
            return self._gemini_ask(system, user, max_tokens)
        raise ValueError(f"Unsupported provider: {self.provider}")

    def stream(self, system: str, user: str, max_tokens: int = 2048):
        """Stream response as text chunks (compatible with st.write_stream)."""
        if self.provider == "Anthropic (Claude)":
            yield from self._anthropic_stream(system, user, max_tokens)
        elif self.provider == "OpenAI (GPT)":
            yield from self._openai_stream(system, user, max_tokens)
        elif self.provider == "Google (Gemini)":
            yield from self._gemini_stream(system, user, max_tokens)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def stream_with_history(self, system: str, messages: list, max_tokens: int = 2048):
        """Multi-turn streaming chat. messages is a list of {role, content} dicts."""
        if self.provider == "Anthropic (Claude)":
            yield from self._anthropic_stream_history(system, messages, max_tokens)
        elif self.provider == "OpenAI (GPT)":
            yield from self._openai_stream_history(system, messages, max_tokens)
        elif self.provider == "Google (Gemini)":
            yield from self._gemini_stream_history(system, messages, max_tokens)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------

    def _anthropic_client(self):
        try:
            import anthropic
        except ImportError:
            raise ImportError("Install the 'anthropic' package: pip install anthropic")
        return anthropic.Anthropic(api_key=self.api_key)

    def _anthropic_ask(self, system, user, max_tokens):
        import anthropic
        client = self._anthropic_client()
        last_err = None
        for attempt in range(3):
            try:
                msg = client.messages.create(
                    model=self.model, max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                return msg.content[0].text
            except anthropic.APIStatusError as e:
                last_err = e
                if e.status_code in (429, 529) and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise
        raise last_err

    def _anthropic_stream(self, system, user, max_tokens):
        client = self._anthropic_client()
        with client.messages.stream(
            model=self.model, max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as s:
            yield from s.text_stream

    def _anthropic_stream_history(self, system, messages, max_tokens):
        import anthropic
        client = self._anthropic_client()
        for attempt in range(3):
            try:
                with client.messages.stream(
                    model=self.model, max_tokens=max_tokens,
                    system=system, messages=messages,
                ) as s:
                    yield from s.text_stream
                return
            except anthropic.APIStatusError as e:
                if e.status_code in (429, 529) and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    def _openai_client(self):
        try:
            import openai
        except ImportError:
            raise ImportError("Install the 'openai' package: pip install openai")
        return openai.OpenAI(api_key=self.api_key)

    def _openai_ask(self, system, user, max_tokens):
        client = self._openai_client()
        resp = client.chat.completions.create(
            model=self.model, max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    def _openai_stream(self, system, user, max_tokens):
        client = self._openai_client()
        stream = client.chat.completions.create(
            model=self.model, max_tokens=max_tokens, stream=True,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def _openai_stream_history(self, system, messages, max_tokens):
        client = self._openai_client()
        # Prepend system message
        full_messages = [{"role": "system", "content": system}] + messages
        stream = client.chat.completions.create(
            model=self.model, max_tokens=max_tokens, stream=True,
            messages=full_messages,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ------------------------------------------------------------------
    # Google Gemini
    # ------------------------------------------------------------------

    def _gemini_ask(self, system, user, max_tokens):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("Install: pip install google-generativeai")
        genai.configure(api_key=self.api_key)
        gm = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system,
            generation_config={"max_output_tokens": max_tokens},
        )
        resp = gm.generate_content(user)
        return resp.text or ""

    def _gemini_stream(self, system, user, max_tokens):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("Install: pip install google-generativeai")
        genai.configure(api_key=self.api_key)
        gm = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system,
            generation_config={"max_output_tokens": max_tokens},
        )
        for chunk in gm.generate_content(user, stream=True):
            if chunk.text:
                yield chunk.text

    def _gemini_stream_history(self, system, messages, max_tokens):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("Install: pip install google-generativeai")
        genai.configure(api_key=self.api_key)
        gm = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=system,
            generation_config={"max_output_tokens": max_tokens},
        )
        # Convert message history (skip last user message — passed separately)
        history = []
        for msg in messages[:-1]:
            role = "model" if msg["role"] == "assistant" else "user"
            history.append({"role": role, "parts": [msg["content"]]})
        last_user = messages[-1]["content"] if messages else ""
        chat = gm.start_chat(history=history)
        for chunk in chat.send_message(last_user, stream=True):
            if chunk.text:
                yield chunk.text
