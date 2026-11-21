"""Pull sources. Each module exposes `pull(cfg, records, run_id) -> SourceResult`.

The registry in kb_sync.pull imports them lazily, so a source that is not configured is
never imported.
"""

from dataclasses import dataclass, field


@dataclass
class SourceResult:
    """What one source's pull did. `skipped` and `failed` hold (title, reason) pairs."""

    new: int = 0
    changed: int = 0
    removed: int = 0
    unchanged: int = 0
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)

    def counts(self):
        return {
            "new": self.new,
            "changed": self.changed,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "skipped": len(self.skipped),
            "failed": len(self.failed),
        }
