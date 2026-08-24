"""Shared Coordinator route helpers."""

from fastapi import Request


def ok(request: Request, data):
    return {"data": data, "meta": {}, "error": None, "request_id": request.state.request_id}
