"""Require a signed-in alliance leader for all private workspace actions."""

import re

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from alliance_auth import csrf_check, current_account, membership, workspace_by_slug
from scoped_db import current_workspace


PRIVATE_PATH = re.compile(r"^/a/([0-9]+-[a-z0-9]+)/api(?:/|$)")
MANAGE_PATH = re.compile(r"^/a/([0-9]+-[a-z0-9]+)/manage/?$")


def _authorize(connect_db, request, slug, api_path):
    account = current_account(connect_db, request)
    if not account:
        return None
    with connect_db() as conn, conn.cursor() as cur:
        workspace = workspace_by_slug(cur,slug)
        role = membership(cur,workspace["id"],account["id"])
    if role not in ("R4", "R5"):
        return None
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf_check(request)
    if role != "R5" and (api_path.startswith("/api/admin/clear-data") or
                          (api_path == "/api/train/settings" and request.method != "GET")):
        return None
    return {"id":workspace["id"], "slug":slug, "name":workspace["name"], "timezone":workspace.get("timezone") or "UTC", "role":role, "actor":account,
            "action":request.method + " " + api_path}


class WorkspaceBoundary:
    def __init__(self, app, connect_db):
        self.app = app
        self.connect_db = connect_db

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        match = PRIVATE_PATH.match(path)
        manage = MANAGE_PATH.match(path)
        if path.startswith("/api/") and not path.startswith(("/api/auth/", "/api/public/")):
            return await JSONResponse({"detail":"Open an alliance and sign in."},status_code=401)(scope,receive,send)
        if not match and not manage:
            return await self.app(scope,receive,send)
        slug = (match or manage).group(1)
        api_path = path[len("/a/" + slug):] if match else "/manage"
        request = Request(scope,receive)
        try:
            workspace = await anyio.to_thread.run_sync(_authorize,self.connect_db,request,slug,api_path)
        except Exception as exc:
            status = getattr(exc,"status_code",503)
            detail = getattr(exc,"detail","Workspace access is temporarily unavailable.")
            return await JSONResponse({"detail":detail},status_code=status)(scope,receive,send)
        if not workspace:
            response = JSONResponse({"detail":"Leadership access is required."},status_code=403) if match else RedirectResponse("/a/"+slug,status_code=303)
            return await response(scope,receive,send)
        token = current_workspace.set(workspace)
        try:
            if match:
                scope = dict(scope)
                scope["path"] = api_path
                scope["raw_path"] = api_path.encode("ascii")
            await self.app(scope,receive,send)
        finally:
            current_workspace.reset(token)
