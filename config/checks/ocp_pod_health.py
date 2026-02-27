from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class PodIssue:
    pod: str
    ready_ok: bool
    phase: str
    waiting: str
    terminated: str
    workload: str
    restarts: int


def _workload_from_ownerrefs(owner_refs: Any) -> str:
    try:
        if owner_refs and len(owner_refs) > 0:
            k = owner_refs[0].get("kind") or "unknown"
            n = owner_refs[0].get("name") or "unknown"
            return f"{k}/{n}"
    except Exception:
        pass
    return "unknown/unknown"


def _sum_restarts(container_statuses: Any) -> int:
    total = 0
    if not container_statuses:
        return 0
    for cs in container_statuses:
        try:
            total += int(cs.get("restartCount") or 0)
        except Exception:
            pass
    return total


def _ready_ok(container_statuses: Any) -> bool:
    if not container_statuses:
        return True
    total = len(container_statuses)
    ready = sum(1 for cs in container_statuses if cs.get("ready") is True)
    return total > 0 and ready == total


def _waiting_reasons(container_statuses: Any) -> str:
    reasons: List[str] = []
    for cs in (container_statuses or []):
        st = cs.get("state") or {}
        w = st.get("waiting")
        if w and w.get("reason"):
            reasons.append(str(w["reason"]))
    # uniq, preserve-ish
    out: List[str] = []
    for r in reasons:
        if r not in out:
            out.append(r)
    return ",".join(out)


def _terminated_reasons(container_statuses: Any) -> str:
    reasons: List[str] = []
    for cs in (container_statuses or []):
        st = cs.get("state") or {}
        t = st.get("terminated")
        if t and t.get("reason"):
            reasons.append(str(t["reason"]))
    out: List[str] = []
    for r in reasons:
        if r not in out:
            out.append(r)
    return ",".join(out)


def _is_problem(issue: PodIssue) -> bool:
    # Legacy kurallarına yakın:
    # - Failed/Unknown/Pending
    # - CrashLoopBackOff, ImagePullBackOff, ErrImagePull, CreateContainer*, RunContainerError, ContainerCreating, Error
    # - Running ama ready değil
    # - OOMKilled
    if issue.phase in {"Failed", "Unknown", "Pending"}:
        return True

    waiting = issue.waiting or ""
    terminated = issue.terminated or ""

    bad_wait = (
        "CrashLoopBackOff" in waiting
        or "ImagePullBackOff" in waiting
        or "ErrImagePull" in waiting
        or "CreateContainerConfigError" in waiting
        or "CreateContainerError" in waiting
        or "RunContainerError" in waiting
        or "ContainerCreating" in waiting
        or waiting == "Error"
    )
    if bad_wait:
        return True

    if issue.phase == "Running" and not issue.ready_ok:
        return True

    if "OOMKilled" in terminated:
        return True

    return False


def _oc(*args: str, timeout_sec: int = 30, env: Dict[str, str] | None = None) -> Tuple[int, str, str]:
    p = subprocess.run(
        ["oc", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_sec,
        env=env,
    )
    return p.returncode, p.stdout, p.stderr


def _login_and_get_pods(api: str, token: str, insecure: bool, namespace: str, timeout_sec: int) -> Dict[str, Any]:
    # isolated kubeconfig file per run
    with tempfile.NamedTemporaryFile(prefix="alarmfw_ocp_", suffix=".kubeconfig", delete=False) as tf:
        kubeconfig_path = tf.name
    os.chmod(kubeconfig_path, 0o600)

    try:
        login_args = ["login", f"--server={api}", f"--token={token}"]
        if insecure:
            login_args.append("--insecure-skip-tls-verify=true")

        rc, out, err = _oc(*login_args, timeout_sec=timeout_sec, env={"KUBECONFIG": kubeconfig_path})
        if rc != 0:
            raise RuntimeError(f"oc login failed rc={rc} err={err.strip()[:400]}")

        # project switch soft
        _oc("project", namespace, timeout_sec=timeout_sec, env={"KUBECONFIG": kubeconfig_path})

        rc, out, err = _oc("get", "pods", "-n", namespace, "-o", "json", timeout_sec=timeout_sec, env={"KUBECONFIG": kubeconfig_path})
        if rc != 0:
            raise RuntimeError(f"oc get pods failed rc={rc} err={err.strip()[:400]}")
        return json.loads(out)
    finally:
        try:
            os.remove(kubeconfig_path)
        except Exception:
            pass


class OcpPodHealthCheck:
    """
    Single namespace + single cluster pod health check.
    Produces PROBLEM if any problematic pod exists, else OK.

    Params expected:
      namespace, cluster
      ocp_api, ocp_token, ocp_insecure (bool-ish)
      timeout_sec
    """

    def __init__(self, params: Dict[str, Any]):
        self.namespace = str(params["namespace"])
        self.cluster = str(params["cluster"])
        self.api = str(params["ocp_api"])
        self.token = str(params["ocp_token"])
        self.insecure = str(params.get("ocp_insecure", "true")).lower() == "true"
        self.timeout_sec = int(params.get("timeout_sec", 30))

    def run(self) -> Dict[str, Any]:
        # oc binary check
        if not shutil.which("oc"):
            return {
                "status": "PROBLEM",
                "message": "oc binary not found in container",
                "evidence": {"namespace": self.namespace, "cluster": self.cluster},
            }

        pods = _login_and_get_pods(self.api, self.token, self.insecure, self.namespace, self.timeout_sec)

        issues: List[PodIssue] = []
        for item in (pods.get("items") or []):
            meta = item.get("metadata") or {}
            stat = item.get("status") or {}
            spec = item.get("spec") or {}

            name = meta.get("name") or "-"
            phase = stat.get("phase") or "Unknown"

            # exclude succeeded + Jobs (legacy)
            owner_refs = meta.get("ownerReferences") or []
            workload = _workload_from_ownerrefs(owner_refs)
            if phase == "Succeeded":
                continue
            if workload.startswith("Job/"):
                continue

            cs = stat.get("containerStatuses") or []
            ready_ok = _ready_ok(cs)
            waiting = _waiting_reasons(cs)
            terminated = _terminated_reasons(cs)
            restarts = _sum_restarts(cs)

            issue = PodIssue(
                pod=name,
                ready_ok=ready_ok,
                phase=phase,
                waiting=waiting or "-",
                terminated=terminated or "-",
                workload=workload,
                restarts=restarts,
            )

            if _is_problem(issue):
                issues.append(issue)

        if issues:
            # kısa, okunur mesaj
            top = issues[:15]
            lines = [
                f"- {i.pod} | phase={i.phase} ready={i.ready_ok} waiting={i.waiting} terminated={i.terminated} workload={i.workload} restarts={i.restarts}"
                for i in top
            ]
            msg = (
                f"[OCP POD HEALTH] ns={self.namespace} cluster={self.cluster} problematic_pods={len(issues)}\n"
                + "\n".join(lines)
            )
            return {
                "status": "PROBLEM",
                "message": msg,
                "evidence": {
                    "namespace": self.namespace,
                    "cluster": self.cluster,
                    "count": len(issues),
                    "pods": [i.__dict__ for i in issues],
                },
            }

        return {
            "status": "OK",
            "message": f"[OCP POD HEALTH] ns={self.namespace} cluster={self.cluster} OK",
            "evidence": {"namespace": self.namespace, "cluster": self.cluster},
        }
