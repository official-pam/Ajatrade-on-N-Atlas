"""Request-signature verification against Meta's app secret."""

import hashlib
import hmac


def verify_signature(
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
) -> bool:
    """Validate the ``X-Hub-Signature-256`` header for a raw request body."""
    if not app_secret:
        return True  # skip in local dev; always set it in production
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))
