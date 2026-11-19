"""配置模块：所有可配置项通过环境变量覆盖，默认值适配 Docker 与本地运行。"""
import os

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

# 年化预警：某只产品七日年化低于该值（%）时发邮件提醒
DEFAULT_ALERT_ANNUAL = float(os.environ.get("ALERT_ANNUAL", "1.0"))
