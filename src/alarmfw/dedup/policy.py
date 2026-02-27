from dataclasses import dataclass

@dataclass(frozen=True)
class DedupPolicy:
    repeat_interval_sec: int = 600
    recovery_notify: bool = True
    recovery_cooldown_sec: int = 60
    error_repeat_interval_sec: int = 900

    @staticmethod
    def from_config(cfg: dict) -> "DedupPolicy":
        d = (cfg or {}).get("dedup", {}) or {}
        return DedupPolicy(
            repeat_interval_sec=int(d.get("repeat_interval_sec", 600)),
            recovery_notify=bool(d.get("recovery_notify", True)),
            recovery_cooldown_sec=int(d.get("recovery_cooldown_sec", 60)),
            error_repeat_interval_sec=int(d.get("error_repeat_interval_sec", 900)),
        )
