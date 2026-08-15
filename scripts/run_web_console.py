#!/usr/bin/env python3
"""Start the read-only web console.

Usage:
    python scripts/run_web_console.py [--host 127.0.0.1] [--port 8080]
                                      [--ssl-cert PATH --ssl-key PATH]

Defaults come from the ``web:`` section of ``config/settings.yaml``.
"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def main() -> int:
    from src.web.config import load_settings

    parser = argparse.ArgumentParser(description="Read-only web console server")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--ssl-cert", default=None, help="TLS certificate (PEM)")
    parser.add_argument("--ssl-key", default=None, help="TLS private key (PEM)")
    args = parser.parse_args()

    settings = load_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    ssl_cert = args.ssl_cert or settings.ssl_cert_path or None
    ssl_key = args.ssl_key or settings.ssl_key_path or None
    if bool(ssl_cert) != bool(ssl_key):
        parser.error("--ssl-cert 与 --ssl-key 必须同时提供")
    if settings.ephemeral_secret:
        print("[web] 警告: 未配置 WEB_SESSION_SECRET，使用临时密钥；重启后所有会话失效。"
              "生产部署请在 .env 中设置 WEB_SESSION_SECRET。")

    import uvicorn

    from src.web.app import create_app

    app = create_app(settings)
    scheme = "https" if ssl_cert else "http"
    print(f"[web] 只读控制台启动于 {scheme}://{host}:{port}")
    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_certfile=ssl_cert,
        ssl_keyfile=ssl_key,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
