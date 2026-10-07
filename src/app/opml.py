"""Safe OPML parsing for podcast subscription imports.

Uses the stdlib expat parser directly with every DTD/entity hook rejected, so
XXE and entity-expansion payloads (billion laughs) fail before any expansion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import NoReturn
from xml.etree import ElementTree as ET
from xml.parsers import expat


class OpmlParseError(ValueError):
    """Raised when an uploaded document is not a safe, well-formed OPML file."""


@dataclass(frozen=True)
class OpmlFeed:
    url: str
    title: str | None


def _reject_dtd(*_args: object) -> NoReturn:
    raise OpmlParseError("DOCTYPE and entity declarations are not allowed in OPML")


def _attr(attrs: dict[str, str], name: str) -> str | None:
    # Exporters disagree on casing (xmlUrl vs xmlurl).
    for key, value in attrs.items():
        if key.lower() == name.lower():
            return value
    return None


def parse_opml(data: bytes) -> list[OpmlFeed]:
    """Return every outline carrying an xmlUrl, in document order.

    Nested outlines (categories/folders) are included. Duplicates are kept;
    callers decide how to normalise and dedupe URLs.
    """
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _reject_dtd
    parser.EntityDeclHandler = _reject_dtd
    parser.UnparsedEntityDeclHandler = _reject_dtd
    parser.ExternalEntityRefHandler = _reject_dtd

    feeds: list[OpmlFeed] = []
    root: list[str] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        if not root:
            root.append(name)
        if name != "outline":
            return
        url = (_attr(attrs, "xmlUrl") or "").strip()
        if not url:
            return
        title = (_attr(attrs, "text") or _attr(attrs, "title") or "").strip()
        feeds.append(OpmlFeed(url=url, title=title or None))

    parser.StartElementHandler = start

    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise OpmlParseError(f"Malformed XML: {exc}") from exc

    if not root or root[0].lower() != "opml":
        raise OpmlParseError("Document is not OPML (root element must be <opml>)")
    return feeds


def build_opml(feeds: list[OpmlFeed], title: str = "Podly feeds") -> bytes:
    """Serialise feeds as an OPML 2.0 document (attributes XML-escaped)."""
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = title
    ET.SubElement(head, "dateCreated").text = format_datetime(datetime.now(UTC))
    body = ET.SubElement(root, "body")
    for feed in feeds:
        name = feed.title or feed.url
        ET.SubElement(
            body, "outline", type="rss", text=name, title=name, xmlUrl=feed.url
        )
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
