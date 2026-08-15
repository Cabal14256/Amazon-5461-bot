#!/usr/bin/env python3
"""Generate a self-signed TLS certificate for LAN development use.

Output: ``runtime/private/certs/web-console.crt`` / ``web-console.key``
(git-ignored, local-only).  Prefers the ``cryptography`` package; falls back
to the ``openssl`` CLI; fails with a clear message when neither exists.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
DEFAULT_OUT_DIR = project_root / "runtime" / "private" / "certs"


def _generate_with_cryptography(cert_path: Path, key_path: Path, common_name: str, days: int) -> bool:
    try:
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        return False

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    san = x509.SubjectAlternativeName(
        [x509.DNSName(common_name), x509.DNSName("localhost"), x509.IPAddress(
            __import__("ipaddress").ip_address("127.0.0.1"))]
    )
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return True


def _generate_with_openssl(cert_path: Path, key_path: Path, common_name: str, days: int) -> bool:
    openssl = shutil.which("openssl")
    if not openssl:
        return False
    subprocess.run(
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-days", str(days), "-subj", f"/CN={common_name}",
            "-addext", f"subjectAltName=DNS:{common_name},DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate self-signed dev certificate")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--common-name", default="amazon-5461-web.local",
                        help="CN/SAN，建议用本机局域网主机名")
    parser.add_argument("--days", type=int, default=825)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cert_path = out_dir / "web-console.crt"
    key_path = out_dir / "web-console.key"

    if _generate_with_cryptography(cert_path, key_path, args.common_name, args.days):
        backend = "cryptography"
    elif _generate_with_openssl(cert_path, key_path, args.common_name, args.days):
        backend = "openssl"
    else:
        print(
            "错误: 既无 cryptography 库也无 openssl 命令，无法生成证书。\n"
            "请执行 .\\.venv\\Scripts\\python.exe -m pip install cryptography "
            "或安装 OpenSSL 后重试。",
            file=sys.stderr,
        )
        return 1

    print(f"已生成自签证书（{backend}）:")
    print(f"  证书: {cert_path}")
    print(f"  私钥: {key_path}")
    print("在 config/settings.yaml 的 web: 段配置 ssl_cert_path / ssl_key_path 后重启控制台。")
    print("局域网客户端首次访问需手动信任该自签证书。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
