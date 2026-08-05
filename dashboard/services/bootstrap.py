"""Wires config -> DataSource -> service instances for the app to consume."""
from __future__ import annotations

from dataclasses import dataclass

from .analytics_service import AnalyticsService
from .collector_service import CollectorService
from .config import AppConfig
from .data_source import DataSource, get_data_source
from .history_service import HistoryService
from .report_service import ReportService
from .summary_service import SummaryService


@dataclass
class Services:
    data_source: DataSource
    summary: SummaryService
    collector: CollectorService
    history: HistoryService
    report: ReportService
    analytics: AnalyticsService


def build_services(config: AppConfig) -> Services:
    ds = get_data_source(config)
    ttl = config.refresh_interval_seconds
    return Services(
        data_source=ds,
        summary=SummaryService(ds, ttl),
        collector=CollectorService(ds, ttl),
        history=HistoryService(ds, ttl),
        report=ReportService(ds, ttl),
        analytics=AnalyticsService(ds, ttl),
    )
