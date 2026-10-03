from urllib.parse import urlencode


def build_corid_mobile_deep_link(
    corid_session_token: str,
    *,
    email: str | None = None,
    cor_id: str | None = None,
) -> str:
    deep_link_params = {"sessionToken": corid_session_token}
    if email:
        deep_link_params["email"] = email
    if cor_id:
        deep_link_params["cor_id"] = cor_id
    return f"coridapp://open?{urlencode(deep_link_params)}"
