"""Safe, useful provider failure descriptions for user-facing events."""


def provider_failure(provider: str, *, error: Exception | None = None,
                     status_code: int | None = None) -> str:
    name = provider.strip().title()
    if status_code is None and error is not None:
        candidate = getattr(error, "status_code", None) or getattr(error, "code", None)
        status_code = candidate if isinstance(candidate, int) else None
    if status_code in (401, 403):
        return f"{name} authentication failed. Check the configured credentials."
    if status_code == 429:
        return f"{name} rate limit reached. Wait briefly and try again."
    if status_code is not None:
        return f"{name} request failed with HTTP {status_code}. Try again later."
    error_name = type(error).__name__.lower() if error is not None else ""
    if "timeout" in error_name:
        return f"{name} request timed out. Try again."
    if "auth" in error_name or "credential" in error_name or "permission" in error_name:
        return f"{name} authentication failed. Check the configured credentials."
    if "rate" in error_name or "quota" in error_name:
        return f"{name} rate limit reached. Wait briefly and try again."
    return f"{name} is unavailable or returned an error. Try again later."
