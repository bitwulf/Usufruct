"""Fetch a single Justia per-Title LRS page."""
from __future__ import annotations

from ...fetch.client import CachedClient, FetchResult
from ..corpus import JUSTIA_TITLE_URL_TEMPLATE


def justia_title_url(title_number: str | int) -> str:
    return JUSTIA_TITLE_URL_TEMPLATE.format(title=title_number)


def fetch_justia_title(
    client: CachedClient,
    title_number: str | int,
    force_refetch: bool = False,
) -> FetchResult:
    return client.get(justia_title_url(title_number), force_refetch=force_refetch)
