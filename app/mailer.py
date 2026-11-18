"""邮件推送：SMTP 发送 + 日报 HTML 模板。"""
import re
import smtplib
import ssl
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from .config import settings


def short_name(name: str) -> str:
    return re.sub(r"[（(]代销建信理财[）)]", "", name or "").strip()


def send(subject: str, html: str, to: list[str] | None = None) -> None:
    recipients = to or settings.recipients
    if not recipients:
        raise RuntimeError("未配置收件人 MAIL_TO")

    sender = settings.MAIL_FROM or settings.SMTP_USER
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header("建行理财监控", "utf-8")), sender))
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    host, port = settings.SMTP_HOST, settings.SMTP_PORT
    if settings.SMTP_SSL:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, timeout=25, context=ctx) as s:
            s.login(settings.SMTP_USER, settings.SMTP_PASS)
            s.sendmail(sender, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=25) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.login(settings.SMTP_USER, settings.SMTP_PASS)
            s.sendmail(sender, recipients, msg.as_string())


def _money(v: float) -> str:
    return f"{v:,.2f}"


def build_daily_html(data: dict, test: bool = False, now: datetime | None = None) -> str:
    now = now or datetime.now()
    total = data.get("total") or {}
    products = data.get("products") or []

    cards = ""
    for p in products:
        c = p.get("calc") or {}
        latest = p.get("latest") or {}
        rate = f"{latest['rate']:.2f}%" if latest else "--"
        date_str = latest.get("date", "--")
        if c.get("days"):
            detail = (
                f'<tr><td style="padding:5px 0;color:#6b7280;font-size:12px">本金</td>'
                f'<td style="padding:5px 0;text-align:right;font-size:12px">¥{_money(c["principal"])}</td>'
                f'<td style="padding:5px 0;color:#6b7280;font-size:12px;padding-left:14px">本息 {c["days"]} 天</td>'
                f'<td style="padding:5px 0;text-align:right;font-size:12px;font-weight:600">¥{_money(c["balance"])}</td></tr>'
                f'<tr><td style="padding:5px 0;color:#6b7280;font-size:12px">累计收益</td>'
                f'<td style="padding:5px 0;text-align:right;font-size:12px;color:#0f5c4e;font-weight:700">'
                f'+¥{_money(c["interest"])}</td>'
                f'<td style="padding:5px 0;color:#6b7280;font-size:12px;padding-left:14px">日均</td>'
                f'<td style="padding:5px 0;text-align:right;font-size:12px">¥{_money(c["interest"] / c["days"])}</td></tr>'
            )
        else:
            detail = ('<tr><td colspan="4" style="padding:6px 0;font-size:12px;color:#9ca3af">'
                      '尚未设置本金，可在网页里填写</td></tr>')

        cards += f"""
        <tr><td style="padding:0 0 12px">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
            style="background:#fafbfc;border:1px solid #eceff3;border-radius:10px">
            <tr><td style="padding:14px 16px">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="font-size:13px;font-weight:600;color:#111827;line-height:1.4">{short_name(p['name'])}</td>
                  <td style="text-align:right;font-size:20px;font-weight:700;color:#0f5c4e;white-space:nowrap">{rate}</td>
                </tr>
                <tr>
                  <td colspan="2" style="font-size:11px;color:#9ca3af;padding-top:2px">
                    七日年化 · 披露日 {date_str} · {p['code']}
                  </td>
                </tr>
                <tr><td colspan="2" style="padding-top:10px">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{detail}</table>
                </td></tr>
              </table>
            </td></tr>
          </table>
        </td></tr>"""

    if not cards:
        cards = ('<tr><td style="padding:18px;text-align:center;color:#9ca3af;font-size:13px">'
                 '还没有订阅任何产品</td></tr>')

    tag = ('<span style="display:inline-block;margin-left:8px;padding:2px 8px;border-radius:999px;'
           'background:rgba(255,255,255,.18);font-size:11px">测试邮件</span>') if test else ""

    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px 12px;background:#f5f6f8;
 font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
   <tr><td align="center">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0"
      style="max-width:600px;background:#ffffff;border-radius:16px;overflow:hidden;
      box-shadow:0 6px 24px rgba(15,23,42,.08)">
     <tr><td style="background:linear-gradient(135deg,#0f5c4e,#187a67);padding:26px 28px;color:#fff">
       <div style="font-size:13px;letter-spacing:.14em;opacity:.85">建行理财 · 每日收益监控{tag}</div>
       <div style="font-size:32px;font-weight:700;margin-top:12px;letter-spacing:-.02em">
         ¥{_money(total.get('balance') or 0)}
       </div>
       <div style="font-size:13px;opacity:.92;margin-top:8px">
         合计本息（{total.get('count') or 0} 个产品） ·
         累计收益 +¥{_money(total.get('interest') or 0)} ·
         区间年化 {total.get('annualized') or 0:.2f}%
       </div>
     </td></tr>
     <tr><td style="padding:20px 28px 6px">
       {cards}
     </td></tr>
     <tr><td style="padding:6px 28px 24px">
       <div style="font-size:11px;color:#9aa3af;line-height:1.8;border-top:1px solid #eceff3;padding-top:12px">
         收益按「日收益 = 余额 × 七日年化 ÷ 365」逐日复利估算，与实际计息存在差异，仅供参考。<br>
         数据来源：中国建设银行理财产品信息披露（净值公告 PDF）。<br>
         生成时间：{now.strftime('%Y-%m-%d %H:%M:%S')}
       </div>
     </td></tr>
    </table>
   </td></tr>
  </table>
</body></html>"""
