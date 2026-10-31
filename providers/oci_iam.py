"""OCI IAM-authenticated OpenAI-compatible inference provider.

Authentication signing and refresh are delegated to Oracle's oci-genai-auth
library; Yuki owns endpoint validation, request bounds, and response handling.
"""
from __future__ import annotations

import json

from providers.openai_compatible import _trusted_https_endpoint


def create_oci_iam_auth(mode="resource_principal", *, config_file=None, profile_name="DEFAULT"):
    """Create an Oracle-maintained OCI signer for the selected runtime identity."""
    try:
        from oci_genai_auth import (
            OciInstancePrincipalAuth,
            OciResourcePrincipalAuth,
            OciSessionAuth,
            OciUserPrincipalAuth,
        )
    except ImportError as exc:
        raise RuntimeError(
            "OCI IAM requires oci-genai-auth==1.1.1; install requirements-oci.txt."
        ) from exc

    classes = {
        "resource_principal": OciResourcePrincipalAuth,
        "instance_principal": OciInstancePrincipalAuth,
        "session": OciSessionAuth,
        "user_principal": OciUserPrincipalAuth,
    }
    if mode not in classes:
        raise ValueError(
            "YUKI_OCI_AUTH_MODE must be resource_principal, instance_principal, session, or user_principal."
        )
    if mode in {"session", "user_principal"}:
        kwargs = {"profile_name": profile_name}
        if config_file:
            kwargs["config_file"] = config_file
        return classes[mode](**kwargs)
    return classes[mode]()


class OciIamProvider:
    """Call OCI Generative AI with a refreshable OCI IAM signer."""

    def __init__(self, model, *, region, project_ocid, auth, timeout=45, http_client=None):
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid OCI model identifier.")
        if not isinstance(region, str) or not region.strip():
            raise ValueError("Configure YUKI_OCI_REGION.")
        if not isinstance(project_ocid, str) or not project_ocid.startswith(
            "ocid1.generativeaiproject."
        ):
            raise ValueError("Configure YUKI_OCI_PROJECT_OCID.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        host = f"inference.generativeai.{region.strip().lower()}.oci.oraclecloud.com"
        self.base_url = _trusted_https_endpoint(
            f"https://{host}/openai/v1", allowed_hosts={host}
        )
        self.model = model.strip()
        self.project_ocid = project_ocid
        self.timeout = timeout
        if http_client is None:
            try:
                import httpx
            except ImportError as exc:
                raise RuntimeError(
                    "OCI IAM inference requires httpx; install requirements-oci.txt."
                ) from exc
            http_client = httpx.Client(
                auth=auth,
                follow_redirects=False,
                timeout=timeout,
                verify=True,
            )
        self._client = http_client

    def is_available(self):
        return self._client is not None

    def generate(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt.strip()[:20000]}],
            "stream": False,
        }
        try:
            response = self._client.post(
                self.base_url + "/chat/completions",
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "openai-project": self.project_ocid,
                },
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                raise RuntimeError("OCI inference endpoint returned a redirect; redirects are disabled.")
            response.raise_for_status()
            raw = response.content
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("OCI IAM inference request failed.") from exc
        if not isinstance(raw, bytes) or len(raw) > 2_000_000:
            raise RuntimeError("OCI inference response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            result = body["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("OCI inference returned an unsupported response.") from exc
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("OCI inference returned no text response.")
        return result.strip()
