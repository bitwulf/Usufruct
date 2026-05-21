"""Fetch a single legis.la.gov LRS section page (``Law.aspx?d=NNNNNN``)."""
from __future__ import annotations

from ...fetch.client import CachedClient, FetchResult
from ..corpus import LEGIS_SECTION_URL_TEMPLATE


def legis_section_url(website_law_id: int) -> str:
    return LEGIS_SECTION_URL_TEMPLATE.format(d=website_law_id)


def fetch_legis_section(
    client: CachedClient,
    website_law_id: int,
    force_refetch: bool = False,
) -> FetchResult:
    return client.get(legis_section_url(website_law_id), force_refetch=force_refetch)
