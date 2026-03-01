import logging
import smtplib
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

log = logging.getLogger("alarmfw.notifier.smtp")

_STATUS_COLOR = {
    "OK":      "#27ae60",
    "PROBLEM": "#e74c3c",
    "ERROR":   "#e67e22",
}
_SEVERITY_COLOR = {
    "CRITICAL": "#c0392b",
    "HIGH":     "#e74c3c",
    "WARN":     "#e67e22",
    "INFO":     "#2980b9",
}

_POD_COLUMNS = ["pod", "ready_str", "waiting", "restarts", "created_at", "node", "image", "workload"]
_POD_HEADERS = ["NAME", "READY", "STATUS", "RESTARTS", "CREATED (+3)", "NODE", "IMAGE TAG", "WORKLOAD"]


def _pod_table_html(pods: List[Dict[str, Any]]) -> str:
    if not pods:
        return ""

    header_cells = "".join(
        f'<th style="background:#2c3e50;color:#fff;padding:8px 12px;text-align:left;font-size:12px;white-space:nowrap;">{h}</th>'
        for h in _POD_HEADERS
    )

    rows = ""
    for i, pod in enumerate(pods):
        bg = "#f9f9f9" if i % 2 == 0 else "#ffffff"
        status = pod.get("waiting") or pod.get("terminated") or pod.get("phase") or "-"
        status = "-" if status in ("-", "") else status
        rs = pod.get("workload", "-")
        if "/" in rs:
            rs = rs.split("/", 1)[-1]

        values = [
            pod.get("pod", "-"),
            pod.get("ready_str", "-"),
            status,
            str(pod.get("restarts", "-")),
            pod.get("created_at", "-"),
            pod.get("node", "-"),
            pod.get("image", "-"),
            rs,
        ]
        cells = "".join(
            f'<td style="padding:7px 12px;font-size:12px;border-bottom:1px solid #eee;white-space:nowrap;">{v}</td>'
            for v in values
        )
        rows += f'<tr style="background:{bg};">{cells}</tr>'

    return f"""
    <h3 style="margin:24px 0 8px;color:#2c3e50;font-size:14px;">Problematic Pods</h3>
    <div style="overflow-x:auto;">
      <table style="border-collapse:collapse;width:100%;font-family:monospace;">
        <thead><tr>{header_cells}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    """


def _delta_html(delta: Dict[str, Any]) -> str:
    if not delta:
        return ""

    items = ""
    for pod in delta.get("new_pods") or []:
        items += f'<li style="color:#e74c3c;margin:3px 0;">&#43; Yeni problem pod: <b>{pod}</b></li>'
    for pod in delta.get("recovered_pods") or []:
        items += f'<li style="color:#27ae60;margin:3px 0;">&#10003; İyileşen pod: <b>{pod}</b></li>'
    for r in delta.get("restart_increases") or []:
        items += (
            f'<li style="color:#e67e22;margin:3px 0;">&#8593; Restart artışı: <b>{r["pod"]}</b>'
            f'&nbsp;&nbsp;({r["from"]} &rarr; {r["to"]})</li>'
        )

    if not items:
        return ""

    return f"""
    <div style="background:#fff8e1;border-left:4px solid #f39c12;border-radius:4px;padding:12px 16px;margin-bottom:20px;">
      <div style="font-size:13px;font-weight:bold;color:#b7770d;margin-bottom:6px;">Değişiklikler</div>
      <ul style="margin:0;padding-left:18px;font-size:13px;font-family:monospace;">{items}</ul>
    </div>
    """


def _build_html(payload: Dict[str, Any]) -> str:
    status   = payload.get("status", "UNKNOWN")
    severity = payload.get("severity", "")
    alarm    = payload.get("alarm_name", "")
    ts       = payload.get("timestamp_utc", "")

    status_color   = _STATUS_COLOR.get(status, "#7f8c8d")
    severity_color = _SEVERITY_COLOR.get(severity, "#7f8c8d")

    evidence = payload.get("evidence") or {}
    pods: List[Dict[str, Any]] = evidence.get("pods") or []
    pod_count = evidence.get("count", len(pods))
    cluster   = evidence.get("cluster", "")
    namespace = evidence.get("namespace", "")
    delta     = evidence.get("delta") or {}

    info_rows = ""
    for label, value in [
        ("Alarm", alarm),
        ("Cluster", cluster),
        ("Namespace", namespace),
        ("Problematic Pods", str(pod_count) if pods else ""),
        ("Timestamp", ts),
    ]:
        if value:
            info_rows += f"""
            <tr>
              <td style="padding:6px 12px;font-weight:bold;color:#555;font-size:13px;width:160px;white-space:nowrap;">{label}</td>
              <td style="padding:6px 12px;font-size:13px;color:#333;">{value}</td>
            </tr>"""

    delta_section = _delta_html(delta)
    pod_table = _pod_table_html(pods)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:24px 0;">
    <tr><td align="center">
      <table width="680" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:6px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

        <!-- Header -->
        <tr>
          <td style="background:{status_color};padding:20px 28px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <span style="color:#fff;font-size:22px;font-weight:bold;">&#9888; {status}</span>
                  <span style="display:inline-block;margin-left:12px;background:{severity_color};color:#fff;font-size:12px;padding:3px 10px;border-radius:12px;font-weight:bold;">{severity}</span>
                </td>
                <td align="right">
                  <span style="color:rgba(255,255,255,0.85);font-size:12px;">{ts}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:24px 28px;">

            <!-- Info table -->
            <table cellpadding="0" cellspacing="0" style="width:100%;border:1px solid #e8e8e8;border-radius:4px;margin-bottom:20px;">
              {info_rows}
            </table>

            {delta_section}
            {pod_table}

          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f8f9fa;padding:12px 28px;border-top:1px solid #eee;">
            <span style="font-size:11px;color:#999;">AlarmFW &bull; Automated Alert</span>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


class SmtpMailNotifier:
    def __init__(self, cfg: Dict[str, Any]):
        self.host = cfg["host"]
        self.port = int(cfg.get("port", 587))
        self.user = cfg.get("user")
        self.password = cfg.get("password")
        self.use_tls = bool(cfg.get("use_tls", True))
        self.mail_from = cfg.get("from", self.user or "alarmfw@localhost")
        self.to: List[str] = list(cfg.get("to", []) or [])
        self.subject_prefix = cfg.get("subject_prefix", "[ALARMFW]")

    def send(self, payload: Dict[str, Any]) -> None:
        status   = payload.get("status", "UNKNOWN")
        severity = payload.get("severity", "")
        alarm    = payload.get("alarm_name", "")

        subject = f"{self.subject_prefix}[{severity}][{status}] {alarm}"

        msg = MIMEMultipart("alternative")
        msg["From"]    = self.mail_from
        msg["To"]      = ", ".join(self.to)
        msg["Subject"] = subject

        msg.attach(MIMEText(_build_html(payload), "html", "utf-8"))

        with smtplib.SMTP(self.host, self.port, timeout=10) as s:
            if self.use_tls:
                s.starttls()
            if self.user and self.password:
                s.login(self.user, self.password)
            s.send_message(msg)
        log.info("SMTP mail sent to %s", self.to)
