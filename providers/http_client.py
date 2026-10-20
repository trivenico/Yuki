"""HTTP helpers that refuse redirects for model-provider requests.

Provider endpoints are configuration inputs. Refusing redirects prevents a configured
loopback endpoint from redirecting requests (or bearer credentials) to another host.
"""
from __future__ import annotations

import urllib.request


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Reject all redirects rather than forwarding prompts or authorization headers."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_provider_request(request: urllib.request.Request, *, timeout: float):
    """Open a request without following redirects.

    Callers must validate the configured base URL and handle HTTPError/URLError.
    """
    opener = urllib.request.build_opener(_RejectRedirects)
    return opener.open(request, timeout=timeout)
