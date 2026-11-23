"""邮件推送：QQ/163 等 SMTP（授权码登录），HTML 日报。"""
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.header import Header
from email.utils import formataddr, parseaddr, getaddresses
from datetime import datetime
import json

import db

logger = logging.getLogger("mailer")


def build_report_html(data, update_details=None):
    """生成日报 HTML（邮件正文 + 预览页共用）。

    移动端邮箱优先：只用 table + inline 样式 + 纯色/浅色底 + 圆角，
    不用 flex / grid / 渐变 / opacity —— QQ、网易、Gmail 的手机 App 都能正确渲染。
    所有表格用百分比列宽、数字加 nowrap，窄屏不会溢出或错位。
    """
    p = data["portfolio"]
    holdings = data["holdings"]
    withdrawals = data.get("withdrawals", [])
    today = datetime.now().strftime("%Y-%m-%d")

    bank_summary = p.get("bank_summary") or {}
    profit = p["total_profit"]
    pos = profit >= 0
    big_color = "#dc2626" if pos else "#16a34a"       # 中国习惯：涨红跌绿
    soft_bg = "#fef2f2" if pos else "#f0fdf4"
    soft_border = "#fecaca" if pos else "#bbf7d0"
    label_color = "#b91c1c" if pos else "#15803d"
    hist = p.get("historical_profit", 0)
    rolling = p.get("rolling_profit", 0)
    expense = p.get("total_expense", 0)
    bank_label = {"ccb": "建行", "psbc": "邮储", "spdb": "浦发"}
    bank_values = " · ".join(
        f"{bs.get('label','')} ¥{bs.get('value',0):,.2f}" for bs in bank_summary.values()
    ) or "暂无数据"

    def _val(v):
        s = str(v if v is not None else "").strip()
        return s if s else "—"

    # ---- 三家官方七日年化：三张浅色卡片（银行名 + 小标题 + 数字） ----
    bank_cards = [
        ("ccb", "建行", "#1d4ed8", "#eff6ff", "#2563eb", "#1e3a8a"),
        ("psbc", "邮储", "#047857", "#ecfdf5", "#059669", "#065f46"),
        ("spdb", "浦发", "#6d28d9", "#f5f3ff", "#7c3aed", "#4c1d95"),
    ]

    def annual_card(key, name, name_c, bg, line_c, val_c):
        official = p.get("app_overall_annual") if key == "ccb" else p.get(f"{key}_app_overall_annual")
        official = str(official or "").strip()
        calc = (bank_summary.get(key) or {}).get("annual")
        if official:
            val = f"{official}%"
        elif calc is not None:
            val = f"{calc:.4f}%"
        else:
            val = "—"
        return (f'<td width="33.33%" align="center" bgcolor="{bg}" '
                f'style="border-radius:12px;border-top:3px solid {line_c};padding:11px 3px;">'
                f'<div style="color:{name_c};font-size:13px;font-weight:800;line-height:1.5;">{name}</div>'
                f'<div style="color:#94a3b8;font-size:10px;line-height:1.5;">七日年化</div>'
                f'<div style="color:{val_c};font-size:20px;font-weight:800;line-height:1.4;'
                f'white-space:nowrap;margin-top:3px;">{val}</div></td>')

    banks_html = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="6" border="0" style="margin-bottom:14px;"><tr>'
                  + annual_card(*bank_cards[0])
                  + annual_card(*bank_cards[1])
                  + annual_card(*bank_cards[2])
                  + "</tr></table>")

    # ---- 四个指标（2×2 浅灰圆角卡） ----
    def metric_cell(label, value, color="#1e293b"):
        return (f'<td width="50%" align="center" bgcolor="#f8fafc" style="border-radius:10px;padding:10px 4px;">'
                f'<div style="color:#94a3b8;font-size:11px;line-height:1.6;">{label}</div>'
                f'<div style="color:{color};font-size:15px;font-weight:800;line-height:1.5;'
                f'white-space:nowrap;margin-top:2px;">{value}</div></td>')

    metrics_html = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="6" border="0" style="margin-bottom:14px;">'
                    + "<tr>"
                    + metric_cell("总本金", f"¥{p['total_principal']:,.2f}")
                    + metric_cell("当前市值", f"¥{p['total_value']:,.2f}")
                    + "</tr><tr>"
                    + metric_cell("累计收益率", f"{'+' if p['profit_rate'] >= 0 else ''}{p['profit_rate']:.4f}%", big_color)
                    + metric_cell("累计支出", f"¥{expense:,.2f}")
                    + "</tr></table>")
    if p.get("pending_capital", 0) > 0:
        metrics_html += (f'<div style="color:#b45309;font-size:12px;line-height:1.8;margin:-4px 0 12px;">'
                         f'待入账本金 +¥{p["pending_capital"]:,.2f}（T+1 入账，当天不计息，次日起计息）</div>')

    # ---- 持仓明细 ----
    rows = ""
    for h in holdings:
        rate = h.get("profit_rate") or 0
        color = "#dc2626" if rate >= 0 else "#16a34a"
        bank = h.get("bank") or "ccb"
        status_txt = ""
        if h.get("status") == "pending":
            status_txt = " · 待入账"
        elif h.get("status") == "settled":
            status_txt = " · 已入账待起息"
        extra = ""
        if h.get("pending_capital", 0) > 0:
            extra += f'<div style="color:#b45309;font-size:11px;line-height:1.6;">待入账 +¥{h["pending_capital"]:,.2f}</div>'
        if h.get("additions_total", 0) > 0:
            extra += f'<div style="color:#b45309;font-size:11px;line-height:1.6;">含追加 +¥{h["additions_total"]:,.2f}</div>'
        if h.get("total_expense", 0) > 0:
            extra += f'<div style="color:#b45309;font-size:11px;line-height:1.6;">含支出 −¥{h["total_expense"]:,.2f}</div>'
        annual = "—" if h.get("latest_annual") is None else f"{h['latest_annual']:.4f}%"
        cum = h.get("cum_profit") or 0
        rows += (
            "<tr>"
            f'<td width="50%" style="padding:10px 4px;border-bottom:1px solid #eef2f7;">'
            f'<div style="color:#1e293b;font-size:13px;font-weight:700;line-height:1.5;">{h["product_name"]}</div>'
            f'<div style="color:#94a3b8;font-size:11px;line-height:1.6;">{bank_label.get(bank, "建行")} · {h.get("product_code", "")}{status_txt}</div>'
            f'<div style="color:#64748b;font-size:11px;line-height:1.6;">本金 ¥{h["principal"]:,.2f}</div>'
            f"{extra}</td>"
            f'<td width="30%" align="right" style="padding:10px 4px;border-bottom:1px solid #eef2f7;color:#1e293b;font-size:13px;font-weight:700;white-space:nowrap;">¥{h["current_value"]:,.2f}'
            f'<div style="color:{color};font-size:11px;font-weight:700;white-space:nowrap;">{"+" if cum >= 0 else ""}¥{cum:,.2f}</div></td>'
            f'<td width="20%" align="right" style="padding:10px 4px;border-bottom:1px solid #eef2f7;color:#475569;font-size:12px;white-space:nowrap;">{annual}</td>'
            "</tr>"
        )

    # ---- 支出记录 ----
    expense_html = ""
    if withdrawals:
        wrows = ""
        for w in withdrawals:
            wrows += (
                "<tr>"
                f'<td style="padding:7px 6px;border-bottom:1px solid #eef2f7;color:#64748b;font-size:12px;white-space:nowrap;">{w["expense_date"]}</td>'
                f'<td style="padding:7px 6px;border-bottom:1px solid #eef2f7;color:#64748b;font-size:12px;">{w.get("scope_label") or "-"}</td>'
                f'<td align="right" style="padding:7px 6px;border-bottom:1px solid #eef2f7;color:#dc2626;font-size:12px;font-weight:700;white-space:nowrap;">−¥{w["amount"]:,.2f}</td>'
                f'<td style="padding:7px 6px;border-bottom:1px solid #eef2f7;color:#94a3b8;font-size:11px;">{w.get("note") or "-"}</td>'
                "</tr>"
            )
        expense_html = (
            f'<div style="color:#334155;font-size:13px;font-weight:800;margin:18px 0 6px;">'
            f"本金支出记录（累计 −¥{expense:,.2f}）</div>"
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            '<tr bgcolor="#f1f5f9">'
            '<td style="padding:6px;color:#64748b;font-size:11px;border-radius:6px 0 0 6px;">日期</td>'
            '<td style="padding:6px;color:#64748b;font-size:11px;">扣减范围</td>'
            '<td align="right" style="padding:6px;color:#64748b;font-size:11px;">金额</td>'
            '<td style="padding:6px;color:#64748b;font-size:11px;border-radius:0 6px 6px 0;">备注</td>'
            "</tr>"
            f"{wrows}</table>"
        )

    # ---- 更新明细 ----
    detail_html = ""
    if update_details:
        items = "".join(
            f'<div style="color:#475569;font-size:12px;line-height:1.9;">· {d}</div>' for d in update_details
        )
        detail_html = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:14px;">'
            '<tr><td bgcolor="#f8fafc" style="border-radius:10px;padding:10px 12px;">'
            '<div style="color:#334155;font-size:12px;font-weight:800;margin-bottom:4px;">本次数据更新</div>'
            f"{items}</td></tr></table>"
        )

    # ---- App 昨日收益：全部为空时显示友好提示 ----
    app_parts = [p.get("app_yesterday_profit"), p.get("psbc_app_yesterday_profit"), p.get("spdb_app_yesterday_profit")]
    if any(str(x if x is not None else "").strip() for x in app_parts):
        app_line = (f'App 昨日收益（{_val(p.get("app_ref_date"))}）：'
                    f'建行 ¥{_val(p.get("app_yesterday_profit"))} · '
                    f'邮储 ¥{_val(p.get("psbc_app_yesterday_profit"))} · '
                    f'浦发 ¥{_val(p.get("spdb_app_yesterday_profit"))}')
    else:
        app_line = "App 昨日收益：暂无参考数据（可在设置页填写）"

    html = f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f6fb;">
  <tr><td align="center" style="padding:14px 10px 30px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;font-family:-apple-system,BlinkMacSystemFont,'Microsoft YaHei',Arial,sans-serif;">
      <tr><td bgcolor="#4338ca" style="border-radius:14px 14px 0 0;padding:18px 18px 16px;">
        <div style="color:#ffffff;font-size:20px;font-weight:800;line-height:1.4;">现金理财 · 每日收益</div>
        <div style="color:#c7d2fe;font-size:12px;line-height:1.7;margin-top:5px;">建行 · 邮储 · 浦发 净值监控　·　{today}</div>
      </td></tr>
      <tr><td bgcolor="#ffffff" style="border-radius:0 0 14px 14px;padding:16px 14px 6px;">
        {banks_html}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:14px;"><tr><td bgcolor="{soft_bg}" style="border:1px solid {soft_border};border-radius:12px;padding:13px 15px;">
          <div style="color:{label_color};font-size:12px;line-height:1.6;">我的收益估算（三家合计）</div>
          <div style="color:{big_color};font-size:28px;font-weight:800;line-height:1.4;margin:3px 0 5px;white-space:nowrap;">{'+' if profit >= 0 else ''}¥{profit:,.2f}</div>
          <div style="color:#64748b;font-size:11px;line-height:1.9;">
            历史基准 ¥{hist:,.2f}（建行 ¥{p.get('historical_ccb', 0):,.2f} · 邮储 ¥{p.get('historical_psbc', 0):,.2f} · 浦发 ¥{p.get('historical_spdb', 0):,.2f}）<br>
            监控后滚动 ¥{rolling:,.2f} · 程序估算整体年化 {(p.get('overall_annual') or 0):.4f}%
          </div>
        </td></tr></table>
        {metrics_html}
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:14px;"><tr><td bgcolor="#fffbeb" style="border-left:3px solid #f5b301;border-radius:0 10px 10px 0;padding:10px 12px;">
          <div style="color:#92400e;font-size:12px;line-height:1.9;">{app_line}<br>分银行市值：{bank_values}</div>
        </td></tr></table>
        <div style="color:#334155;font-size:13px;font-weight:800;margin-bottom:6px;">持仓明细（{len(holdings)} 笔）</div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr bgcolor="#f1f5f9">
            <td style="padding:7px 6px;color:#64748b;font-size:11px;border-radius:6px 0 0 6px;">产品 / 本金</td>
            <td align="right" style="padding:7px 6px;color:#64748b;font-size:11px;">市值 / 累计收益</td>
            <td align="right" style="padding:7px 6px;color:#64748b;font-size:11px;border-radius:0 6px 6px 0;">年化</td>
          </tr>
          {rows}
        </table>
        {expense_html}
        {detail_html}
        <div style="color:#94a3b8;font-size:11px;line-height:1.9;padding:14px 0 12px;border-top:1px solid #eef2f7;margin-top:16px;">
          收益按每万份收益逐日复利估算；本金支出：建行按该行全部产品平均分摊，邮储 / 浦发从所选那只产品扣减。<br>
          综合年化优先采用各行 App 官方值，收益为估算值，仅供参考，实际以银行账单为准。
        </div>
      </td></tr>
    </table>
  </td></tr>
</table>"""
    return html


def build_report_page(data, update_details=None):
    """预览页：把邮件 HTML 包成完整网页（补齐 charset / viewport / 移动端底色）。"""
    body = build_report_html(data, update_details)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="format-detection" content="telephone=no,email=no,address=no">
<title>日报预览 · 现金理财监控</title>
<style>
  html,body{{margin:0;padding:0;background:#e8ecf3;-webkit-text-size-adjust:100%;}}
  img{{max-width:100%;height:auto;}}
  @media (max-width:560px){{
    body{{background:#f4f6fb;}}
  }}
</style>
</head><body>{body}</body></html>"""


def _smtp_send(subject, html, override_to=None, attachments=None):
    """统一的 SMTP 发送。返回 (是否成功, 消息)。
    attachments: 可选，[(文件名, bytes)] 列表，用于携带备份文件等附件。
    """
    s = db.get_settings()
    host = s.get("smtp_host", "smtp.qq.com")
    port = int(s.get("smtp_port", "465") or 465)
    user = s.get("smtp_user", "")
    password = s.get("smtp_pass", "")
    mail_to = override_to or s.get("mail_to", "")

    if not user or not password or not mail_to:
        return False, "SMTP账号/密码或收件人未配置"

    # 发件人显示名可能是中文（如"理财监控小程序"），邮件头必须是 ASCII，
    # 用 formataddr 做 RFC2047 编码 -> "=?utf-8?...?= <user@qq.com>"。
    # 真正投递用的 envelope sender 必须是纯邮箱账号，不能用显示名。
    display_name = (s.get("mail_from") or "").strip() or "现金理财监控"
    from_header = formataddr((display_name, user))
    # 收件人同样可能带中文显示名，逐个编码；多个用逗号分隔。
    to_pairs = [(name or "", addr) for name, addr in getaddresses([mail_to]) if addr]
    if not to_pairs:
        to_pairs = [("", x.strip()) for x in mail_to.split(",") if x.strip()]
    to_header = ", ".join(formataddr((n, a)) for n, a in to_pairs)
    to_addrs = [a for _, a in to_pairs]

    # 有附件时用 mixed 容器（正文仍是 alternative 子部分），无附件时保持原结构
    if attachments:
        msg = MIMEMultipart("mixed")
        body = MIMEMultipart("alternative")
        body.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(body)
        for filename, content in attachments:
            part = MIMEBase("application", "octet-stream")
            if isinstance(content, str):
                content = content.encode("utf-8")
            part.set_payload(content)
            encoders.encode_base64(part)
            # 文件名用 RFC2231 编码，保证中文名在邮件客户端正常显示
            part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", filename))
            msg.attach(part)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(html, "html", "utf-8"))
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = from_header
    msg["To"] = to_header

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.starttls()
        with server:
            server.login(user, password)
            server.sendmail(user, to_addrs, msg.as_string())
        logger.info(f"邮件已发送至 {mail_to}")
        return True, "发送成功"
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False, str(e)


def report_subject(data):
    """日报标题：三家七日年化一行展示（官方值优先，为空则回退到自动计算值）。"""
    p = data.get("portfolio", {})
    bank_summary = p.get("bank_summary") or {}

    def val(official_key, bank):
        # 与正文 annual_card 保持一致：官方 App 年化优先，为空则用自动计算值，都没有才"未填"
        official = str(p.get(official_key) or "").strip()
        if official:
            return f"{official}%"
        calc = (bank_summary.get(bank) or {}).get("annual")
        if calc is not None:
            return f"{calc:.4f}%"
        return "未填"

    return ("建行·{ccb} -|- 邮储·{psbc} -|- 浦发·{spdb}"
            .format(ccb=val("app_overall_annual", "ccb"),
                    psbc=val("psbc_app_overall_annual", "psbc"),
                    spdb=val("spdb_app_overall_annual", "spdb")))


def send_report(data, update_details=None, override_to=None):
    """发送日报。返回 (是否成功, 消息)。"""
    html = build_report_html(data, update_details)
    return _smtp_send(report_subject(data), html, override_to)


def build_alert_html(data, low, threshold):
    """年化预警邮件正文。"""
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    p = data["portfolio"]
    rows = ""
    for h in low:
        rows += f"""
        <tr style="border-top:1px solid #f3e0e0;">
          <td style="padding:9px 10px;"><b>{h['product_name']}</b>
            <div style="font-size:11px;color:#999060;color:#999;">{h.get('product_code','')}</div></td>
          <td style="padding:9px 10px;">{h.get('bank_label','')}</td>
          <td style="padding:9px 10px;text-align:right;color:#c0392b;font-weight:700;">{h['latest_annual']:.4f}%</td>
          <td style="padding:9px 10px;text-align:right;color:#b45309;">低于 {h['gap']:.4f}pp</td>
          <td style="padding:9px 10px;text-align:right;">¥{h.get('current_value') or 0:,.2f}</td>
        </tr>"""
    html = f"""
    <div style="font-family:-apple-system,'Microsoft YaHei',Arial,sans-serif;max-width:720px;margin:0 auto;color:#333;">
      <div style="background:linear-gradient(135deg,#b45309,#d97706);color:#fff;padding:20px 24px;border-radius:8px 8px 0 0;">
        <div style="font-size:22px;font-weight:800;">⚠️ 年化收益预警 · {len(low)} 只产品低于 {threshold:.2f}%</div>
        <div style="opacity:.9;margin-top:6px;font-size:13px;">现金理财监控 · {today}</div>
      </div>
      <div style="padding:20px 24px;background:#fff;border:1px solid #eee;border-top:none;border-radius:0 0 8px 8px;">
        <div style="background:#fffbeb;border-left:3px solid #f59e0b;padding:11px 14px;border-radius:4px;font-size:13px;color:#92400e;line-height:1.8;margin-bottom:16px;">
          下列产品的七日年化已跌破你设置的预警值 <b>{threshold:.2f}%</b>，建议评估是否继续持有或调仓。<br>
          当前整体七日年化 <b>{(p.get('overall_annual') or 0):.4f}%</b> · 持仓市值 <b>¥{p.get('total_value', 0):,.2f}</b>
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead><tr style="background:#fef3c7;color:#78350f;text-align:left;">
            <th style="padding:9px 10px;">产品</th>
            <th style="padding:9px 10px;">银行</th>
            <th style="padding:9px 10px;text-align:right;">七日年化</th>
            <th style="padding:9px 10px;text-align:right;">差距</th>
            <th style="padding:9px 10px;text-align:right;">当前市值</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
        <div style="margin-top:20px;padding-top:14px;border-top:1px solid #eee;color:#aaa;font-size:12px;line-height:1.7;">
          预警阈值可在「设置 → 年化预警」中调整；只想关掉提醒不看数据时，把开关设为「关闭」。<br>
          七日年化取自各行最新一期净值公告，仅供参考。
        </div>
      </div>
    </div>"""
    return html


def send_alert(data, low, threshold, override_to=None):
    """发送年化预警邮件。返回 (是否成功, 消息)。"""
    today = datetime.now().strftime("%Y-%m-%d")
    html = build_alert_html(data, low, threshold)
    return _smtp_send(f"⚠️ 年化预警：{len(low)} 只产品低于 {threshold:.2f}%（{today}）", html, override_to)


def send_backup(payload, override_to=None, test=False):
    """把数据备份作为附件发送到邮箱。返回 (是否成功, 消息)。

    payload: db.export_all() 返回的备份数据字典。
    test: True 时主题标注【测试】，用于设置页的"立即发送"验证。
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y%m%d_%H%M")
    filename = f"理财监控备份_{stamp}.json"
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    tag = "【测试】" if test else ""
    subject = f"{tag}现金理财监控数据备份（{today}）"

    tables = payload.get("tables", {}) if isinstance(payload, dict) else {}
    parts = []
    for key, label in (("holdings", "持仓"), ("yields", "净值"), ("withdrawals", "支出"),
                       ("products", "产品"), ("users", "用户")):
        rows = tables.get(key)
        if isinstance(rows, list):
            parts.append(f"{label} {len(rows)} 条")
    stat = " · ".join(parts) if parts else "完整数据"
    exported = payload.get("exported_at", today) if isinstance(payload, dict) else today

    html = f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      max-width:560px;margin:0 auto;color:#1e293b;line-height:1.7;">
      <div style="background:linear-gradient(135deg,#ef4444,#dc2626);color:#fff;border-radius:14px;
        padding:22px 24px;margin-bottom:18px;">
        <div style="font-size:19px;font-weight:750;">💾 现金理财监控 · 自动备份</div>
        <div style="font-size:12.5px;opacity:.9;margin-top:5px;">{today} · 数据文件已附在邮件中</div>
      </div>
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:18px 20px;font-size:13.5px;">
        <div style="margin-bottom:8px;">你好，这是系统按计划自动导出的<b>数据备份</b>，请查收附件：</div>
        <div style="background:#fff;border:1px dashed #cbd5e1;border-radius:9px;padding:12px 14px;margin:10px 0;">
          <div style="font-weight:700;color:#dc2626;">📎 {filename}</div>
          <div style="color:#64748b;font-size:12px;margin-top:4px;">{stat} · 备份时间 {exported}</div>
        </div>
        <div style="color:#16a34a;font-size:12px;margin-top:12px;">
          ✅ 本备份已自动剔除邮箱账号/授权码、用户账号等敏感信息，仅含持仓、净值、收益等业务数据，可安全留存。
        </div>
      </div>
      <div style="text-align:center;color:#94a3b8;font-size:11px;margin-top:16px;">
        此邮件由现金理财监控系统自动发送 · {tag or '每日定时备份'}
      </div>
    </div>"""
    return _smtp_send(subject, html, override_to, attachments=[(filename, content)])


def test_connection():
    """测试 SMTP 连接。"""
    s = db.get_settings()
    host = s.get("smtp_host", "smtp.qq.com")
    port = int(s.get("smtp_port", "465") or 465)
    user = s.get("smtp_user", "")
    password = s.get("smtp_pass", "")
    if not user or not password:
        return False, "请先填写SMTP账号和授权码"
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls()
        with server:
            server.login(user, password)
        return True, "连接成功"
    except Exception as e:
        return False, str(e)


# ---------------- 定期存款到期提醒（定期监控）----------------
def send_raw_mail(subject, html, to=None):
    """发送任意主题的 HTML 邮件（定期监控到期提醒用），返回 (ok, msg)。"""
    return _smtp_send(subject, html, override_to=to)


def _fmt_deposit_term(months):
    """把存期月数格式化成「1年 / 3个月」这样的简短文本。"""
    try:
        m = int(months)
    except (TypeError, ValueError):
        return ""
    if not m:
        return ""
    if m % 12 == 0:
        return f"{m // 12}年"
    return f"{m}个月"


def build_fixed_notice_html(due_today, due_tomorrow):
    """构造定期存款到期提醒正文（含完整存单明细）。due_today/due_tomorrow 为存单 dict 列表。"""
    def _num(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    def _rate(v):
        n = _num(v)
        return f"{n:g}" if n else "—"

    def cards(items, accent, soft_bg):
        """每笔存单渲染成一张明细卡，字段与台账一致，适配手机窄屏。"""
        total_p = 0.0
        total_i = 0.0
        out = []
        for d in items:
            p = _num(d.get("principal"))
            i = _num(d.get("interest"))
            total_p += p
            total_i += i
            who = (d.get("owner_name") or "").strip()
            who_html = (
                f'<span style="font-weight:500;color:#64748b;font-size:11.5px;">　{who}</span>'
                if who else ""
            )
            term = _fmt_deposit_term(d.get("term_months"))
            meta = (
                f'存入 {d.get("save_date", "") or "—"}　·　到期 {d.get("expire_date", "") or "—"}'
                + (f'　·　存期 {term}' if term else "")
            )
            money = (
                f'年利率 {_rate(d.get("rate_percent"))}%　·　预估利息 ¥{i:,.2f}'
            )
            remark = (d.get("remark") or "").strip()
            remark_html = (
                f'<div style="font-size:11px;color:#94a3b8;margin-top:5px;">备注：{remark}</div>'
                if remark else ""
            )
            out.append(
                f'<div style="border:1px solid #eef1f6;border-radius:9px;padding:10px 12px;margin-bottom:8px;background:#fff;">'
                f'<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">'
                f'<div style="font-size:13.5px;font-weight:700;color:#1e293b;">{d.get("bank_name", "")}{who_html}</div>'
                f'<div style="font-size:14px;font-weight:800;color:{accent};white-space:nowrap;">¥{p:,.2f}</div>'
                f'</div>'
                f'<div style="font-size:11.5px;color:#64748b;margin-top:7px;line-height:1.7;">{meta}<br>{money}</div>'
                f'{remark_html}'
                f'</div>'
            )
        return "".join(out), total_p, total_i

    today_cards, today_p, today_i = cards(due_today, "#dc2626", "#fef2f2")
    tomorrow_cards, tomorrow_p, tomorrow_i = cards(due_tomorrow, "#ea580c", "#fff7ed")

    table = (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;'
        'max-width:560px;margin:0 auto;color:#1e293b;">'
        '<div style="background:linear-gradient(135deg,#ef4444,#fb7137);border-radius:14px 14px 0 0;'
        'padding:20px 22px;color:#fff;font-size:19px;font-weight:800;">定期存款到期提醒</div>'
        '<div style="background:#fff;border:1px solid #e9edf4;border-top:none;border-radius:0 0 14px 14px;padding:18px 22px;">'
    )
    if due_today:
        table += (
            '<div style="font-size:15px;font-weight:700;color:#dc2626;margin:6px 0 4px;">🔴 到期当天 · '
            f'{len(due_today)} 笔</div>'
            f'<div style="font-size:12px;color:#991b1b;margin:0 0 10px;">本金合计 ¥{today_p:,.2f}　·　预估利息 ¥{today_i:,.2f}</div>'
            '<div style="font-size:12px;color:#991b1b;margin:-4px 0 10px;">以下存单今天到期，请及时办理支取或转存。</div>'
            + today_cards
        )
    if due_tomorrow:
        table += (
            '<div style="font-size:15px;font-weight:700;color:#ea580c;margin:16px 0 4px;">🟠 到期前一天 · '
            f'{len(due_tomorrow)} 笔</div>'
            f'<div style="font-size:12px;color:#9a3412;margin:0 0 10px;">本金合计 ¥{tomorrow_p:,.2f}　·　预估利息 ¥{tomorrow_i:,.2f}</div>'
            '<div style="font-size:12px;color:#9a3412;margin:-4px 0 10px;">以下存单明天到期，请提前做好安排。</div>'
            + tomorrow_cards
        )
    if not due_today and not due_tomorrow:
        table += '<div style="font-size:14px;color:#64748b;padding:12px 0;">到期当天与前一天均没有到期的定期存款。</div>'
    table += ('<div style="font-size:11px;color:#94a3b8;margin-top:16px;border-top:1px solid #eef1f6;'
              'padding-top:12px;line-height:1.7;">每笔存单会在<b>到期前一天</b>与<b>到期当天</b>各提醒一次。<br>'
              '来自 现金理财监控 · 定期监控</div></div></div>')
    return table


def send_fixed_notice():
    """每日定时检查存单，在到期前一天与到期当天各发一次提醒邮件（同一天只发一次）。返回 (ok, msg)。"""
    from datetime import datetime as _dt, timedelta as _td
    s = db.get_settings()
    if s.get("fixed_notice_enabled", "0") != "1":
        return True, "定期提醒未开启，跳过"
    today = _dt.now().date()
    last = s.get("fixed_notice_last_date", "")
    if last == today.isoformat():
        return True, "今日已发送，跳过"
    # due_today：到期日=今天，即「到期当天」；due_tomorrow：到期日=明天，即「到期前一天」
    due_today = db.fixed_deposits_expiring_on([today.isoformat()])
    tomorrow = (today + _td(days=1)).isoformat()
    due_tomorrow = db.fixed_deposits_expiring_on([tomorrow])
    # 无到期存单时不发，避免每天骚扰
    if not due_today and not due_tomorrow:
        return True, "到期当天与前一天均无到期存单，跳过"
    subject = f"定期存款到期提醒 · 到期当天 {len(due_today)} 笔 / 到期前一天 {len(due_tomorrow)} 笔"
    html = build_fixed_notice_html(due_today, due_tomorrow)
    to = (s.get("fixed_notice_email") or "").strip() or (s.get("mail_to") or "").strip()
    ok, msg = _smtp_send(subject, html, override_to=to or None)
    if ok:
        db.set_settings({"fixed_notice_last_date": today.isoformat()})
    return ok, msg
