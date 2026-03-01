CHECK_REGISTRY = {
    "dummy": "alarmfw.checks.dummy",
    "shell_command": "alarmfw.checks.shell_command",
    "ocp_pod_health": "alarmfw.checks.ocp_pod_health",
    "ocp_cluster_snapshot": "alarmfw.checks.ocp_cluster_snapshot",
}

__all__ = ["CHECK_REGISTRY"]