"""LRS-specific constants: URLs, URN scheme, citation form."""
from __future__ import annotations

JUSTIA_ROOT_URL = "https://law.justia.com/codes/louisiana/revised-statutes/"
JUSTIA_TITLE_URL_TEMPLATE = (
    "https://law.justia.com/codes/louisiana/revised-statutes/title-{title}/"
)
LEGIS_SECTION_URL_TEMPLATE = "https://legis.la.gov/legis/Law.aspx?d={d}"
LEGIS_LRS_ROOT_TOC_URL = "https://legis.la.gov/legis/Laws_Toc.aspx?folder=75&level=Parent"
LEGIS_LRS_TITLE_TOC_URL_TEMPLATE = (
    "https://legis.la.gov/legis/Laws_Toc.aspx?folder={folder}&title={title}&level=Parent"
)


def urn_for(title_number: str, section_number: str) -> str:
    return f"urn:us-la:rs:{title_number}:{section_number}"


def citation_for(title_number: str, section_number: str) -> str:
    """Canonical display form, e.g. ``R.S. 14:30``."""
    return f"R.S. {title_number}:{section_number}"
