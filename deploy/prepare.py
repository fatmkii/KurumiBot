"""部署预检、运行素材初始化与 Supervisor 配置生成。"""
import hashlib
import ipaddress
import os
from pathlib import Path
import pwd
import shlex
import socket
import sqlite3
import sys
import tempfile
import zipfile

from dotenv import load_dotenv

from kurumibot.admin import lan_host
from kurumibot.admin_config import ensure_login, save_env
from kurumibot.config import Config, DEFAULT_IMAGE, ROOT
from kurumibot.library import Library


class DeploymentError(Exception):
    pass


def initialize_library(root):
    target = root / "data/materials/library-v1"
    if target.exists():
        return target
    archive = root / "materials/library-v1.zip"
    expected = (root / "materials/library-v1.zip.sha256").read_text().split()[0]
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise DeploymentError("素材压缩包 SHA256 校验失败")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(archive) as package:
            for name in package.namelist():
                if not (staging / name).resolve().is_relative_to(staging):
                    raise DeploymentError("素材包包含非法路径")
            package.extractall(staging)
        source = root / "materials/library-v1/library.sqlite3"
        if source.exists():
            # SQLite backup 同时保留旧部署在 WAL 中的人工修改。
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old:
                with sqlite3.connect(staging / "library-v1/library.sqlite3") as new:
                    old.backup(new)
        (staging / "library-v1").rename(target)
    return target


def prepare(root, uv_bin, username):
    home = pwd.getpwnam(username).pw_dir
    if any(char in str(root) + str(uv_bin) + home for char in '\r\n";'):
        raise DeploymentError('部署路径不能包含换行、双引号或分号')
    env_file = root / ".env"
    load_dotenv(env_file, override=True)
    for name, alias in (("QQ_APP_ID", "QQBOT_APP_ID"), ("QQ_APP_SECRET", "QQBOT_CLIENT_SECRET"),
                        ("CODEX_OAUTH_PROXY_API_KEY", "CODEX_OAUTH_PROXY_API_KEY")):
        if not (os.getenv(name) or os.getenv(alias)):
            raise DeploymentError(f"请在 .env 填写 {name}")
    host = os.getenv("KURUMI_ADMIN_HOST") or lan_host()
    if not host:
        raise DeploymentError("未找到 192.168 地址，请在 .env 设置 KURUMI_ADMIN_HOST 为服务器实际私有 IPv4")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise DeploymentError("KURUMI_ADMIN_HOST 必须是私有 IPv4") from None
    if address.version != 4 or not address.is_private or address.is_unspecified:
        raise DeploymentError("KURUMI_ADMIN_HOST 必须是私有 IPv4")
    # 端口 0 检查网卡地址可绑定，不与正在运行的后台抢占 10963。
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
    except OSError:
        raise DeploymentError("无法绑定后台地址，请将 KURUMI_ADMIN_HOST 设置为服务器实际网卡地址") from None

    original = root / "materials/library-v1"
    configured = Path(os.getenv("KURUMI_LIBRARY", "materials/library-v1"))
    configured = configured if configured.is_absolute() else root / configured
    if configured.resolve() in (original.resolve(), (root / "data/materials/library-v1").resolve()):
        target = initialize_library(root)
        updates = {"KURUMI_LIBRARY": str(target.relative_to(root))}
        fallback = Path(os.getenv("KURUMI_DEFAULT_IMAGE", DEFAULT_IMAGE))
        fallback = fallback if fallback.is_absolute() else root / fallback
        if fallback.is_relative_to(original):
            updates["KURUMI_DEFAULT_IMAGE"] = str((target / fallback.relative_to(original)).relative_to(root))
        save_env(updates, env_file)
    load_dotenv(env_file, override=True)
    config = Config.from_env()
    library = Library(config.library)
    try:
        total = library.db.execute("SELECT count(*) FROM materials WHERE enabled = 1").fetchone()[0]
        if len(library.candidates()) != total:
            raise DeploymentError("存在缺失图片的已启用素材，请检查素材目录")
    finally:
        library.close()
    config.history.parent.mkdir(parents=True, exist_ok=True)
    ensure_login(env_file)
    env_file.chmod(0o600)
    replacements = {"__ROOT__": str(root), "__UV__": shlex.quote(str(Path(uv_bin).resolve())),
                    "__ENV__": shlex.quote(str(env_file)), "__USER__": username,
                    "__HOME__": home}
    template = (root / "deploy/kurumibot.conf").read_text()
    for key, value in replacements.items():
        template = template.replace(key, value.replace("%", "%%"))
    (root / "data").mkdir(exist_ok=True)
    (root / "data/kurumibot.conf").write_text(template)
    print(f"预检通过：{total} 条启用素材；管理后台 http://{host}:10963")


if __name__ == "__main__":
    try:
        prepare(ROOT, sys.argv[1], sys.argv[2])
    except Exception as exc:
        # 不打印可能含凭据的第三方异常正文。
        print(str(exc) if isinstance(exc, DeploymentError) else f"部署预检失败：{type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
