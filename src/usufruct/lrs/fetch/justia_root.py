"""Fetch the Justia LRS root index page (lists all 54 Titles)."""
from __future__ import annotations

from ...fetch.client import CachedClient, FetchResult
from ..corpus import JUSTIA_ROOT_URL

__all__ = ["JUSTIA_ROOT_URL", "fetch_justia_root"]


def fetch_justia_root(
    client: CachedClient, force_refetch: bool = False
) -> FetchResult:
    return client.get(JUSTIA_ROOT_URL, force_refetch=force_refetch)
