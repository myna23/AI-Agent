"""
Unified AI model client — supports OpenAI GPT, Google Gemini, and WB mAI Factory.
Exports: ModelClient, PROVIDERS, BEST_MODELS, DEFAULT_PROVIDER, DEFAULT_MODEL, fetch_available_models

Interface:
    client.ask(system, user) → str
    client.stream(system, user) → generator of str chunks
    client.stream_with_history(system, messages) → generator of str chunks

Provider libraries are imported lazily so missing packages only raise errors
when that provider is actually used.
"""

# ---------------------------------------------------------------------------
# Provider catalogue — shown in the settings UI
# ---------------------------------------------------------------------------
PROVIDERS = {
    # ── World Bank Desktop (DesktopToken auth) — GPT via mAI Factory ────
    # For local demos on a WB office machine. Authenticates via itsai SDK.
    # Store the mAI Factory endpoint in WB_AZURE_ENDPOINT in your .env file.
    "WB Desktop (GPT)": {
        "best":       "gpt-5",
        "models":     ["gpt-5", "gpt-4o", "gpt-4o-mini"],
        "env_key":    "WB_AZURE_ENDPOINT",
        "package":    "azure-openai",
        "docs_url":   "https://ai.worldbank.org/",
        "api_version": "2025-04-01-preview",
        "mai_base":   "https://azapimdev.worldbank.org/conversationalai/v2/",
    },
    # ── World Bank Desktop (DesktopToken auth) — Claude via mAI Bedrock ─
    # Same DesktopToken auth as GPT, but routes to Bedrock Claude endpoint.
    "WB Desktop (Claude)": {
        "best":    "us.anthropic.claude-sonnet-4-6",
        "models":  [
            "us.anthropic.claude-sonnet-4-6",
            "us.anthropic.claude-haiku-4-5",
            "us.anthropic.claude-opus-4-5",
        ],
        "env_key":  "WB_AZURE_ENDPOINT",
        "package":  "bedrock-claude",
        "docs_url": "https://ai.worldbank.org/",
        "bedrock_base": "https://azapimdev.worldbank.org/conversationalai/bedrock/model/",
    },
    # ── World Bank Posit Connect (azure.identity — AZURE_CLIENT_ID set by mAI integration) ─
    # Used when the app is deployed on Posit Connect with the mAI Factory DEV integration.
    # The integration injects AZURE_CLIENT_ID + AZURE_TENANT_ID; no manual token needed.
    "WB Posit (GPT)": {
        "best":       "gpt-4o",
        "models":     ["gpt-4o", "gpt-4o-mini"],
        "env_key":    "AZURE_CLIENT_ID",
        "package":    "azure-openai",
        "api_version": "2025-04-01-preview",
        "mai_base":   "https://azapimdev.worldbank.org/conversationalai/v2/",
    },
    "WB Posit (Claude)": {
        "best":    "us.anthropic.claude-sonnet-4-6",
        "models":  [
            "us.anthropic.claude-sonnet-4-6",
            "us.anthropic.claude-haiku-4-5",
        ],
        "env_key":  "AZURE_CLIENT_ID",
        "package":  "bedrock-claude",
        "bedrock_base": "https://azapimdev.worldbank.org/conversationalai/bedrock/model/",
    },
    # ── World Bank mAI Factory (primary for WB deployment) ──────────────
    # Single token from mAI Factory replaces all individual provider keys.
    # Routes to GPT and Gemini through the WB internal gateway.
    # Set MAI_FACTORY_TOKEN + MAI_FACTORY_BASE_URL in .env / Posit Connect.
    "WB mAI Factory (GPT)": {
        "best":    "gpt-4o",
        "models":  ["gpt-4o", "gpt-4o-mini"],
        "env_key": "MAI_FACTORY_TOKEN",
        "package": "openai",
        "docs_url": "https://ai.worldbank.org/platform/documentation",
        "base_url_env": "MAI_FACTORY_BASE_URL",
    },
    # ── Direct provider keys (fallback / local dev) ──────────────────────
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

# Default provider: desktop → posit → mAI Factory token → plain OpenAI
import os as _os
_wb_azure  = _os.getenv("WB_AZURE_ENDPOINT", "")
_on_posit  = _os.getenv("CONNECT_SERVER", "")   # Posit Connect always sets this
_mai_token = _os.getenv("MAI_FACTORY_TOKEN", "")
if _wb_azure:
    DEFAULT_PROVIDER = "WB Desktop (GPT)"
    DEFAULT_MODEL    = "gpt-5"
elif _on_posit:
    DEFAULT_PROVIDER = "WB Posit (GPT)"
    DEFAULT_MODEL    = "gpt-4o"
elif _mai_token:
    DEFAULT_PROVIDER = "WB mAI Factory (GPT)"
    DEFAULT_MODEL    = "gpt-4o"
else:
    DEFAULT_PROVIDER = "OpenAI (GPT)"
    DEFAULT_MODEL    = "gpt-4o"

# Best models shown in the quick selector (one per meaningful provider)
BEST_MODELS = [
    ("WB Desktop (GPT)",     "gpt-5"),
    ("WB Desktop (Claude)",  "us.anthropic.claude-sonnet-4-6"),
    ("WB Posit (GPT)",       "gpt-4o"),
    ("WB Posit (Claude)",    "us.anthropic.claude-sonnet-4-6"),
    ("WB mAI Factory (GPT)", "gpt-4o"),
    ("OpenAI (GPT)",         "gpt-4o"),
    ("Google (Gemini)",      "gemini-2.0-flash"),
]


def fetch_available_models(provider: str, api_key: str) -> list:
    """
    Fetch the live model list from the provider API so new models
    New models appear automatically without code changes.
    Falls back to the hardcoded list in PROVIDERS on any error.
    """
    fallback = PROVIDERS.get(provider, {}).get("models", [])
    if not api_key:
        return fallback

    pinfo    = PROVIDERS.get(provider, {})
    base_url = _os.getenv(pinfo.get("base_url_env", ""), "").strip() or None

    try:
        # ── WB mAI Factory (GPT) or direct OpenAI ────────────────────
        if pinfo.get("package") == "openai":
            import openai
            if base_url:
                # Azure-style endpoint (mAI Factory / Azure OpenAI)
                client = openai.AzureOpenAI(
                    api_key=api_key,
                    azure_endpoint=base_url,
                    api_version="2024-12-01-preview",
                )
            else:
                client = openai.OpenAI(api_key=api_key)
            data = client.models.list()
            ids  = sorted(
                [m.id for m in data.data
                 if m.id.startswith(("gpt-4", "gpt-3.5", "gpt-5", "o1", "o3", "o4"))],
                reverse=True,
            )
            return ids[:20] if ids else fallback

        # ── Google Gemini ─────────────────────────────────────────────
        if pinfo.get("package") == "google-generativeai":
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
    you would any AI client.
    """

    def __init__(self, provider: str, model: str, api_key: str):
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider '{provider}'. Choose from: {list(PROVIDERS)}")
        self.provider = provider
        self.model    = model
        self.api_key  = api_key.strip()
        # Pick up optional base URL for mAI Factory / Azure routing
        pinfo = PROVIDERS[provider]
        base_url_env = pinfo.get("base_url_env", "")
        self.base_url = _os.getenv(base_url_env, "").strip() if base_url_env else ""

    def _is_openai(self):
        return PROVIDERS[self.provider].get("package") == "openai"

    def _is_gemini(self):
        return PROVIDERS[self.provider].get("package") == "google-generativeai"

    def _is_azure_openai(self):
        return PROVIDERS[self.provider].get("package") == "azure-openai"

    def _is_bedrock_claude(self):
        return PROVIDERS[self.provider].get("package") == "bedrock-claude"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def ask(self, system: str, user: str, max_tokens: int = 2048) -> str:
        """Send a single-turn message and return the full response text."""
        if self._is_openai():
            return self._openai_ask(system, user, max_tokens)
        if self._is_gemini():
            return self._gemini_ask(system, user, max_tokens)
        if self._is_azure_openai():
            return self._azure_openai_ask(system, user, max_tokens)
        if self._is_bedrock_claude():
            return self._bedrock_claude_ask(system, user, max_tokens)
        raise ValueError(f"Unsupported provider: {self.provider}")

    def stream(self, system: str, user: str, max_tokens: int = 2048):
        """Stream response as text chunks (compatible with st.write_stream)."""
        if self._is_openai():
            yield from self._openai_stream(system, user, max_tokens)
        elif self._is_gemini():
            yield from self._gemini_stream(system, user, max_tokens)
        elif self._is_azure_openai():
            yield from self._azure_openai_stream(system, user, max_tokens)
        elif self._is_bedrock_claude():
            yield from self._bedrock_claude_stream(system, user, max_tokens)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def stream_with_history(self, system: str, messages: list, max_tokens: int = 2048):
        """Multi-turn streaming chat. messages is a list of {role, content} dicts."""
        if self._is_openai():
            yield from self._openai_stream_history(system, messages, max_tokens)
        elif self._is_gemini():
            yield from self._gemini_stream_history(system, messages, max_tokens)
        elif self._is_azure_openai():
            yield from self._azure_openai_stream_history(system, messages, max_tokens)
        elif self._is_bedrock_claude():
            yield from self._bedrock_claude_stream_history(system, messages, max_tokens)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    # ------------------------------------------------------------------
    # WB Desktop (DesktopToken auth) — shared token helper
    # ------------------------------------------------------------------

    def _get_desktop_token(self) -> str:
        """Get access token via itsai DesktopToken for mAI Factory calls."""
        try:
            from itsai.platform.authentication import DesktopToken
            token_class = DesktopToken()
            return token_class.token_provider(env="DEV")
        except Exception as e:
            raise RuntimeError(f"DesktopToken auth failed: {e}. Ensure itsai SDK is installed and you are on a WB machine.")

    def _get_auth_token(self) -> str:
        """Get bearer token — uses azure.identity on Posit Connect, DesktopToken on WB desktop."""
        if self.provider == "WB Posit (Claude)":
            from azure.identity import DefaultAzureCredential
            scope = _os.getenv("MAI_FACTORY_SCOPE", "api://azapimdev.worldbank.org/.default")
            cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
            return cred.get_token(scope).token
        return self._get_desktop_token()

    # ------------------------------------------------------------------
    # WB Desktop (GPT via mAI Factory) — DesktopToken + AzureOpenAI
    # ------------------------------------------------------------------

    def _azure_openai_client(self):
        from openai import AzureOpenAI
        pinfo = PROVIDERS[self.provider]
        if self.provider == "WB Posit (GPT)":
            from azure.identity import DefaultAzureCredential
            scope = _os.getenv("MAI_FACTORY_SCOPE", "api://azapimdev.worldbank.org/.default")
            cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
            token_provider = lambda: cred.get_token(scope).token
        else:
            try:
                from itsai.platform.authentication import DesktopToken
            except ImportError:
                raise ImportError("Install: pip install itsai-platform")
            token_class = DesktopToken()
            token_provider = lambda: token_class.token_provider(env="DEV")
        return AzureOpenAI(
            azure_endpoint=pinfo.get("mai_base", self.api_key),
            azure_ad_token_provider=token_provider,
            api_version=pinfo.get("api_version", "2025-04-01-preview"),
        )

    def _azure_openai_ask(self, system, user, max_tokens):
        client = self._azure_openai_client()
        kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        if self.model == "gpt-5":
            kwargs["max_completion_tokens"] = max_tokens
            kwargs["reasoning_effort"] = "low"
        else:
            kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(**kwargs)
        if not resp.choices:
            return ""
        return resp.choices[0].message.content or ""

    def _azure_openai_stream(self, system, user, max_tokens):
        yield self._azure_openai_ask(system, user, max_tokens)

    def _azure_openai_stream_history(self, system, messages, max_tokens):
        client = self._azure_openai_client()
        full_messages = [{"role": "system", "content": system}] + messages
        kwargs = dict(model=self.model, messages=full_messages)
        if self.model == "gpt-5":
            kwargs["max_completion_tokens"] = max_tokens
            kwargs["reasoning_effort"] = "low"
        else:
            kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(**kwargs)
        if resp.choices:
            yield resp.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # WB Desktop (Claude via mAI Factory Bedrock) — DesktopToken + requests
    # ------------------------------------------------------------------

    def _bedrock_claude_ask(self, system, user, max_tokens):
        import requests
        token = self._get_auth_token()
        pinfo = PROVIDERS[self.provider]
        url = f"{pinfo['bedrock_base']}{self.model}/converse"
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
        }
        if system:
            payload["system"] = [{"type": "text", "text": system}]
        resp = requests.post(url, json=payload, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("output", {}).get("message", {}).get("content") or data.get("content") or [{}])
        return content[0].get("text", "")

    def _bedrock_claude_stream(self, system, user, max_tokens):
        yield self._bedrock_claude_ask(system, user, max_tokens)

    def _bedrock_claude_stream_history(self, system, messages, max_tokens):
        import requests
        token = self._get_auth_token()
        pinfo = PROVIDERS[self.provider]
        url = f"{pinfo['bedrock_base']}{self.model}/converse"
        bedrock_messages = []
        for m in messages:
            role = m["role"]
            content = m["content"]
            if isinstance(content, str):
                bedrock_messages.append({"role": role, "content": [{"type": "text", "text": content}]})
            else:
                bedrock_messages.append({"role": role, "content": content})
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": bedrock_messages,
        }
        if system:
            payload["system"] = [{"type": "text", "text": system}]
        resp = requests.post(url, json=payload, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("output", {}).get("message", {}).get("content") or data.get("content") or [{}])
        yield content[0].get("text", "")

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
