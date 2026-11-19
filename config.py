"""配置模块：所有可配置项通过环境变量覆盖，默认值适配 Docker 与本地运行。"""
import os

# 应用版本号：唯一来源，由 bump_version.py 在每次推送 GitHub 前自动递增
APP_VERSION = "v1.0.8"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "longwallet.db")

# Web 服务
PORT = int(os.environ.get("PORT", "8866"))
HOST = "0.0.0.0"
SECRET_KEY = os.environ.get("SECRET_KEY", "longwallet-monitor-secret-key")

# 建行数据源
CCB_LIST_PAGE = "https://ccb.com/chn/home/finance/index/cpgg/cpcxqgg/index.shtml"
CCB_SEARCH_URL = (
    "https://ccb.com/tran/WCCMainPlatV5?CCB_IBSVersion=V5&SERVLET_NAME=WCCMainPlatV5"
    "&isAjaxRequest=true&TXCODE=NLCQ88&Mnplt_Tp_Ind=1"
)
CCB_DETAIL_URL = (
    "https://ccb.com/tran/WCCMainPlatV5?CCB_IBSVersion=V5&SERVLET_NAME=WCCMainPlatV5"
    "&TXCODE=NLCQ89&Itt_Fcn_Dsc="
)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 邮储数据源（s.psbc.com 的 TLS 与站点证书兼容问题，统一用 curl 子进程请求）
PSBC_SEARCH_URL = "https://s.psbc.com/portal/PsbcService/finquick/"
PSBC_SITE_URL = "https://www.psbc.com/cn/grfw/tzlc/lc/"

# 浦银理财（浦发）数据源：产品列表 + 份额净值都走同一个 JSON 接口
SPDB_SEARCH_URL = "https://www.spdb-wm.com/api/search"
SPDB_SITE_URL = "https://www.spdb-wm.com/financialProducts/"

# 定时推送默认时间
DEFAULT_PUSH_HOUR = int(os.environ.get("PUSH_HOUR", "9"))
DEFAULT_PUSH_MINUTE = int(os.environ.get("PUSH_MINUTE", "0"))

# 下午补充抓取时间（仅更新数据，不发邮件）——作为 fetch_times 未配置时的兜底默认
SECOND_FETCH_HOUR = int(os.environ.get("SECOND_FETCH_HOUR", "14"))
SECOND_FETCH_MINUTE = int(os.environ.get("SECOND_FETCH_MINUTE", "0"))

# 自动抓取时间点列表（可配置多个，仅抓数据不发邮件）。
# 真正生效值存数据库 settings.fetch_times（JSON 数组），此处仅作初始默认。
DEFAULT_FETCH_TIMES = [f"{SECOND_FETCH_HOUR:02d}:{SECOND_FETCH_MINUTE:02d}"]

# 每日自动备份并发送邮箱（默认下午 16:00，默认关闭，由用户在设置页开启）
DEFAULT_BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "16"))
DEFAULT_BACKUP_MINUTE = int(os.environ.get("BACKUP_MINUTE", "0"))

# 年化预警：某只产品七日年化低于该值（%）时发邮件提醒
DEFAULT_ALERT_ANNUAL = float(os.environ.get("ALERT_ANNUAL", "1.0"))

# 访问码兜底值：部分部署网关要求请求携带 sharecode。
# 正常访问由前端从 URL 参数读取；若预览面板等场景丢失参数，可在此配置兜底值。
# 默认留空，源码不内置任何具体凭证，需要时由部署环境通过环境变量提供。
SHARECODE_FALLBACK = os.environ.get("SHARECODE", "")
