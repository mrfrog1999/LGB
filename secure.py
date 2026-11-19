"""敏感字段加密：用独立随机密钥文件对邮箱授权码等做对称加密。

设计要点：
- 密钥为随机生成的 Fernet key，保存在 data/.secret.key（文件权限 600），
  不写进源码、不进数据库、不进 Git。换个环境若没有该密钥文件，会重新生成，
  密文将无法解密（返回空），因此备份数据库时要连同 .secret.key 一起保存。
- 密文带 'enc:' 前缀，便于与历史明文区分，实现平滑迁移与兼容。
- 无 cryptography 依赖时优雅降级：明文存储但记录警告，不影响主流程。
"""
import os
import stat
import logging
from config import DATA_DIR

logger = logging.getLogger("secure")

KEY_PATH = os.path.join(DATA_DIR, ".secret.key")
PREFIX = "enc:"

_key = None
_key_error = None


def _load_or_create_key():
    """加载或创建 Fernet 密钥，返回 (key_bytes 或 None)。"""
    global _key, _key_error
    if _key is not None or _key_error is not None:
        return _key
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        _key_error = e
        logger.warning("未安装 cryptography，敏感字段将明文存储。请 pip install cryptography")
        return None
    try:
        if os.path.exists(KEY_PATH):
            with open(KEY_PATH, "rb") as f:
                key = f.read().strip()
            # 顺手收紧权限（兼容旧版本创建的宽松权限文件）
            try:
                os.chmod(KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
        else:
            key = Fernet.generate_key()
            with open(KEY_PATH, "wb") as f:
                f.write(key)
            os.chmod(KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)
        _key = key
        return key
    except Exception as e:
        _key_error = e
        logger.error(f"加载加密密钥失败，敏感字段降级为明文存储: {e}")
        return None


def _fernet():
    key = _load_or_create_key()
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key)


def encrypt(value):
    """加密字符串；已是密文或空值则原样返回。失败时降级返回原值。"""
    if value is None:
        return ""
    s = str(value)
    if not s or s.startswith(PREFIX):
        return s
    f = _fernet()
    if not f:
        return s
    try:
        token = f.encrypt(s.encode("utf-8")).decode("ascii")
        return PREFIX + token
    except Exception as e:
        logger.error(f"加密失败，降级为明文: {e}")
        return s


def decrypt(value):
    """解密字符串；明文（无 enc: 前缀）原样返回，兼容历史数据。"""
    if value is None:
        return ""
    s = str(value)
    if not s.startswith(PREFIX):
        return s  # 历史明文或空值
    f = _fernet()
    if not f:
        logger.warning("无加密密钥，密文无法解密")
        return ""
    try:
        return f.decrypt(s[len(PREFIX):].encode("ascii")).decode("utf-8")
    except Exception as e:
        logger.error(f"解密失败（密钥可能已变更）: {e}")
        return ""


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def harden_permissions():
    """收紧数据库相关文件权限为 600（仅属主可读写）。"""
    targets = [
        KEY_PATH,
        os.path.join(DATA_DIR, "longwallet.db"),
        os.path.join(DATA_DIR, "longwallet.db-wal"),
        os.path.join(DATA_DIR, "longwallet.db-shm"),
    ]
    mode = stat.S_IRUSR | stat.S_IWUSR  # 0o600
    done = []
    for p in targets:
        try:
            if os.path.exists(p):
                os.chmod(p, mode)
                done.append(os.path.basename(p))
        except OSError as e:
            logger.warning(f"无法收紧权限 {p}: {e}")
    return done
