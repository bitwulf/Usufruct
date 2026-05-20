"""Fetch the legis.la.gov flat Civil Code table of contents."""
from __future__ import annotations

from .client import CachedClient, FetchResult

LEGIS_TOC_URL = "https://legis.la.gov/legis/Laws_Toc.aspx?folder=67&level=Parent"


def fetch_legis_toc(client: CachedClient, force_refetch: bool = False) -> FetchResult:
    return client.get(LEGIS_TOC_URL, force_refetch=force_refetch)
