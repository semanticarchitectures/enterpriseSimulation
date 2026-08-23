"""Unit tests for reporting formatters.

Verifies that JSONFormatter and CSVFormatter produce valid output
from TimelineEntry and SummaryMetrics data.

Requirements: 12.4
"""

import csv
import io
import json

from enterprise_sim.reporting.formatters import CSVFormatter, JSONFormatter
from enterprise_sim.reporting.reporter import SummaryMetrics, TimelineEntry


class TestJSONFormatter:
    """Tests for JSONFormatter.format()."""

    def test_produces_valid_json_with_timeline_and_metrics_keys(self):
        """JSON output has 'timeline' and 'metrics' top-level keys."""
        timeline = [
            TimelineEntry(
                event_type="gate_completion",
                timestamp=5.0,
                source_module="authorization",
                payload={"gate_name": "gate_1", "chain_id": "chain_a"},
            )
        ]
        metrics = SummaryMetrics(
            total_duration=10.0,
            total_costs=500.0,
            messages_sent=3,
            messages_delivered=2,
            messages_failed=1,
            mean_delivery_time=2.5,
            chain_completion_times={"chain_a": 5.0},
        )

        result = JSONFormatter.format(timeline, metrics)
        parsed = json.loads(result)

        assert "timeline" in parsed
        assert "metrics" in parsed
        assert len(parsed["timeline"]) == 1
        assert parsed["timeline"][0]["event_type"] == "gate_completion"
        assert parsed["timeline"][0]["timestamp"] == 5.0
        assert parsed["timeline"][0]["source_module"] == "authorization"
        assert parsed["metrics"]["total_duration"] == 10.0
        assert parsed["metrics"]["total_costs"] == 500.0

    def test_empty_timeline_produces_valid_json(self):
        """Empty timeline and zero metrics produce valid JSON output."""
        timeline: list[TimelineEntry] = []
        metrics = SummaryMetrics()

        result = JSONFormatter.format(timeline, metrics)
        parsed = json.loads(result)

        assert parsed["timeline"] == []
        assert parsed["metrics"]["total_duration"] == 0.0
        assert parsed["metrics"]["total_costs"] == 0.0
        assert parsed["metrics"]["messages_sent"] == 0

    def test_multiple_timeline_entries(self):
        """Multiple timeline entries are all present in JSON output."""
        timeline = [
            TimelineEntry(
                event_type="fuel_consumed",
                timestamp=1.0,
                source_module="aerodynamics",
                payload={"leg_id": "leg_1", "fuel_quantity": 100.0},
            ),
            TimelineEntry(
                event_type="extraction_start",
                timestamp=3.0,
                source_module="airdrop",
                payload={"drop_zone_id": "dz_alpha"},
            ),
        ]
        metrics = SummaryMetrics(total_duration=3.0)

        result = JSONFormatter.format(timeline, metrics)
        parsed = json.loads(result)

        assert len(parsed["timeline"]) == 2
        assert parsed["timeline"][0]["event_type"] == "fuel_consumed"
        assert parsed["timeline"][1]["event_type"] == "extraction_start"


class TestCSVFormatter:
    """Tests for CSVFormatter.format()."""

    def test_produces_valid_csv_with_timeline_and_metrics(self):
        """CSV output contains timeline entries and metrics summary."""
        timeline = [
            TimelineEntry(
                event_type="gate_completion",
                timestamp=5.0,
                source_module="authorization",
                payload={"gate_name": "gate_1"},
            )
        ]
        metrics = SummaryMetrics(
            total_duration=10.0,
            total_costs=500.0,
            messages_sent=3,
            messages_delivered=2,
            messages_failed=1,
            mean_delivery_time=2.5,
            chain_completion_times={"chain_a": 5.0},
        )

        result = CSVFormatter.format(timeline, metrics)

        # Should be parseable as CSV
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)

        # First row is timeline header comment
        assert rows[0] == ["# Timeline"]
        # Second row is column headers
        assert rows[1] == ["event_type", "timestamp", "source_module", "payload"]
        # Third row is the timeline entry
        assert rows[2][0] == "gate_completion"
        assert rows[2][1] == "5.0"
        assert rows[2][2] == "authorization"

        # Metrics section exists
        assert ["# Metrics"] in rows
        assert ["metric", "value"] in rows

    def test_empty_timeline_produces_valid_csv(self):
        """Empty timeline still produces valid CSV with metrics."""
        timeline: list[TimelineEntry] = []
        metrics = SummaryMetrics()

        result = CSVFormatter.format(timeline, metrics)

        reader = csv.reader(io.StringIO(result))
        rows = list(reader)

        # Should still have timeline header and metrics section
        assert rows[0] == ["# Timeline"]
        assert ["# Metrics"] in rows

    def test_metrics_values_in_csv(self):
        """Metrics values are correctly represented in CSV."""
        timeline: list[TimelineEntry] = []
        metrics = SummaryMetrics(
            total_duration=42.0,
            total_costs=1000.0,
            messages_sent=10,
            messages_delivered=8,
            messages_failed=2,
            mean_delivery_time=3.5,
            chain_completion_times={"c1": 5.0, "c2": 8.0},
        )

        result = CSVFormatter.format(timeline, metrics)

        # Check that key metric values are present in the output
        assert "42.0" in result
        assert "1000.0" in result
        assert "10" in result
        assert "3.5" in result


class TestTimelineEntryModel:
    """Tests for the TimelineEntry Pydantic model."""

    def test_construction_with_all_fields(self):
        """TimelineEntry can be constructed with all fields."""
        entry = TimelineEntry(
            event_type="test_event",
            timestamp=1.5,
            source_module="test_module",
            payload={"key": "value"},
        )
        assert entry.event_type == "test_event"
        assert entry.timestamp == 1.5
        assert entry.source_module == "test_module"
        assert entry.payload == {"key": "value"}

    def test_default_empty_payload(self):
        """TimelineEntry payload defaults to empty dict."""
        entry = TimelineEntry(
            event_type="test_event",
            timestamp=0.0,
            source_module="mod",
        )
        assert entry.payload == {}


class TestSummaryMetricsModel:
    """Tests for the SummaryMetrics Pydantic model."""

    def test_default_zero_values(self):
        """SummaryMetrics defaults to zero values."""
        metrics = SummaryMetrics()
        assert metrics.total_duration == 0.0
        assert metrics.total_costs == 0.0
        assert metrics.messages_sent == 0
        assert metrics.messages_delivered == 0
        assert metrics.messages_failed == 0
        assert metrics.mean_delivery_time == 0.0
        assert metrics.chain_completion_times == {}

    def test_construction_with_values(self):
        """SummaryMetrics can be constructed with specific values."""
        metrics = SummaryMetrics(
            total_duration=100.0,
            total_costs=5000.0,
            messages_sent=20,
            messages_delivered=15,
            messages_failed=5,
            mean_delivery_time=4.2,
            chain_completion_times={"chain_1": 10.0},
        )
        assert metrics.total_duration == 100.0
        assert metrics.total_costs == 5000.0
        assert metrics.chain_completion_times["chain_1"] == 10.0
