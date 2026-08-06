"""Wires config -> DataSource -> service instances for the app to consume."""
from __future__ import annotations

from dataclasses import dataclass

from .analytics_service import AnalyticsService
from .collector_service import CollectorService
from .config import AppConfig
from .data_source import DataSource, get_data_source
from .history_service import HistoryService
from .investigation_service import InvestigationService, get_investigation_backend
from .report_service import ReportService
from .resource_service import ResourceService
from .summary_service import SummaryService


@dataclass
class Services:
    data_source: DataSource
    summary: SummaryService
    collector: CollectorService
    history: HistoryService
    report: ReportService
    analytics: AnalyticsService
    resource: ResourceService
    investigation: InvestigationService


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
        resource=ResourceService(ds, ttl),
        investigation=InvestigationService(get_investigation_backend(config)),
    )
