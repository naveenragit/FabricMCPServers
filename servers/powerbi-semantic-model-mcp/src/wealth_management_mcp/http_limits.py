"""Final ASGI response limit, after SDK serialization and metadata framing."""

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class ResponseLimitMiddleware:
    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # This POC serves request/response JSON, not long-lived subscriptions.
        if scope["method"] == "GET" and scope["path"].rstrip("/") == "/mcp":
            await Response(status_code=405)(scope, receive, send)
            return
        start: Message | None = None
        chunks: list[bytes] = []
        size = 0
        rejected = False

        async def bounded_send(message: Message) -> None:
            nonlocal start, size, rejected
            if message["type"] == "http.response.start":
                start = message
            elif message["type"] == "http.response.body" and not rejected:
                body = message.get("body", b"")
                size += len(body)
                if size > self.limit:
                    rejected = True
                    chunks.clear()
                    await JSONResponse({"error": "response_limit_exceeded"}, status_code=502)(scope, receive, send)
                else:
                    chunks.append(body)
                    if not message.get("more_body", False):
                        assert start is not None
                        await send(start)
                        await send({"type": "http.response.body", "body": b"".join(chunks)})

        await self.app(scope, receive, bounded_send)