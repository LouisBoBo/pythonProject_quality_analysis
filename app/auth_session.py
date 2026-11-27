# -*- coding: utf-8 -*-
"""品质分析登录会话：代理 ERP/MES /api/auth/login，本地签发会话 Cookie。"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("PCB_Quality.auth")

# 默认企业（与 zr_aicoding 登录页一致）
DEFAULT_ENTERPRISES = ["江西中软电子有限公司"]

# 简易登录限流：同一 IP 在窗口内失败次数过多则暂时拒绝
_LOGIN_FAILS: Dict[str, list] = {}
_LOGIN_LOCK = threading.Lock()
_LOGIN_WINDOW_S = 300
_LOGIN_MAX_FAILS = 8
# 全局上限：最多跟踪的 IP 条目数，避免大量来源 IP 撑爆内存
_LOGIN_MAX_IPS = 4096


def _purge_locked(now: float) -> None:
    """清理窗口外记录；仍超全局上限时按最近失败时间淘汰最旧条目。"""
    expired = [
        ip for ip, hits in _LOGIN_FAILS.items()
        if not any(now - t < _LOGIN_WINDOW_S for t in hits)
    ]
    for ip in expired:
        _LOGIN_FAILS.pop(ip, None)
    overflow = len(_LOGIN_FAILS) - _LOGIN_MAX_IPS
    if overflow > 0:
        oldest = sorted(_LOGIN_FAILS.items(), key=lambda kv: max(kv[1] or [0]))
        for ip, _ in oldest[:overflow]:
            _LOGIN_FAILS.pop(ip, None)


def check_login_rate_limit(client_ip: str) -> Optional[str]:
    """超限返回错误文案，否则 None。"""
    ip = (client_ip or "unknown").strip() or "unknown"
    now = time.time()
    with _LOGIN_LOCK:
        _purge_locked(now)
        hits = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < _LOGIN_WINDOW_S]
        _LOGIN_FAILS[ip] = hits
        if len(hits) >= _LOGIN_MAX_FAILS:
            return f"登录尝试过于频繁，请 {int(_LOGIN_WINDOW_S / 60)} 分钟后再试"
    return None


def record_login_failure(client_ip: str) -> None:
    ip = (client_ip or "unknown").strip() or "unknown"
    now = time.time()
    with _LOGIN_LOCK:
        _purge_locked(now)
        hits = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < _LOGIN_WINDOW_S]
        hits.append(now)
        _LOGIN_FAILS[ip] = hits
        _purge_locked(now)


def clear_login_failures(client_ip: str) -> None:
    ip = (client_ip or "unknown").strip() or "unknown"
    with _LOGIN_LOCK:
        _LOGIN_FAILS.pop(ip, None)


@dataclass
class UserSession:
    sid: str
    username: str
    enterprise_code: str = ""
    role: str = ""
    erp_token: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def to_public(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "enterprise_code": self.enterprise_code,
            "role": self.role or "user",
            "expires_at": int(self.expires_at),
        }


class SessionStore:
    """进程内会话表（线程安全）。"""

    def __init__(self, ttl_seconds: int = 86400 * 7):
        self._ttl = max(3600, int(ttl_seconds))
        self._lock = threading.RLock()
        self._sessions: Dict[str, UserSession] = {}

    def create(
        self,
        username: str,
        *,
        enterprise_code: str = "",
        role: str = "",
        erp_token: str = "",
    ) -> UserSession:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        sess = UserSession(
            sid=sid,
            username=(username or "").strip(),
            enterprise_code=(enterprise_code or "").strip(),
            role=(role or "").strip(),
            erp_token=erp_token or "",
            created_at=now,
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._purge_locked(now)
            self._sessions[sid] = sess
        return sess

    def get(self, sid: str) -> Optional[UserSession]:
        if not sid:
            return None
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            sess = self._sessions.get(sid)
            if not sess:
                return None
            if sess.expires_at < now:
                self._sessions.pop(sid, None)
                return None
            return sess

    def delete(self, sid: str) -> None:
        if not sid:
            return
        with self._lock:
            self._sessions.pop(sid, None)

    def _purge_locked(self, now: float) -> None:
        dead = [k for k, v in self._sessions.items() if v.expires_at < now]
        for k in dead:
            self._sessions.pop(k, None)


_STORE: Optional[SessionStore] = None


def get_session_store(ttl_seconds: int = 86400 * 7) -> SessionStore:
    global _STORE
    if _STORE is None:
        _STORE = SessionStore(ttl_seconds=ttl_seconds)
    return _STORE


def erp_login(
    base_url: str,
    username: str,
    password: str,
    enterprise_code: str = "",
    *,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """调用 ERP/MES POST /api/auth/login，返回 token 与规范化结果。"""
    base = (base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("未配置 ERP/MES 登录地址")
    user = (username or "").strip()
    if not user or not password:
        raise ValueError("请输入账号和密码")

    payload = {
        "username": user,
        "password": password,
        "enterprise_code": (enterprise_code or "").strip() or "江西中软电子有限公司",
    }
    url = f"{base}/api/auth/login"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=max(5.0, float(timeout))) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = getattr(resp, "status", None) or resp.getcode()
    except urllib.error.HTTPError as e:
        detail = "账号或密码错误"
        try:
            body = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            d = parsed.get("detail") or parsed.get("error") or body
            if isinstance(d, list):
                detail = "; ".join(
                    str(x.get("msg") or x) if isinstance(x, dict) else str(x) for x in d
                )
            elif d:
                detail = str(d)
        except Exception:
            pass
        raise PermissionError(detail) from e
    except urllib.error.URLError as e:
        raise ConnectionError(f"无法连接 ERP/MES 登录服务 {base}：{e.reason}") from e

    try:
        result = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ERP/MES 登录返回非 JSON（HTTP {code}）") from e
    if not isinstance(result, dict):
        raise RuntimeError("ERP/MES 登录返回格式异常")

    token = (result.get("access_token") or result.get("token") or "").strip()
    if not token:
        raise RuntimeError("ERP/MES 登录成功但未返回 access_token")

    # 可选：拉取 /api/auth/me 补全角色
    role = ""
    try:
        me_req = urllib.request.Request(
            f"{base}/api/auth/me",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(me_req, timeout=max(5.0, float(timeout))) as me_resp:
            me_raw = me_resp.read().decode("utf-8", errors="replace")
            me = json.loads(me_raw) if me_raw else {}
            if isinstance(me, dict):
                role = str(me.get("role") or "").strip()
                if me.get("username"):
                    user = str(me.get("username")).strip() or user
    except Exception as e:
        logger.info("登录后拉取 /api/auth/me 失败（可忽略）: %s", e)

    return {
        "access_token": token,
        "token_type": result.get("token_type") or "bearer",
        "username": user,
        "role": role,
        "enterprise_code": payload["enterprise_code"],
        "erp_auth_base": base,
    }


def session_cookie_header(
    sid: str,
    *,
    max_age: int,
    clear: bool = False,
    secure: bool = False,
) -> str:
    flags = ["HttpOnly", "SameSite=Lax", "Path=/"]
    if secure:
        flags.append("Secure")
    if clear:
        return "qa_sid=; " + "; ".join(flags) + "; Max-Age=0"
    age = max(60, int(max_age))
    return f"qa_sid={sid}; " + "; ".join(flags) + f"; Max-Age={age}"


def safe_internal_next(next_path: str, default: str = "/pcb_quality_system.html") -> str:
    """仅允许站内相对路径，拒绝 //evil、反斜杠、协议相对开放重定向。"""
    raw = (next_path or "").strip().split("#", 1)[0]
    if not raw:
        return default
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return default
    if "://" in raw or ".." in raw:
        return default
    path_only = raw.split("?", 1)[0]
    if path_only in ("/", "/pcb_quality_system.html", "/login.html", "/index"):
        return raw
    return default
