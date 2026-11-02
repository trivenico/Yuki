"""OCI Responses API provider for stateful and agent-oriented inference."""
from __future__ import annotations

import json

from providers.oci_iam import OciIamProvider


class OciResponsesProvider(OciIamProvider):
    """Use OCI's OpenAI-compatible Responses endpoint with OCI IAM signing."""

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        try:
            response = self._client.post(
                self.base_url + "/responses",
                json={
                    "model": self.model,
                    "input": prompt.strip()[:20000],
                    "stream": False,
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "openai-project": self.project_ocid,
                },
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                raise RuntimeError("OCI Responses endpoint returned a redirect; redirects are disabled.")
            response.raise_for_status()
            raw = response.content
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("OCI Responses API request failed.") from exc
        if not isinstance(raw, bytes) or len(raw) > 2_000_000:
            raise RuntimeError("OCI Responses API response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            answer = body.get("output_text")
            if not isinstance(answer, str) or not answer.strip():
                chunks = []
                for item in body.get("output", []):
                    if not isinstance(item, dict):
                        continue
                    for part in item.get("content", []):
                        if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                            value = part.get("text")
                            if isinstance(value, str):
                                chunks.append(value)
                answer = "\n".join(chunks)
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            raise RuntimeError("OCI Responses API returned an unsupported response.") from exc
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("OCI Responses API returned no text output.")
        return answer.strip()
