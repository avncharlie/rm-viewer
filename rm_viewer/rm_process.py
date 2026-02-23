import os
import json
import shutil
import hashlib
import logging
import argparse
import tempfile
import traceback
import zipfile
from pathlib import Path

import fitz
import xxhash
from remarks import run_remarks
from rmc.exporters.svg import set_device, set_dimensions_for_pdf
from rmc.exporters.pdf import rm_to_svg, chrome_svg_to_pdf

from .utils import validate_path, validate_output_path, get_gcv_api_key
from .ocr import run_ocr_on_rm_output, add_text_layer_to_page

log = logging.getLogger(__name__)

def xx_dir_hash(directory: Path) -> str:
    """Compute a hash of all files in directory for change detection."""
    h = xxhash.xxh3_64()
    for f in sorted(directory.rglob('*')):
        if f.is_file():
            h.update(str(f.relative_to(directory)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def compute_source_hash(id: str, files: list[Path]) -> str:
    """Compute hash from source xochitl files for change detection.

    Copies files to a temp directory to get the same structure as nb_xochitl_dir,
    then computes the hash.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for file in files:
            if file.is_dir():
                shutil.copytree(file, tmp_path / file.name)
            else:
                shutil.copy(file, tmp_path)
        return xx_dir_hash(tmp_path)


def call_remarks(xochitl_dir: Path, output_dir: Path) -> bool:
    """Run remarks on xochitl directory. Returns True on success."""
    log.info(f"Running remarks on {xochitl_dir}")
    try:
        run_remarks(xochitl_dir, output_dir)
        return True
    except Exception as e:
        log.warning(f"remarks failed: {e}")
        return False


def build_process_parser(parser: argparse._SubParsersAction):
    process_parser = parser.add_parser(
        'processor',
        help='Run "processor" to convert a Remarkable file directory into an '
            'output directory.'
    )
    process_parser.add_argument(
        'xochitl_dir', type=validate_path,
        help="Path of xochitl dir to process"
    )
    process_parser.add_argument(
        'output_dir', type=validate_output_path,
        help="Path to put processed xochitl files"
    )
    process_parser.add_argument(
        '--ocr-debug', action='store_true',
        help="Make OCR text visible for debugging alignment"
    )
    process_parser.add_argument(
        '--no-ocr', action='store_true',
        help="Disable OCR even if API key is available"
    )
    process_parser.add_argument(
        '--no-thumbnails', action='store_true',
        help="Skip thumbnail generation"
    )


def create_id_filemap(xochitl_dir: Path) -> dict[str, list[Path]]:
    '''
    Group files by ID

    :param xochitl_dir: /home/root/.local/share/remarkable/xochitl directory
    from remarkable
    :returns: mapping of notebook ID -> files corresponding to notebook
    '''
    xochitl_files = os.listdir(xochitl_dir)
    id_filemap: dict[str, list[Path]] = {}
    for filename in xochitl_files:
        uuid = filename.split('.')[0]
        if uuid not in id_filemap:
            id_filemap[uuid] = []
        id_filemap[uuid].append(xochitl_dir / filename)
    return id_filemap

def get_page_count(content: dict) -> int:
    """Extract total page count from content.json."""
    if 'cPages' in content:
        return len(content['cPages'].get('pages', []))
    elif 'pages' in content:
        return len(content['pages'])
    return 0


def get_pages(content: dict) -> list[str]:
    """
    Extract ordered list of page IDs from content
    :param content: parsed .content
    :returns: List of page IDs in display order
    """
    if "cPages" in content:
        return [
            page["id"] for page in content["cPages"]["pages"]
            if not page.get("deleted", {}).get("value", 0) == 1
        ]
    elif "pages" in content:
        return content["pages"]
    return []


def get_page_redir_map(content: dict) -> dict[str, int | None]:
    """
    Get mapping from page ID to backing PDF page index.

    :param content: parsed .content
    :returns: Dict mapping page_id -> backing PDF page index, or None if inserted page
    """
    redir_map = {}
    if "cPages" in content:
        for page in content["cPages"]["pages"]:
            if page.get("deleted", {}).get("value", 0) == 1:
                continue
            page_id = page["id"]
            if "redir" in page:
                redir_map[page_id] = page["redir"].get("value")
            else:
                # Inserted page - no backing PDF
                redir_map[page_id] = None
    elif "pages" in content:
        # Old format - assume 1:1 mapping
        for i, page_id in enumerate(content["pages"]):
            redir_map[page_id] = i
    return redir_map


def rm_to_pdf_no_text(rm_path, pdf_path):
    '''
    Convert .rm file to PDF, and hide text.
    '''
    with tempfile.NamedTemporaryFile(suffix=".svg", mode="w", delete=False) as f_temp:
        rm_to_svg(rm_path, f_temp.name)
        temp_svg_path = f_temp.name

    # hack to hide text
    new = ''
    with open(temp_svg_path, 'r') as f:
        lines = f.readlines()
        for i, line in enumerate(lines):
            new += line
            if i >= len(lines) - 2:
                continue
            if line.strip().startswith('text {') and \
                    lines[i+1].strip().startswith('font-family'): #}
                new += 'display: none;\n'
    with open(temp_svg_path, 'w') as f:
        f.write(new)

    try:
        chrome_svg_to_pdf(temp_svg_path, pdf_path)
    finally:
        Path(temp_svg_path).unlink(missing_ok=True)

def build_rm_file_index(
    rm_file_dir: Path,
    rm_output_dir: Path,
    base_output_dir: Path,
    pages: list[str],
    content: dict,
    backing_pdf_file: Path | None,
    api_key: str | None = None,
    old_rm_files: list[dict] | None = None
) -> tuple[list[dict], int]:
    '''
    Build index of .rm files with their page mappings and convert to PDF.

    :param rm_file_dir: Directory containing .rm files
    :param rm_output_dir: Directory for converted PDFs
    :param base_output_dir: Base output directory for relative paths
    :param pages: Ordered list of page IDs
    :param content: Parsed .content dict
    :param backing_pdf_file: Path to backing PDF, or None
    :param api_key: Google Cloud Vision API key for OCR
    :param old_rm_files: Previous rm_files metadata for OCR caching
    :returns: Tuple of (list of dicts with page_id, path, index, backing_pdf_index, ocr_path; new OCR scan count)
    '''
    rm_files = []
    new_ocr_count = 0
    redir_map = get_page_redir_map(content)

    # Build lookup of old page data by page_id for OCR caching
    old_pages_by_id = {}
    if old_rm_files:
        for old_page in old_rm_files:
            old_page_id = old_page.get('page_id', '')
            if old_page_id:
                old_pages_by_id[old_page_id] = old_page

    # Open backing PDF if it exists to get page dimensions
    backing_pdf_doc = None
    if backing_pdf_file and backing_pdf_file.exists():
        backing_pdf_doc = fitz.open(backing_pdf_file)

    for f in rm_file_dir.rglob('*.rm'):
        page_id = f.stem
        if page_id not in pages:
            # Orphaned .rm file (e.g. page was deleted but file remains)
            continue
        page_index = pages.index(page_id)
        backing_pdf_index = redir_map.get(page_id)

        # Set dimensions based on backing PDF page or device default
        if backing_pdf_doc and backing_pdf_index is not None:
            page = backing_pdf_doc[backing_pdf_index]
            w_pt, h_pt = page.rect.width, page.rect.height
            set_dimensions_for_pdf(w_pt, h_pt)
        else:
            set_device('RMPP')

        # Convert .rm to PDF
        fname = page_id
        rm_output_pdf = rm_output_dir / f'{fname}.pdf'
        rm_to_pdf_no_text(str(f), str(rm_output_pdf))

        rm_hash = hashlib.md5(f.read_bytes()).hexdigest()

        # Check if we can reuse old OCR
        old_page = old_pages_by_id.get(page_id)
        can_reuse_ocr = False
        if old_page:
            old_rm_hash = old_page.get('rm_hash', '')
            old_backing_idx = old_page.get('backing_pdf_index')
            if old_rm_hash == rm_hash and old_backing_idx == backing_pdf_index:
                can_reuse_ocr = True

        # Run OCR if API key is available
        ocr_path = None
        if api_key:
            if can_reuse_ocr and old_page.get('ocr_path'):
                # Reuse old OCR file path directly (file already exists)
                old_ocr_full_path = base_output_dir / old_page['ocr_path']
                if old_ocr_full_path.exists():
                    ocr_path = old_page['ocr_path']
                    log.debug(f"Reusing OCR for page {page_id}")

            if not ocr_path:
                # Run fresh OCR
                ocr_json_path = rm_output_dir / f'{fname}.ocr.json'
                ocr_result = run_ocr_on_rm_output(
                    rm_output_pdf, ocr_json_path, api_key, rm_hash, dpi=600
                )
                if ocr_result:
                    ocr_path = str(ocr_json_path.relative_to(base_output_dir))
                    new_ocr_count += 1

        rm_files.append({
            'page_id': page_id,
            'rm_path': str(f.relative_to(base_output_dir)),
            'rm_hash': rm_hash,
            'out_path': str(rm_output_pdf.relative_to(base_output_dir)),
            'index': page_index,
            'backing_pdf_index': backing_pdf_index,
            'ocr_path': ocr_path
        })

    if backing_pdf_doc:
        backing_pdf_doc.close()

    return rm_files, new_ocr_count


def build_page_index(
    rm_file_dir: Path | None,
    pages: list[str],
    content: dict
) -> list[dict]:
    """Build index of ALL pages with their cache keys.

    Returns list of dicts with:
        - page_id: str
        - index: int
        - backing_pdf_index: int | None
        - rm_hash: str | None
    """
    redir_map = get_page_redir_map(content)

    # Build set of page IDs that have .rm files
    rm_hashes = {}
    if rm_file_dir and rm_file_dir.exists():
        for f in rm_file_dir.rglob('*.rm'):
            page_id = f.stem
            rm_hashes[page_id] = hashlib.md5(f.read_bytes()).hexdigest()

    page_index = []
    for idx, page_id in enumerate(pages):
        page_index.append({
            'page_id': page_id,
            'index': idx,
            'backing_pdf_index': redir_map.get(page_id),
            'rm_hash': rm_hashes.get(page_id)
        })

    return page_index


def generate_thumbnails(
    output_pdf: Path,
    thumbnail_dir: Path,
    base_output_dir: Path,
    page_index: list[dict],
    old_thumbnail_pages: list[dict] | None = None
) -> tuple[list[dict], int]:
    """Generate thumbnails for all pages with caching support.

    :param output_pdf: Path to the final output PDF
    :param thumbnail_dir: Directory to store thumbnails
    :param base_output_dir: Base output directory for relative paths
    :param page_index: List of page metadata from build_page_index()
    :param old_thumbnail_pages: Previous thumbnail_pages metadata for caching
    :returns: Tuple of (list of thumbnail metadata dicts, count of new thumbnails generated)
    """
    target_width = 384
    target_height = 512

    # Build lookup of old pages by page_id
    old_pages_by_id = {}
    if old_thumbnail_pages:
        for old_page in old_thumbnail_pages:
            old_page_id = old_page.get('page_id', '')
            if old_page_id:
                old_pages_by_id[old_page_id] = old_page

    doc = fitz.open(output_pdf)
    thumbnail_pages = []
    new_thumbnails_count = 0

    for page_info in page_index:
        page_id = page_info['page_id']
        page_idx = page_info['index']
        backing_pdf_index = page_info['backing_pdf_index']
        rm_hash = page_info['rm_hash']

        thumbnail_path = thumbnail_dir / f'{page_idx} - {page_id}.png'
        thumbnail_rel_path = str(thumbnail_path.relative_to(base_output_dir))

        # Check if cache is valid - search by UUID to handle page reordering
        old_page = old_pages_by_id.get(page_id)
        can_reuse = False
        existing_thumbnails = list(thumbnail_dir.glob(f'* - {page_id}.png'))
        existing_thumbnail = existing_thumbnails[0] if existing_thumbnails else None

        if old_page and existing_thumbnail:
            old_backing_idx = old_page.get('backing_pdf_index')
            old_rm_hash = old_page.get('rm_hash')
            # Cache valid if both backing_pdf_index AND rm_hash match
            if old_backing_idx == backing_pdf_index and old_rm_hash == rm_hash:
                # Special case: inserted blank page (None, None) - always regenerate
                if backing_pdf_index is None and rm_hash is None:
                    can_reuse = False
                else:
                    can_reuse = True
                    # Rename if page number changed
                    if existing_thumbnail != thumbnail_path:
                        log.debug(f"Renaming thumbnail: {existing_thumbnail.name} -> {thumbnail_path.name}")
                        existing_thumbnail.rename(thumbnail_path)

        if can_reuse:
            log.debug(f"Reusing thumbnail for page {page_idx}")
            thumbnail_pages.append({
                'page_id': page_id,
                'index': page_idx,
                'backing_pdf_index': backing_pdf_index,
                'rm_hash': rm_hash,
                'thumbnail_path': thumbnail_rel_path
            })
            continue

        # Render new thumbnail
        if page_idx >= len(doc):
            log.warning(f"Page index {page_idx} out of range for {output_pdf}")
            continue

        page = doc[page_idx]
        page_rect = page.rect

        if backing_pdf_index is not None:
            # PDF-backed page: fit entire page within thumbnail, centered
            zoom = min(target_width / page_rect.width, target_height / page_rect.height)
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix)
            thumb = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, target_width, target_height), 0)
            thumb.set_rect(thumb.irect, (255, 255, 255))
            x_offset = (target_width - pix.width) // 2
            y_offset = (target_height - pix.height) // 2
            pix.set_origin(x_offset, y_offset)
            thumb.copy(pix, pix.irect)
            thumb.save(thumbnail_path)
        else:
            # No backing PDF: fill width, crop from top if tall
            zoom = target_width / page_rect.width
            matrix = fitz.Matrix(zoom, zoom)
            scaled_height = int(page_rect.height * zoom)

            if scaled_height >= target_height:
                clip = fitz.IRect(0, 0, target_width, target_height)
                pix = page.get_pixmap(matrix=matrix, clip=clip)
                pix.save(thumbnail_path)
            else:
                pix = page.get_pixmap(matrix=matrix)
                thumb = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, target_width, target_height), 0)
                thumb.set_rect(thumb.irect, (255, 255, 255))
                y_offset = (target_height - pix.height) // 2
                pix.set_origin(0, y_offset)
                thumb.copy(pix, pix.irect)
                thumb.save(thumbnail_path)
        new_thumbnails_count += 1

        log.info(f"Generated thumbnail for page {page_idx}: {thumbnail_path.name}")

        thumbnail_pages.append({
            'page_id': page_id,
            'index': page_idx,
            'backing_pdf_index': backing_pdf_index,
            'rm_hash': rm_hash,
            'thumbnail_path': thumbnail_rel_path
        })

    doc.close()

    # Clean up orphaned thumbnails (wrong page number or deleted pages)
    current_thumbnail_names = {f'{p["index"]} - {p["page_id"]}.png' for p in thumbnail_pages}
    for f in thumbnail_dir.glob('*.png'):
        if f.name not in current_thumbnail_names:
            log.info(f"Removing orphaned thumbnail: {f.name}")
            f.unlink()

    return thumbnail_pages, new_thumbnails_count


def stitch_ocr_text_layers(
    output_pdf: Path,
    rm_files: list[dict],
    base_output_dir: Path,
    debug: bool = False
) -> tuple[int, int]:
    """
    Stitch OCR text layers into the final remarks PDF.

    :param output_pdf: Path to the remarks output PDF
    :param rm_files: List of rm file dicts with ocr_path entries
    :param base_output_dir: Base output directory for resolving relative paths
    :param debug: If True, make text visible for debugging
    :returns: Tuple of (number of pages with OCR text added, total words added)
    """
    doc = fitz.open(output_pdf)
    pages_with_ocr = 0
    total_words = 0

    for rm_file in rm_files:
        ocr_rel_path = rm_file.get('ocr_path')
        if not ocr_rel_path:
            continue

        page_index = rm_file['index']
        if page_index >= len(doc):
            log.warning(f"Page index {page_index} out of range for {output_pdf}")
            continue

        page = doc[page_index]

        ocr_path = base_output_dir / ocr_rel_path
        if not ocr_path.exists():
            log.warning(f"OCR file not found: {ocr_path}")
            continue

        try:
            with open(ocr_path) as f:
                ocr_result = json.load(f)

            words_added = add_text_layer_to_page(
                page, ocr_result, page.rect.width, page.rect.height, debug=debug
            )

            if words_added > 0:
                pages_with_ocr += 1
                total_words += words_added
                log.debug(f"Added {words_added} words to page {page_index}")

        except Exception as e:
            log.warning(f"Failed to add OCR layer for page {page_index}: {e}")

    doc.save(output_pdf, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()

    return pages_with_ocr, total_words


def build_search_index(
    output_pdf: Path,
    rm_files: list[dict],
    base_output_dir: Path,
    search_index_path: Path
) -> None:
    """
    Build a search index JSON file with backing PDF text and OCR text per page.

    Must be called BEFORE stitch_ocr_text_layers() so that page.get_text()
    returns only the original backing PDF text, not the stitched OCR layer.

    :param output_pdf: Path to the remarks output PDF (before OCR stitching)
    :param rm_files: List of rm file dicts with ocr_path entries
    :param base_output_dir: Base output directory for resolving relative paths
    :param search_index_path: Path to write the search_index.json
    """
    backing_pages = {}
    ocr_pages = {}

    # Extract text from backing PDF pages
    doc = fitz.open(output_pdf)
    for i in range(len(doc)):
        text = doc[i].get_text().strip()
        if text:
            backing_pages[str(i + 1)] = text
    doc.close()

    # Extract OCR text from .ocr.json files
    for rm_file in rm_files:
        ocr_rel_path = rm_file.get('ocr_path')
        if not ocr_rel_path:
            continue

        ocr_path = base_output_dir / ocr_rel_path
        if not ocr_path.exists():
            continue

        try:
            with open(ocr_path) as f:
                ocr_result = json.load(f)

            gcv_response = ocr_result.get('gcv_response', {})
            responses = gcv_response.get('responses', [{}])
            text_annotations = responses[0].get('textAnnotations', [])
            if text_annotations:
                full_text = text_annotations[0].get('description', '').strip()
                if full_text:
                    page_num = str(rm_file['index'] + 1)
                    ocr_pages[page_num] = full_text
        except Exception as e:
            log.warning(f"Failed to read OCR for search index: {e}")

    index = {}
    if backing_pages:
        index['backing_pages'] = backing_pages
    if ocr_pages:
        index['ocr_pages'] = ocr_pages

    with open(search_index_path, 'w') as f:
        json.dump(index, f, indent=2)

    log.info(f"Created search index: {len(backing_pages)} backing pages, {len(ocr_pages)} OCR pages")


def parse_item(
    id: str,
    files: list[Path],
    output_dir: Path,
    old_item: dict | None,
    api_key: str | None = None,
    ocr_debug: bool = False,
    no_thumbnails: bool = False
) -> tuple[dict, str, dict]:
    '''
    Given item ID and xochitl files, generate output folder containing
    thumbnails, OCR-ed output pdf and extracted text. Returns metadata for item.
    Will only process files and OCR documents that have been changed.

    :param id: UUID of item
    :param files: xochitl files corresponding to item
    :param old_item: Existing metadata for this specific item (if any)
    :param output_dir: directory to put output
    :returns: tuple of (metadata dict, status string, stats dict)
              status is one of: 'created', 'modified', 'unchanged', 'skipped'
              stats contains: thumbnails_generated, ocr_scans, words_recognized
    '''
    # Get metadata and content
    metadata = {}
    content = {}

    pages: list[str] = []
    last_opened_page = 1
    cover_page_number = 0
    parent = ''
    name = ''
    for file in files:
        if str(file).endswith('.metadata'):
            with open(file) as f:
                metadata = json.load(f)
                name = metadata.get('visibleName', '')
                parent = metadata.get('parent', None)
        if str(file).endswith('.content'):
            with open(file) as f:
                content = json.load(f)
                pages = get_pages(content)
                cover_page_number = content.get('coverPageNumber', 0)
                if 'cPages' in content:
                    last = content['cPages']\
                        .get('lastOpened', {}).get('value', '')
                    if pages and last in pages:
                        last_opened_page = pages.index(last) + 1

    # Old-format docs store lastOpenedPage (0-indexed) in .metadata
    if 'cPages' not in content and 'lastOpenedPage' in metadata:
        last_opened_page = metadata['lastOpenedPage'] + 1

    # if name != 'Colours': return {}, 'skipped', {}

    # Skip empty items
    if not content and not metadata:
        return {}, 'skipped', {}

    # Skip deleted items
    if 'parent' in metadata and metadata['parent'] == 'trash':
        log.info(f'Skipping deleted item: {name}')
        return {}, 'skipped', {}

    # Return info for folders
    is_folder = len(files) == 1 or not content
    if is_folder:
        status = 'unchanged' if old_item else 'created'
        return {
            'type': 'folder',
            'id': id,
            'name': name,
            'parent': parent
        }, status, {}

    nb_output_dir = output_dir / f'{name} - {id}'
    cached_dir_exists = nb_output_dir.exists()

    # For books: compute source hash and check against old
    source_hash = compute_source_hash(id, files)

    if old_item and old_item.get('type') == 'book':
        old_hash = old_item.get('xochitl_dir_hash', '')
        old_name = old_item.get('name', '')

        # Handle rename: if name changed, rename the output directory
        if old_name and old_name != name:
            old_dir = output_dir / f'{old_name} - {id}'
            new_dir = output_dir / f'{name} - {id}'
            if old_dir.exists():
                log.info(f'Renaming "{old_name}" to "{name}"')
                old_dir.rename(new_dir)

        # If hash unchanged, return old metadata (with updated name/parent)
        if source_hash == old_hash and cached_dir_exists:
            log.info(f'Unchanged: {name}')
            result = old_item.copy()
            result['name'] = name
            result['parent'] = parent
            return result, 'unchanged', {}

    # Hash changed or new item - do full processing
    log.info(f'Processing item: {name}')

    # Get old rm_files for OCR caching (before we modify anything)
    old_rm_files = old_item.get('rm_files', []) if old_item else []

    # Delete directories EXCEPT rm_output (needed for OCR cache) and thumbnails (for thumbnail cache)
    if nb_output_dir.exists():
        for child in nb_output_dir.iterdir():
            if child.name not in ('rm_output', 'thumbnails'):
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

    # Create output directories
    nb_xochitl_dir = nb_output_dir / 'xochitl'
    nb_thumbnail_dir = nb_output_dir / 'thumbnails'
    nb_rm_output_dir = nb_output_dir / 'rm_output'
    nb_output_dir.mkdir(parents=True, exist_ok=True)
    nb_xochitl_dir.mkdir(exist_ok=True)
    nb_thumbnail_dir.mkdir(exist_ok=True)
    nb_rm_output_dir.mkdir(exist_ok=True)

    # Copy xochitl files into nb_xochitl_dir
    rm_file_dir = None
    for file in files:
        if file.is_dir():
            cpdir = nb_xochitl_dir / file.name
            shutil.copytree(file, cpdir)
            if file.name == id:
                rm_file_dir = cpdir
        else:
            shutil.copy(file, nb_xochitl_dir)

    # Run remarks in temp directory
    output_pdf = nb_output_dir / f'{name}.pdf'
    with tempfile.TemporaryDirectory() as tmp_dir:
        remarks_out = Path(tmp_dir) / 'remarks_out'
        run_remarks(nb_xochitl_dir, remarks_out)
        expected_pdf = remarks_out / f'{name} _remarks.pdf'
        if not expected_pdf.exists():
            raise RuntimeError(f'Remarks produced no output for item "{name}"')
        shutil.copy2(expected_pdf, output_pdf)

    # Get backing PDF
    backing_pdf = None
    backing_pdf_file = nb_xochitl_dir / f'{id}.pdf'
    if backing_pdf_file.exists():
        backing_pdf = backing_pdf_file

    # Build rm_file index (with OCR if api_key available)
    rm_files = []
    new_ocr_scans = 0
    if rm_file_dir:
        rm_files, new_ocr_scans = build_rm_file_index(
            rm_file_dir, nb_rm_output_dir, output_dir, pages, content, backing_pdf_file,
            api_key=api_key,
            old_rm_files=old_rm_files
        )

    # Clean up orphaned OCR files
    current_ocr_files = set()
    for rm_file in rm_files:
        if rm_file.get('ocr_path'):
            current_ocr_files.add(Path(rm_file['ocr_path']).name)

    for f in nb_rm_output_dir.glob('*.ocr.json'):
        if f.name not in current_ocr_files:
            log.info(f"Removing orphaned OCR file: {f.name}")
            f.unlink()

    # Build search index (before OCR stitching so page.get_text() returns only backing PDF text)
    search_index_path = nb_output_dir / 'search_index.json'
    build_search_index(output_pdf, rm_files, output_dir, search_index_path)

    # Stitch OCR text layers into the final PDF
    ocr_words = 0
    if rm_files:
        _, ocr_words = stitch_ocr_text_layers(output_pdf, rm_files, output_dir, debug=ocr_debug)

    # Build page index for thumbnails
    page_index = build_page_index(rm_file_dir, pages, content)

    # Generate thumbnails (unless disabled)
    thumbnail_pages = []
    new_thumbnails = 0
    if not no_thumbnails:
        # Get old thumbnail metadata
        old_thumbnail_pages = old_item.get('thumbnail_pages', []) if old_item else []

        # Generate thumbnails
        thumbnail_pages, new_thumbnails = generate_thumbnails(
            output_pdf,
            nb_thumbnail_dir,
            output_dir,
            page_index,
            old_thumbnail_pages
        )

    xochitl_dir = str(nb_xochitl_dir.relative_to(output_dir))
    output_pdf = str(output_pdf.relative_to(output_dir))
    backing_pdf = '' if not backing_pdf else str(backing_pdf.relative_to(output_dir))
    thumbnail_dir = str(nb_thumbnail_dir.relative_to(output_dir))

    xochitl_dir_hash = xx_dir_hash(nb_xochitl_dir)

    status = 'modified' if old_item and cached_dir_exists else 'created'
    stats = {
        'thumbnails_generated': new_thumbnails,
        'ocr_scans': new_ocr_scans,
        'words_recognized': ocr_words
    }
    return {
        'type': 'book',
        'id': id,
        'name': name,
        'parent': parent,

        'xochitl_dir': xochitl_dir,
        'output_pdf': output_pdf,
        'backing_pdf': backing_pdf,
        'thumbnail_dir': thumbnail_dir,

        'xochitl_dir_hash': xochitl_dir_hash,

        'rm_files': rm_files,
        'thumbnail_pages': thumbnail_pages,

        'last_opened_page': last_opened_page,
        'cover_page_number': cover_page_number,
        'total_pages': len(pages)
    }, status, stats

def try_get_name(files: list[Path]):
    for file in files:
        if str(file).endswith('.metadata'):
            with open(file) as f:
                metadata = json.load(f)
                name = metadata.get('visibleName', '')
                return name

def run_rm_process(xochitl_dir: Path, output_dir: Path, *, no_ocr=False, ocr_debug=False, no_thumbnails=False):
    """Core processing logic. Called by both CLI and syncd."""
    old_metadata = None
    old_metadata_f = (output_dir / 'metadata.json')
    if old_metadata_f.exists():
        with open(old_metadata_f) as f:
            old_metadata = json.load(f)

    # Get GCV API key for OCR
    api_key = None
    if no_ocr:
        log.info("OCR disabled via --no-ocr flag")
    else:
        api_key = get_gcv_api_key()
        if api_key:
            log.info("GCV API key found, OCR will be enabled")
            if ocr_debug:
                log.info("OCR debug mode enabled - text will be visible")
        else:
            log.info("No GCV API key found, OCR will be disabled")

    # Build lookup from old metadata
    old_items_by_id = {item['id']: item for item in old_metadata} if old_metadata else {}
    processed_ids = set()
    summary = {'created': [], 'modified': [], 'deleted': [], 'unchanged': []}

    # Stats accumulators
    total_thumbnails = 0
    total_ocr_scans = 0
    total_words = 0

    full_metadata = []
    errors = []

    id_filemap = create_id_filemap(xochitl_dir)
    for id, files in id_filemap.items():
        old_item = old_items_by_id.get(id)
        try:
            result, status, stats = parse_item(id, files, output_dir, old_item, api_key=api_key, ocr_debug=ocr_debug, no_thumbnails=no_thumbnails)
            if result:
                full_metadata.append(result)
                processed_ids.add(id)
                if status in summary:
                    summary[status].append(result.get('name', id))
                # Accumulate stats
                total_thumbnails += stats.get('thumbnails_generated', 0)
                total_ocr_scans += stats.get('ocr_scans', 0)
                total_words += stats.get('words_recognized', 0)
        except Exception:
            name = try_get_name(files)
            tb = traceback.format_exc()
            errors.append({'name': name, 'id': id, 'error': tb})
            log.error(f'Item "{name}" (UUID: {id}) failed to parse! Traceback:\n{traceback.format_exc()}')
            # Keep old item in metadata if it exists (don't lose data on error)
            if old_item:
                full_metadata.append(old_item)
                processed_ids.add(id)

    # Handle deletions
    for id, old_item in old_items_by_id.items():
        if id not in processed_ids:
            summary['deleted'].append(old_item.get('name', id))
            # Delete output directory if it's a book
            if old_item.get('type') == 'book':
                old_dir = output_dir / f"{old_item['name']} - {id}"
                if old_dir.exists():
                    log.info(f"Deleting removed item: {old_item['name']}")
                    shutil.rmtree(old_dir)

    metadata_path = output_dir / 'metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(full_metadata, f, indent=2)

    if errors:
        errors_path = output_dir / 'errors.json'
        with open(errors_path, 'w') as f:
            json.dump(errors, f, indent=2)

    # Print summary
    print("\nSummary:")
    if summary['created']:
        print(f"  {len(summary['created'])} notebooks created: {', '.join(summary['created'])}")
    if summary['modified']:
        print(f"  {len(summary['modified'])} notebooks modified: {', '.join(summary['modified'])}")
    if summary['deleted']:
        print(f"  {len(summary['deleted'])} notebooks deleted: {', '.join(summary['deleted'])}")
    if summary['unchanged']:
        print(f"  {len(summary['unchanged'])} notebooks unchanged (skipped)")
    if total_thumbnails:
        print(f"  {total_thumbnails} thumbnails generated")
    if total_ocr_scans or total_words:
        print(f"  {total_ocr_scans} OCR scans completed ({total_words} words recognised)")
    if errors:
        print(f"  {len(errors)} errors")


def rm_process(args: argparse.Namespace):
    """CLI entry point — unpacks argparse and calls run_rm_process."""
    run_rm_process(
        Path(args.xochitl_dir),
        Path(args.output_dir),
        no_ocr=getattr(args, 'no_ocr', False),
        ocr_debug=getattr(args, 'ocr_debug', False),
        no_thumbnails=getattr(args, 'no_thumbnails', False),
    )
