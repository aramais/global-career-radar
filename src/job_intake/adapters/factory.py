from __future__ import annotations

from job_intake.adapters.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from job_intake.adapters.base import JobSourceAdapter
from job_intake.adapters.company_watchlist import CompanyWatchlistAdapter
from job_intake.adapters.dailyremote import DailyRemoteAdapter
from job_intake.adapters.html_page import HtmlPageAdapter
from job_intake.config.settings import SourceDefinition


def build_adapter(source: SourceDefinition) -> JobSourceAdapter:
    if source.type == "dailyremote":
        return DailyRemoteAdapter(source.name, source.params)
    if source.type == "watchlist":
        return CompanyWatchlistAdapter(source.name, source.params)
    if source.type == "html":
        return HtmlPageAdapter(source.name, source.params)
    if source.type == "greenhouse":
        return GreenhouseAdapter(source.name, source.params)
    if source.type == "ashby":
        return AshbyAdapter(source.name, source.params)
    if source.type == "lever":
        return LeverAdapter(source.name, source.params)
    raise ValueError(f"Unsupported source adapter type: {source.type}")
