"""JSON and CSV output formatters.

Provides structured output formatting for Reporter timeline and metrics.
Each formatter produces a string representation in its respective format.

Requirements: 12.4
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from enterprise_sim.reporting.reporter import SummaryMetrics, TimelineEntry


class JSONFormatter:
    """Formats timeline and metrics as JSON."""

    @staticmethod
    def format(timeline: list[TimelineEntry], metrics: SummaryMetrics) -> str:
        """Produce JSON string output from timeline and metrics.

        Args:
            timeline: List of TimelineEntry objects.
            metrics: SummaryMetrics object.

        Returns:
            JSON string with "timeline" and "metrics" keys.
        """
        output: dict[str, Any] = {
            "timeline": [entry.model_dump() for entry in timeline],
            "metrics": metrics.model_dump(),
        }
        return json.dumps(output, indent=2)


class CSVFormatter:
    """Formats timeline and metrics as CSV."""

    @staticmethod
    def format(timeline: list[TimelineEntry], metrics: SummaryMetrics) -> str:
        """Produce CSV string output from timeline and metrics.

        The CSV output contains two sections:
        1. Timeline entries with columns: event_type, timestamp, source_module, payload
        2. Metrics summary as key-value rows

        Args:
            timeline: List of TimelineEntry objects.
            metrics: SummaryMetrics object.

        Returns:
            CSV formatted string.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Timeline section
        writer.writerow(["# Timeline"])
        writer.writerow(["event_type", "timestamp", "source_module", "payload"])
        for entry in timeline:
            writer.writerow([
                entry.event_type,
                entry.timestamp,
                entry.source_module,
                json.dumps(entry.payload),
            ])

        # Separator
        writer.writerow([])

        # Metrics section
        writer.writerow(["# Metrics"])
        writer.writerow(["metric", "value"])
        writer.writerow(["total_duration", metrics.total_duration])
        writer.writerow(["total_costs", metrics.total_costs])
        writer.writerow(["messages_sent", metrics.messages_sent])
        writer.writerow(["messages_delivered", metrics.messages_delivered])
        writer.writerow(["messages_failed", metrics.messages_failed])
        writer.writerow(["mean_delivery_time", metrics.mean_delivery_time])
        writer.writerow([
            "chain_completion_times",
            json.dumps(metrics.chain_completion_times),
        ])

        return output.getvalue()
