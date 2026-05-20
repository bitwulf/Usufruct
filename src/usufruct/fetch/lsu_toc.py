"""Fetch the LSU CCLS table of contents."""
from __future__ import annotations

from .client import CachedClient, FetchResult

LSU_TOC_URL = "https://lcco.law.lsu.edu/?uid=1&ver=en"


def fetch_lsu_toc(client: CachedClient, force_refetch: bool = False) -> FetchResult:
    return client.get(LSU_TOC_URL, force_refetch=force_refetch)
