"""账号体系：注册 / 登录 / 会话。

- 密码用 PBKDF2-SHA256 + 随机盐存储（标准库 hashlib，无额外依赖）
- 会话用服务端内存 token（重启失效，demo 场景足够）
- 用户名/密码长度与字符做基本校验，错误信息不区分"用户不存在/密码错误"
"""
import hashlib
import hmac
import os
import re
import secrets
import time

from db import pool

PBKDF2_ROUNDS = 120_000
TOKEN_TTL = 7 * 24 * 3600  # 7 天
_sessions: dict[str, dict] = {}

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


def _hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${dk.hex()}"


def _verify(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def _public(row: dict) -> dict:
    return {"id": row["id"], "username": row["username"],
            "display_name": row["display_name"], "role": row["role"],
            "last_login": str(row["last_login_at"] or "")}


def register(username: str, password: str, display_name: str = "") -> dict:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("用户名需 3-20 位，仅限字母、数字、下划线")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    if len(password or "") > 128:
        # 上限保护：PBKDF2 对超长输入会明显放大 CPU 开销，可被用来打满服务
        raise ValueError("密码最多 128 位")
    exists = pool.query("SELECT id FROM app_users WHERE username = %s", (username,))
    if exists:
        raise ValueError("该用户名已被注册，换个试试")
    name = (display_name or "").strip() or username
    if len(name) > 32:
        raise ValueError("显示名最多 32 个字符")
    pool.execute(
        "INSERT INTO app_users (username, display_name, password_hash) VALUES (%s, %s, %s)",
        (username, name, _hash(password)))
    row = pool.query("SELECT * FROM app_users WHERE username = %s", (username,))[0]
    return {"user": _public(row), "token": _issue(row)}


def login(username: str, password: str) -> dict:
    rows = pool.query("SELECT * FROM app_users WHERE username = %s", ((username or "").strip(),))
    if not rows or not _verify(password or "", rows[0]["password_hash"]):
        raise ValueError("用户名或密码不正确")
    row = rows[0]
    pool.execute("UPDATE app_users SET last_login_at = NOW() WHERE id = %s", (row["id"],))
    row = pool.query("SELECT * FROM app_users WHERE id = %s", (row["id"],))[0]
    return {"user": _public(row), "token": _issue(row)}


def _issue(row: dict) -> str:
    token = secrets.token_urlsafe(24)
    _sessions[token] = {"uid": row["id"], "exp": time.time() + TOKEN_TTL}
    return token


def user_of(token: str | None) -> dict | None:
    """按 token 取当前用户；token 失效返回 None。"""
    if not token:
        return None
    sess = _sessions.get(token)
    if not sess:
        return None
    if sess["exp"] < time.time():
        _sessions.pop(token, None)
        return None
    rows = pool.query("SELECT * FROM app_users WHERE id = %s", (sess["uid"],))
    return _public(rows[0]) if rows else None


def logout(token: str | None) -> None:
    if token:
        _sessions.pop(token, None)


def logout_others(uid: int, keep: str | None = None) -> int:
    """退出该账号的其他所有会话，返回被清掉的会话数。"""
    dead = [t for t, s in _sessions.items() if s["uid"] == uid and t != keep]
    for t in dead:
        _sessions.pop(t, None)
    return len(dead)


def update_profile(uid: int, display_name: str = "", role: str = "", username: str = "") -> dict:
    sets, params = [], []
    if username:
        username = username.strip()
        if not USERNAME_RE.match(username):
            raise ValueError("用户名需 3-20 位，仅限字母、数字、下划线")
        dup = pool.query("SELECT id FROM app_users WHERE username = %s AND id <> %s", (username, uid))
        if dup:
            raise ValueError("该用户名已被占用，换个试试")
        sets.append("username=%s")
        params.append(username)
    if display_name is not None and display_name.strip():
        name = display_name.strip()
        if len(name) > 32:
            raise ValueError("显示名最多 32 个字符")
        sets.append("display_name=%s")
        params.append(name)
    if role in ("hr", "admin"):
        sets.append("role=%s")
        params.append(role)
    if not sets:
        raise ValueError("没有需要更新的内容")
    pool.execute(f"UPDATE app_users SET {', '.join(sets)} WHERE id=%s", (*params, uid))
    return _public(pool.query("SELECT * FROM app_users WHERE id = %s", (uid,))[0])


def change_password(uid: int, old_password: str, new_password: str) -> None:
    rows = pool.query("SELECT * FROM app_users WHERE id = %s", (uid,))
    if not rows:
        raise ValueError("账号不存在")
    if not _verify(old_password or "", rows[0]["password_hash"]):
        raise ValueError("当前密码不正确")
    if len(new_password or "") < 6:
        raise ValueError("新密码至少 6 位")
    if len(new_password or "") > 128:
        raise ValueError("新密码最多 128 位")
    if old_password == new_password:
        raise ValueError("新密码不能与当前密码相同")
    pool.execute("UPDATE app_users SET password_hash = %s WHERE id = %s",
                 (_hash(new_password), uid))


DEMO_USERNAME = "demo"
# 标准演示密码；可用环境变量 DEMO_PASSWORD 覆盖（部署到公开环境时必须改）
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD") or "demo123"


def seed_demo_user() -> None:
    """确保演示账号可用（幂等）。

    以前只判断「账号存在就 return」，结果测试过程中改过密码后，
    种子逻辑再也不会把它修回来，演示时就登不进去了。
    现在改为「账号存在但密码与演示密码不一致时重置」，保证演示账号始终可登录。
    """
    try:
        rows = pool.query("SELECT id, password_hash FROM app_users WHERE username = %s LIMIT 1",
                          (DEMO_USERNAME,))
        if not rows:
            pool.execute(
                "INSERT INTO app_users (username, display_name, password_hash, role) "
                "VALUES (%s, '演示 HR', %s, 'admin')", (DEMO_USERNAME, _hash(DEMO_PASSWORD)))
            return
        if not _verify(DEMO_PASSWORD, rows[0]["password_hash"] or ""):
            pool.execute("UPDATE app_users SET password_hash = %s WHERE id = %s",
                         (_hash(DEMO_PASSWORD), rows[0]["id"]))
    except Exception:
        pass
