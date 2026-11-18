"""运行配置：全部来自环境变量，便于 Docker 部署时通过 .env 覆盖。"""
import os
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        return default


class Settings:
    # ---- 首次启动自动订阅的产品（可在网页里增删）----
    BOOTSTRAP_CODE: str = os.getenv("BOOTSTRAP_CODE", "JXJXBGS2023060204").strip()
    BOOTSTRAP_NAME: str = os.getenv("BOOTSTRAP_NAME", "建信宝按日开放式产品第4期（代销建信理财)").strip()
    SEARCH_KEYWORD: str = os.getenv("SEARCH_KEYWORD", "建信宝按日开放式").strip()

    # ---- 存储 ----
    DB_PATH: str = os.getenv("DB_PATH", "/data/data.db").strip()
    LOG_ROWS: int = _int("LOG_ROWS", 500)

    # ---- 时区与调度 ----
    TZ: str = os.getenv("TZ", "Asia/Shanghai").strip()
    FETCH_TIME: str = os.getenv("FETCH_TIME", "09:10").strip()   # HH:MM
    FETCH_ON_START: bool = _bool("FETCH_ON_START", True)

    # ---- 抓取 ----
    HTTP_TIMEOUT: int = _int("HTTP_TIMEOUT", 30)
    FETCH_RETRIES: int = _int("FETCH_RETRIES", 3)

    # ---- 邮件 ----
    MAIL_ENABLED: bool = _bool("MAIL_ENABLED", False)
    SMTP_HOST: str = os.getenv("SMTP_HOST", "").strip()
    SMTP_PORT: int = _int("SMTP_PORT", 465)
    SMTP_SSL: bool = _bool("SMTP_SSL", True)
    SMTP_USER: str = os.getenv("SMTP_USER", "").strip()
    SMTP_PASS: str = os.getenv("SMTP_PASS", "").strip()
    MAIL_FROM: str = os.getenv("MAIL_FROM", "").strip()
    MAIL_TO: str = os.getenv("MAIL_TO", "").strip()
    MAIL_SUBJECT_PREFIX: str = os.getenv("MAIL_SUBJECT_PREFIX", "建行理财收益日报").strip()

    # ---- 默认本金（新增产品时预填，可在网页里改）----
    DEFAULT_PRINCIPAL: float = _float("DEFAULT_PRINCIPAL", 100000.0)
    DEFAULT_START_DATE: str = os.getenv("DEFAULT_START_DATE", "").strip()

    # ---- Web ----
    WEB_PORT: int = _int("WEB_PORT", 8080)

    @property
    def recipients(self) -> list[str]:
        return [x.strip() for x in self.MAIL_TO.replace(";", ",").split(",") if x.strip()]

    @property
    def mail_ready(self) -> bool:
        return bool(self.MAIL_ENABLED and self.SMTP_HOST and self.SMTP_USER
                    and self.SMTP_PASS and (self.MAIL_FROM or self.SMTP_USER)
                    and self.recipients)

    @property
    def fetch_hour_minute(self) -> tuple[int, int]:
        try:
            hh, mm = self.FETCH_TIME.split(":")
            return max(0, min(23, int(hh))), max(0, min(59, int(mm)))
        except Exception:
            return 9, 10

    def ensure_dirs(self) -> None:
        Path(self.DB_PATH).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
