from fastapi import WebSocket


def extract_websocket_bearer_token(websocket: WebSocket) -> str | None:
    auth_header = websocket.headers.get("authorization")
    if auth_header:
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer" and token:
            return token.strip()

    for query_key in ("access_token", "token"):
        token = websocket.query_params.get(query_key)
        if token:
            return token.strip()

    query_authorization = websocket.query_params.get("authorization")
    if query_authorization:
        scheme, _, token = query_authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            return token.strip()

    return None
