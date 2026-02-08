from __future__ import annotations

import json
import logging
from pathlib import Path

from .rm_items import (
    RemarkableItem, RemarkableFolder, RemarkableDocument, _ts_to_iso
)

log = logging.getLogger(__name__)


class RemarkableIndex:
    """In-memory index built from the output directory at startup."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir.resolve()
        self.items: dict[str, RemarkableItem] = {}
        self.search_indices: dict[str, dict] = {}  # id -> search_index contents
        self.rebuild()

    def rebuild(self):
        """(Re)build the full index from disk."""
        self.items.clear()
        self.search_indices.clear()

        metadata_path = self.output_dir / 'metadata.json'
        if not metadata_path.exists():
            log.warning(f"No metadata.json found in {self.output_dir}")
            return

        with open(metadata_path) as f:
            raw_items = json.load(f)

        # First pass: create all items
        for raw in raw_items:
            item = self._build_item(raw)
            if item:
                self.items[item.id] = item

        # Create virtual root folder
        root = RemarkableFolder(
            id='root', name='My files', item_type='folder', parent_id=''
        )
        self.items['root'] = root

        # Second pass: build parent→children relationships
        for item_id, item in self.items.items():
            if item_id == 'root':
                continue
            parent_id = item.parent_id if item.parent_id else 'root'
            parent = self.items.get(parent_id)
            if isinstance(parent, RemarkableFolder):
                parent.children.append(item_id)
            else:
                # Parent not found or not a folder — attach to root
                root.children.append(item_id)

        # Third pass: compute folder timestamps (max of descendants)
        self._compute_folder_timestamps('root')

    def _build_item(self, raw: dict) -> RemarkableItem | None:
        item_id = raw.get('id', '')
        name = raw.get('name', '')
        parent_id = raw.get('parent', '')

        if raw.get('type') == 'folder':
            return RemarkableFolder(
                id=item_id, name=name, item_type='folder', parent_id=parent_id
            )

        if raw.get('type') == 'book':
            # Read xochitl .metadata for timestamps
            xochitl_dir = raw.get('xochitl_dir', '')
            last_modified = None
            last_opened = None
            date_created = None
            file_type = 'pdf'  # default

            if xochitl_dir:
                xochitl_path = self.output_dir / xochitl_dir
                meta_file = xochitl_path / f'{item_id}.metadata'
                if meta_file.exists():
                    with open(meta_file) as f:
                        xmeta = json.load(f)
                    last_modified = _ts_to_iso(xmeta.get('lastModified'))
                    last_opened = _ts_to_iso(xmeta.get('lastOpened'))
                    date_created = _ts_to_iso(xmeta.get('createdTime'))

                content_file = xochitl_path / f'{item_id}.content'
                if content_file.exists():
                    with open(content_file) as f:
                        content = json.load(f)
                    file_type = content.get('fileType', 'pdf')

            # Resolve paths
            export_pdf = None
            output_pdf_rel = raw.get('output_pdf', '')
            if output_pdf_rel:
                export_pdf = self.output_dir / output_pdf_rel

            pdf_size = 0
            if export_pdf and export_pdf.exists():
                pdf_size = export_pdf.stat().st_size

            thumbnails_dir = None
            thumb_dir_rel = raw.get('thumbnail_dir', '')
            if thumb_dir_rel:
                thumbnails_dir = self.output_dir / thumb_dir_rel

            search_index_path = None
            if export_pdf:
                si = export_pdf.parent / 'search_index.json'
                if si.exists():
                    search_index_path = si
                    try:
                        with open(si) as f:
                            self.search_indices[item_id] = json.load(f)
                    except Exception:
                        log.warning(f"Failed to load search index for {name}")

            doc = RemarkableDocument(
                id=item_id,
                name=name,
                item_type=file_type,  # "notebook", "pdf", "epub"
                parent_id=parent_id,
                last_modified=last_modified,
                last_opened=last_opened,
                date_created=date_created,
                current_page=raw.get('last_opened_page', 1),
                total_pages=raw.get('total_pages', 0),
                export_pdf=export_pdf,
                pdf_size=pdf_size,
                thumbnails_dir=thumbnails_dir,
                thumbnail_pages=raw.get('thumbnail_pages', []),
                search_index=search_index_path,
            )
            return doc

        return None

    def _compute_folder_timestamps(self, folder_id: str) -> tuple[str | None, str | None, str | None]:
        """Recursively compute folder timestamps as max of all descendants."""
        folder = self.items.get(folder_id)
        if not isinstance(folder, RemarkableFolder):
            item = self.items.get(folder_id)
            if item:
                return item.last_modified, item.last_opened, item.date_created
            return None, None, None

        max_modified = None
        max_opened = None
        max_created = None

        for child_id in folder.children:
            m, o, c = self._compute_folder_timestamps(child_id)
            max_modified = max(filter(None, [max_modified, m]), default=None)
            max_opened = max(filter(None, [max_opened, o]), default=None)
            max_created = max(filter(None, [max_created, c]), default=None)

        folder.last_modified = max_modified
        folder.last_opened = max_opened
        folder.date_created = max_created
        return max_modified, max_opened, max_created

    def get(self, item_id: str) -> RemarkableItem | None:
        return self.items.get(item_id)

    def get_children(self, item_id: str) -> list[str]:
        item = self.items.get(item_id)
        if isinstance(item, RemarkableFolder):
            return item.children
        return []

    def get_path(self, item_id: str) -> list[dict]:
        """Build breadcrumb path from root to item."""
        path = []
        current_id = item_id
        while current_id and current_id in self.items:
            item = self.items[current_id]
            path.append({'id': item.id, 'name': item.name})
            if current_id == 'root':
                break
            current_id = item.parent_id if item.parent_id else 'root'
        path.reverse()
        return path

    def get_item_dict(self, item_id: str) -> dict | None:
        """Get item metadata as a dict including path."""
        item = self.items.get(item_id)
        if not item:
            return None
        d = item.to_dict()
        d['path'] = self.get_path(item_id)
        return d

    def search(self, query: str) -> list[dict]:
        """Case-insensitive search across all documents."""
        if not query:
            return []

        query_lower = query.lower()
        results = []

        for item_id, item in self.items.items():
            if not isinstance(item, RemarkableDocument):
                continue

            title_match = query_lower in item.name.lower()
            matches = []

            # Search the search index
            index = self.search_indices.get(item_id, {})
            for source in ('backing_pages', 'ocr_pages'):
                pages = index.get(source, {})
                for page_num, text in pages.items():
                    if query_lower in text.lower():
                        # Extract snippet around the match
                        snippet = self._extract_snippet(text, query_lower)
                        matches.append({
                            'page': int(page_num),
                            'snippet': snippet,
                        })

            if not title_match and not matches:
                continue

            # Deduplicate matches by page number (backing + ocr may overlap)
            seen_pages = set()
            unique_matches = []
            for m in matches:
                if m['page'] not in seen_pages:
                    seen_pages.add(m['page'])
                    unique_matches.append(m)

            result = item.to_dict()
            result['titleMatch'] = title_match
            result['hits'] = len(unique_matches)
            result['matches'] = unique_matches[:10]  # limit to 10 matches
            results.append(result)

        return results

    @staticmethod
    def _extract_snippet(text: str, query_lower: str, context_chars: int = 40) -> str:
        """Extract a snippet around the first occurrence of query in text."""
        text_lower = text.lower()
        idx = text_lower.find(query_lower)
        if idx == -1:
            return text[:80] + '...' if len(text) > 80 else text

        start = max(0, idx - context_chars)
        end = min(len(text), idx + len(query_lower) + context_chars)

        snippet = text[start:end].replace('\n', ' ')
        if start > 0:
            snippet = '...' + snippet
        if end < len(text):
            snippet = snippet + '...'
        return snippet
