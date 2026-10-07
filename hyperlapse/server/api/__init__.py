from fastapi import Request

from ..context import AppContext


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx
