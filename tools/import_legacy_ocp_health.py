from __future__ import annotations

import os
from pathlib import Path


def parse_env_conf(path: Path) -> dict:
    d = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        d[k] = v
    return d

def expand_env_value(v: str) -> str:
    # supports "${VAR}" and "$VAR"
    v = (v or "").strip()
    if v.startswith("${") and v.endswith("}"):
        key = v[2:-1].strip()
        return os.environ.get(key, v)
    if v.startswith("$") and len(v) > 1 and v[1:].replace("_", "").isalnum():
        key = v[1:]
        return os.environ.get(key, v)
    return v

def is_true(v: str | None) -> bool:
    return (v or "").strip().lower() == "true"


def get_enable_namespace(ns_cfg: dict) -> bool:
    # New preferred key
    if "NAMESPACE_ENABLED" in ns_cfg:
        return is_true(ns_cfg.get("NAMESPACE_ENABLED"))
    # Legacy key
    return is_true(ns_cfg.get("MONITORING"))


def compute_notify(ns_cfg: dict) -> tuple[list[str], list[str]]:
    # New preferred keys (optional)
    zbx = is_true(ns_cfg.get("ZABBIX_ENABLED")) or is_true(ns_cfg.get("ZABBIX"))
    mail = is_true(ns_cfg.get("MAIL_ENABLED")) or is_true(ns_cfg.get("MAIL"))

    if zbx and mail:
        return (["zabbix"], ["smtp"])
    if zbx and not mail:
        return (["zabbix"], [])
    if (not zbx) and mail:
        return (["smtp"], [])
    return (["dev_outbox"], [])


def main():
    legacy_root = Path(os.environ.get("LEGACY_ROOT", "legacy/podhealthalarm")).resolve()
    conf_dir = legacy_root / "conf.d"
    clusters_dir = legacy_root / "clusters.d"
    out_path = Path("config/generated/ocp_pod_health.yaml")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    checks = []

    for ns_conf in sorted(conf_dir.glob("*.conf")):
        ns = ns_conf.stem
        ns_cfg = parse_env_conf(ns_conf)

        if not get_enable_namespace(ns_cfg):
            continue

        clusters = [c.strip() for c in (ns_cfg.get("CLUSTERS", "")).split(",") if c.strip()]
        if not clusters:
            continue

        # optional fields
        node = ns_cfg.get("NODE", "OCP")
        department = ns_cfg.get("DEPARTMENT", "UNKNOWN")
        severity = ns_cfg.get("SEVERITY", "5")
        alertgroup = ns_cfg.get("ALERTGROUP", f"{ns}AlertGroup")
        alertkey = ns_cfg.get("POD_HEALTH_ALERTKEY", "OCP_POD_HEALTH")

        primary, fallback = compute_notify(ns_cfg)

        for cl in clusters:
            cl_conf = clusters_dir / f"{cl}.conf"
            if not cl_conf.exists():
                continue
            cl_cfg = parse_env_conf(cl_conf)

            api = expand_env_value(cl_cfg.get("OCP_API", ""))
            token = expand_env_value(cl_cfg.get("OCP_TOKEN", ""))
            insecure = cl_cfg.get("OCP_INSECURE", "true")

            if not api or not token:
                continue

            check_name = f"ocp_pod_health__{ns}__{cl}"

            checks.append(
                {
                    "name": check_name,
                    "type": "ocp_pod_health",
                    "enabled": True,
                    "params": {
                        "namespace": ns,
                        "cluster": cl,
                        "ocp_api": api,
                        "ocp_token": token,
                        "ocp_insecure": insecure,
                        "timeout_sec": 30,
                        "node": node,
                        "department": department,
                        "severity": severity,
                        "alertgroup": alertgroup,
                        "alertkey": alertkey,
                    },
                    "notify": {"primary": primary, "fallback": fallback},
                }
            )

    # write yaml manually (no dependency)
    def y(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    lines = ["checks:"]
    for c in checks:
        lines.append(f'  - name: "{y(c["name"])}"')
        lines.append(f'    type: "{y(c["type"])}"')
        lines.append(f"    enabled: {str(c['enabled']).lower()}")
        lines.append("    params:")
        for pk, pv in c["params"].items():
            lines.append(f'      {pk}: "{y(str(pv))}"')
        lines.append("    notify:")
        lines.append("      primary:")
        for n in c["notify"]["primary"]:
            lines.append(f'        - "{y(n)}"')

        if c["notify"]["fallback"]:
            lines.append("      fallback:")
            for n in c["notify"]["fallback"]:
                lines.append(f'        - "{y(n)}"')
        else:
            lines.append("      fallback: []")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated: {out_path} (checks={len(checks)})")


if __name__ == "__main__":
    main()