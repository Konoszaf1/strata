"""Tests for UsageTracker — pruning old entries."""

import json
import time

import pytest

from app.qa.usage_tracker import UsageTracker


@pytest.fixture
def tmp_project(tmp_path):
    (tmp_path / ".stack").mkdir()
    return str(tmp_path)


class TestUsageTrackerPruning:
    def test_old_entries_pruned_on_load(self, tmp_project):
        """Entries older than 24h should be removed when loading."""
        usage_file = f"{tmp_project}/.stack/usage.json"
        old_ts = time.time() - 100_000  # ~28 hours ago
        recent_ts = time.time() - 100  # recent

        entries = [
            {"timestamp": old_ts, "layer": "prompt", "model": "haiku", "tokens_in": 100, "tokens_out": 50},
            {"timestamp": recent_ts, "layer": "context", "model": "sonnet", "tokens_in": 200, "tokens_out": 100},
        ]
        with open(usage_file, "w") as f:
            json.dump(entries, f)

        tracker = UsageTracker(tmp_project)
        assert len(tracker._entries) == 1
        assert tracker._entries[0].layer == "context"

    def test_old_entries_pruned_on_save(self, tmp_project):
        """Saving should also prune old entries."""
        tracker = UsageTracker(tmp_project)
        # Manually inject an old entry
        from app.qa.usage_tracker import UsageEntry
        old_entry = UsageEntry(
            timestamp=time.time() - 100_000,
            layer="prompt",
            model="haiku",
            tokens_in=100,
            tokens_out=50,
        )
        tracker._entries.append(old_entry)
        tracker.record_usage("context", "sonnet", 200, 100)

        # Reload and verify old entry is gone
        tracker2 = UsageTracker(tmp_project)
        assert len(tracker2._entries) == 1
        assert tracker2._entries[0].layer == "context"

    def test_hourly_usage_only_counts_recent(self, tmp_project):
        tracker = UsageTracker(tmp_project)
        tracker.record_usage("prompt", "haiku", 1000, 500)
        hourly = tracker.get_hourly_usage()
        assert hourly["input"] == 1000
        assert hourly["output"] == 500
