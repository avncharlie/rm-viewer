from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _ts_to_iso(ms_timestamp: str | int | None) -> str | None:
    """Convert millisecond unix timestamp to ISO 8601 string."""
    if ms_timestamp is None:
        return None
    try:
        ts = int(ms_timestamp) / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


@dataclass
class RemarkableItem:
    id: str
    name: str
    item_type: str  # "notebook", "pdf", "epub", "folder"
    parent_id: str  # "" for root-level items
    last_modified: str | None = None  # ISO 8601
    last_opened: str | None = None
    date_created: str | None = None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'type': self.item_type,
            'lastModified': self.last_modified,
            'lastOpened': self.last_opened,
            'dateCreated': self.date_created,
        }


@dataclass
class RemarkableFolder(RemarkableItem):
    children: list[str] = field(default_factory=list)  # child IDs

    @property
    def item_count(self) -> int:
        return len(self.children)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d['itemCount'] = self.item_count
        return d


@dataclass
class RemarkableDocument(RemarkableItem):
    current_page: int = 1
    total_pages: int = 0
    export_pdf: Path | None = None
    pdf_size: int = 0
    thumbnails_dir: Path | None = None
    thumbnail_pages: list[dict] = field(default_factory=list)
    search_index: Path | None = None

    def to_dict(self) -> dict:
        d = super().to_dict()
        d['currentPage'] = self.current_page
        d['pageCount'] = self.total_pages
        d['pdfSize'] = self.pdf_size
        d['thumbnail'] = f'/api/tree/{self.id}/thumbnail/0'
        return d
