"""A device or service API for the tests of HTTP adapters: (method, path) or path → JSON, text, a Response or a
callable taking the request. Every request is recorded as (method, path, JSON body)."""
import json

import httpx


class FakeApi:
    def __init__(self, routes: dict):
        self.routes = routes
        self.sent: list[tuple[str, str, object]] = []
        self.requests: list[httpx.Request] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(req.content) if req.content else None
        except ValueError:
            body = req.content.decode()
        self.sent.append((req.method, req.url.path, body))
        self.requests.append(req)
        hit = self.routes.get((req.method, req.url.path), self.routes.get(req.url.path))
        if hit is None:
            return httpx.Response(404)
        if callable(hit):
            hit = hit(req)
        if isinstance(hit, httpx.Response):
            return hit
        if isinstance(hit, str):
            return httpx.Response(200, text=hit)
        return httpx.Response(200, json=hit)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self, base="https://192.0.2.1", **kw) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base, transport=self.transport(), **kw)

    def attach(self, driver, base="https://192.0.2.1"):
        """Replaces the HTTP client of a device driver, keeping its headers and authentication."""
        old = driver.http
        driver.http = httpx.AsyncClient(base_url=base, transport=self.transport(), headers=old.headers, auth=old.auth)
        return driver
