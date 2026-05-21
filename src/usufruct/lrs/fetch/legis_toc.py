"""Fetch legis.la.gov LRS TOC pages.

The Louisiana State Legislature site exposes the LRS section catalog via two
TOC layers:

* Root TOC at ``Laws_Toc.aspx?folder=75`` — one anchor per Title with the
  Title's own folder ID embedded in the link.
* Per-Title TOC at ``Laws_Toc.aspx?folder=N&title=T`` — flat list of every
  section in that Title with its ``Law.aspx?d=NNNNNN`` link.

Only ``get`` is required from the client (same duck-typed surface as the
existing CC + Justia fetchers).
"""
from __future__ import annotations

from typing import Union

from ...fetch.client import CachedClient, FetchResult
from ..corpus import LEGIS_LRS_ROOT_TOC_URL, LEGIS_LRS_TITLE_TOC_URL_TEMPLATE


def legis_root_toc_url() -> str:
    return LEGIS_LRS_ROOT_TOC_URL


def legis_title_toc_url(folder: int, title: Union[str, int]) -> str:
    return LEGIS_LRS_TITLE_TOC_URL_TEMPLATE.format(folder=folder, title=title)


def fetch_legis_root_toc(
    client: CachedClient, force_refetch: bool = False
) -> FetchResult:
    return client.get(legis_root_toc_url(), force_refetch=force_refetch)


def fetch_legis_title_toc(
    client: CachedClient,
    folder: int,
    title: Union[str, int],
    force_refetch: bool = False,
) -> FetchResult:
    return client.get(
        legis_title_toc_url(folder, title), force_refetch=force_refetch
    )
