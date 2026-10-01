"""Базовая аутентификация и роли.

Роли: viewer (без входа — только просмотр), dispatcher (внесение нештатных ситуаций, применение планов), admin (настройки индекса,
ограничений и параметров оптимизации). Учётные данные и ключ подписи токена — ТОЛЬКО из переменных окружения:
  RAILTWIN_ADMIN_USER / RAILTWIN_ADMIN_PASSWORD, RAILTWIN_DISPATCHER_USER / RAILTWIN_DISPATCHER_PASSWORD, RAILTWIN_SECRET.
Если переменные не заданы и RAILTWIN_DEV != 0, включается DEV-режим с демо-учётками (в логах — предупреждение).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request, WebSocket

LOG = logging.getLogger("railtwin")
RANK = {"viewer": 0, "dispatcher": 1, "admin": 2}
TTL = 8 * 3600


class Auth:
    def __init__(self):
        env = os.environ.get
        self.dev = env("RAILTWIN_DEV", "1") != "0"
        self.open_demo = env("RAILTWIN_OPEN_DEMO", "0") == "1"       # анонимный посетитель = диспетчер (для жюри); admin — только по паролю
        self.users: Dict[str, Dict[str, str]] = {}
        for role in ("admin", "dispatcher"):
            u, p = env(f"RAILTWIN_{role.upper()}_USER"), env(f"RAILTWIN_{role.upper()}_PASSWORD")
            if u and p:
                self.users[u] = {"password": p, "role": role}
        self.demo = False
        if not self.users and self.dev:
            self.users = {"admin": {"password": "admin", "role": "admin"}, "dispatcher": {"password": "dispatcher", "role": "dispatcher"}}
            self.demo = True
            LOG.warning("DEV-режим: включены демо-учётки admin/admin и dispatcher/dispatcher. Для прода задайте RAILTWIN_*_USER/PASSWORD.")
        self.secret = (env("RAILTWIN_SECRET") or secrets.token_hex(32)).encode()
        if not env("RAILTWIN_SECRET"):
            LOG.warning("RAILTWIN_SECRET не задан: сгенерирован случайный ключ (токены сбросятся при перезапуске).")
        self.fails: Dict[str, list] = {}

    # ------------------------------------------------------------------------------------------------------------
    def _sign(self, payload: bytes) -> str:
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    def issue(self, user: str, role: str) -> str:
        body = base64.urlsafe_b64encode(json.dumps({"sub": user, "role": role, "exp": int(time.time()) + TTL}).encode())
        return body.decode() + "." + self._sign(body)

    def verify(self, token: str) -> Optional[dict]:
        try:
            body, sig = token.split(".", 1)
            if not hmac.compare_digest(sig, self._sign(body.encode())):
                return None
            d = json.loads(base64.urlsafe_b64decode(body))
            return d if d["exp"] > time.time() else None
        except Exception:
            return None

    def login(self, username: str, password: str, ip: str = "-") -> Optional[str]:
        f = [t for t in self.fails.get(ip, []) if time.time() - t < 60]
        if len(f) >= 8:
            raise HTTPException(429, "too many attempts")
        u = self.users.get(username)
        ok = u is not None and hmac.compare_digest(u["password"], password)
        if not ok:
            f.append(time.time())
            self.fails[ip] = f
            return None
        return self.issue(username, u["role"])

    # ------------------------------------------------------------------------------------------------------------
    def role_of(self, request: Request | WebSocket) -> str:
        h = request.headers.get("authorization", "")
        tok = h[7:] if h.lower().startswith("bearer ") else request.query_params.get("token", "")
        if tok:
            d = self.verify(tok)
            if d:
                return d["role"]
        if h.lower().startswith("basic "):
            try:
                user, pw = base64.b64decode(h[6:]).decode().split(":", 1)
                u = self.users.get(user)
                if u and hmac.compare_digest(u["password"], pw):
                    return u["role"]
            except Exception:
                pass
        return "dispatcher" if self.open_demo else "viewer"


AUTH = Auth()


def require(role: str):
    def dep(request: Request):
        r = AUTH.role_of(request)
        if RANK[r] < RANK[role]:
            raise HTTPException(401 if r == "viewer" else 403, f"role '{role}' required", headers={"WWW-Authenticate": "Bearer"})
        return r
    return Depends(dep)
