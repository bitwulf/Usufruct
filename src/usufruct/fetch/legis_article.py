"""Fetch a single legis.la.gov Civil Code article page."""
from __future__ import annotations

from .client import CachedClient, FetchResult

LEGIS_ARTICLE_URL = "https://legis.la.gov/legis/Law.aspx?d={d}"


def article_url(website_law_id: int) -> str:
    return LEGIS_ARTICLE_URL.format(d=website_law_id)


def fetch_legis_article(
    client: CachedClient, website_law_id: int, force_refetch: bool = False
) -> FetchResult:
    return client.get(article_url(website_law_id), force_refetch=force_refetch)
