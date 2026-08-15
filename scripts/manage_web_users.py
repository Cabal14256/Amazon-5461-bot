#!/usr/bin/env python3
"""Manage web console local users (add / list / disable / passwd).

Passwords are only accepted interactively (getpass) or via the
``WEB_USER_PASSWORD`` environment variable — never as command-line arguments,
and they are never printed or logged.  The stored value is a PBKDF2 hash.

Usage:
    python scripts/manage_web_users.py add --username alice --role admin
    python scripts/manage_web_users.py list
    python scripts/manage_web_users.py disable --username alice
    python scripts/manage_web_users.py passwd --username alice

    # optional overrides
    python scripts/manage_web_users.py --db runtime/state/ledger.db list
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.db import (  # noqa: E402
    add_web_user,
    get_web_user_by_username,
    init_db,
    list_web_users,
    reset_web_user_password,
    set_web_user_disabled,
)
from src.web.auth import hash_password  # noqa: E402

ROLES = ("viewer", "operator", "reviewer", "admin")


def _default_db_path() -> str:
    from src.web.config import load_settings

    return str(load_settings().db_path)


def _read_password(username: str) -> str:
    env_password = os.getenv("WEB_USER_PASSWORD")
    if env_password:
        return env_password
    first = getpass.getpass(f"Password for {username}: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("两次输入的密码不一致")
    return first


def cmd_add(args) -> int:
    username = args.username.strip()
    if not username:
        print("用户名不能为空", file=sys.stderr)
        return 2
    if get_web_user_by_username(args.db, username):
        print(f"用户已存在: {username}", file=sys.stderr)
        return 2
    password = _read_password(username)
    if len(password) < 8:
        print("密码至少 8 位", file=sys.stderr)
        return 2
    uid = add_web_user(
        args.db,
        username,
        hash_password(password),
        role=args.role,
        display_name=args.display_name,
    )
    print(f"已创建用户 {username} (id={uid}, role={args.role})")
    return 0


def cmd_list(args) -> int:
    users = list_web_users(args.db)
    if not users:
        print("（无 web 用户）")
        return 0
    for user in users:
        state = "disabled" if user["disabled"] else "active"
        last_login = user.get("last_login_at") or "-"
        print(f"{user['id']:>4}  {user['username']:<24} {user['role']:<10} {state:<9} last_login={last_login}")
    return 0


def cmd_disable(args) -> int:
    if not set_web_user_disabled(args.db, args.username.strip(), True):
        print(f"用户不存在: {args.username}", file=sys.stderr)
        return 2
    print(f"已禁用用户 {args.username}")
    return 0


def cmd_passwd(args) -> int:
    username = args.username.strip()
    if not get_web_user_by_username(args.db, username):
        print(f"用户不存在: {username}", file=sys.stderr)
        return 2
    password = _read_password(username)
    if len(password) < 8:
        print("密码至少 8 位", file=sys.stderr)
        return 2
    reset_web_user_password(args.db, username, hash_password(password))
    print(f"已重置密码并启用用户 {username}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Web console user management")
    parser.add_argument("--db", default=None, help="SQLite ledger path (default: settings.yaml paths.db_path)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="create a user (password via prompt or WEB_USER_PASSWORD)")
    p_add.add_argument("--username", required=True)
    p_add.add_argument("--role", default="viewer", choices=ROLES)
    p_add.add_argument("--display-name", default=None)
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="list users")
    p_list.set_defaults(func=cmd_list)

    p_disable = sub.add_parser("disable", help="disable a user")
    p_disable.add_argument("--username", required=True)
    p_disable.set_defaults(func=cmd_disable)

    p_passwd = sub.add_parser("passwd", help="reset a user's password and re-enable the account")
    p_passwd.add_argument("--username", required=True)
    p_passwd.set_defaults(func=cmd_passwd)

    args = parser.parse_args()
    args.db = args.db or _default_db_path()
    init_db(args.db)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
