import argparse
import fcntl
import hashlib
import json
import logging
import os
import tempfile
import threading
from itertools import chain

from pathlib import Path

import zipfile
from io import BytesIO

from flask import (
    Flask, Response, jsonify, request, send_file, send_from_directory,
    stream_with_context,
)

log = logging.getLogger(__name__)
from .utils import validate_path
from .rm_index import RemarkableIndex, get_metadata_version
from .rm_items import RemarkableDocument, RemarkableFolder
from .markdown import GENERATOR_SIGNATURE, load_api_key, stream_pdf_markdown

STATIC_DIR = Path(__file__).with_name("web")

def build_view_parser(parser: argparse._SubParsersAction):
    view_parser = parser.add_parser(
        'view',
        help='Then, use "view" over the output directory to serve the files '
            'through a webserver.'
    )
    view_parser.add_argument(
        "output_dir", type=validate_path,
        help="Path to processed output dir (the one containing metadata.json)"
    )
    view_parser.add_argument("--host", default="127.0.0.1")
    view_parser.add_argument("--port", type=int, default=5000)
    view_parser.add_argument("--workers", type=int, default=1,
                             help="Number of gunicorn worker processes (default: 1)")
    view_parser.add_argument("--threads", type=int, default=2,
                             help="Threads per gunicorn worker (default: 2)")
    view_parser.add_argument("--debug", action="store_true")

def create_app(output_dir: Path) -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')

    output_dir = output_dir.resolve()
    index_lock = threading.Lock()

    def load_stable_index() -> tuple[RemarkableIndex, str]:
        while True:
            version_before = get_metadata_version(output_dir)
            candidate = RemarkableIndex(output_dir)
            version_after = get_metadata_version(output_dir)
            if version_before == version_after:
                return candidate, version_after

    index, index_version = load_stable_index()

    def refresh_index(force: bool = False):
        """Reload this worker when another process publishes new metadata."""
        nonlocal index, index_version

        disk_version = get_metadata_version(output_dir)
        if not force and disk_version == index_version:
            return

        with index_lock:
            disk_version = get_metadata_version(output_dir)
            if not force and disk_version == index_version:
                return

            # Atomic metadata replacement makes matching before/after tokens a
            # reliable indication that this is one complete metadata version.
            index, index_version = load_stable_index()

    @app.before_request
    def refresh_worker_index():
        if request.path.startswith('/api/') and request.endpoint != 'api_rebuild':
            refresh_index()

    # UI
    @app.get("/")
    def serve_index():
        return send_from_directory(str(app.static_folder), "index.html")

    # --- API routes ---

    @app.post("/api/tree/batch")
    def api_batch():
        ids = request.get_json(silent=True) or []
        result = {}
        for item_id in ids:
            d = index.get_item_dict(item_id)
            if d:
                result[item_id] = d
        return jsonify(result)

    @app.get("/api/tree/<item_id>/children")
    def api_children(item_id):
        children = index.get_children(item_id)
        return jsonify(children)

    @app.get("/api/tree/<item_id>/pdf")
    def api_pdf(item_id):
        item = index.get(item_id)
        if not isinstance(item, RemarkableDocument) or not item.export_pdf:
            return 'Not found', 404
        return send_from_directory(
            str(item.export_pdf.parent), item.export_pdf.name
        )

    @app.post("/api/tree/<item_id>/markdown")
    def api_markdown(item_id):
        item = index.get(item_id)
        if (
            not isinstance(item, RemarkableDocument)
            or not item.export_pdf
            or not item.export_pdf.is_file()
        ):
            return 'Not found', 404

        options = request.get_json(silent=True) or {}
        regenerate = bool(options.get('regenerate'))
        pdf_path = item.export_pdf
        cache_dir = pdf_path.parent / 'markdown'
        cache_path = cache_dir / f'{pdf_path.stem}.md'
        metadata_path = cache_path.with_suffix('.md.meta.json')
        lock_path = cache_dir / f'.{pdf_path.stem}.lock'
        cache_dir.mkdir(parents=True, exist_ok=True)

        def pdf_digest(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
            return digest.hexdigest()

        def cache_is_current() -> bool:
            if not cache_path.is_file() or not metadata_path.is_file():
                return False
            try:
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                stat = pdf_path.stat()
            except (OSError, ValueError, TypeError):
                return False
            if metadata.get('generator_signature') != GENERATOR_SIGNATURE:
                return False
            if (
                metadata.get('pdf_size') == stat.st_size
                and metadata.get('pdf_mtime_ns') == stat.st_mtime_ns
            ):
                return True
            if metadata.get('pdf_sha256') != pdf_digest(pdf_path):
                return False

            # The bytes are unchanged; refresh the stat shortcut so later hits
            # do not need to hash the PDF again.
            metadata['pdf_size'] = stat.st_size
            metadata['pdf_mtime_ns'] = stat.st_mtime_ns
            metadata_fd, metadata_temp_name = tempfile.mkstemp(
                dir=cache_dir, prefix=f'.{metadata_path.name}.', suffix='.tmp'
            )
            metadata_temp_path = Path(metadata_temp_name)
            try:
                with os.fdopen(metadata_fd, 'w', encoding='utf-8') as metadata_file:
                    json.dump(metadata, metadata_file, indent=2)
                    metadata_file.flush()
                    os.fsync(metadata_file.fileno())
                os.replace(metadata_temp_path, metadata_path)
            finally:
                metadata_temp_path.unlink(missing_ok=True)
            return True

        lock_file = lock_path.open('a+')
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            response = Response(
                'Markdown generation is already in progress',
                status=409,
                mimetype='text/plain',
            )
            response.headers['Retry-After'] = '2'
            return response

        if not regenerate and cache_is_current():
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            response = send_file(cache_path, mimetype='text/markdown')
            response.headers['X-Markdown-Cache'] = 'HIT'
            response.headers['Cache-Control'] = 'no-store'
            return response

        generation_lock_file = (output_dir / '.markdown-generation.lock').open('a+')
        try:
            fcntl.flock(
                generation_lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            generation_lock_file.close()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            response = Response(
                'Another Markdown generation is already in progress',
                status=409,
                mimetype='text/plain',
            )
            response.headers['Retry-After'] = '2'
            return response

        try:
            api_key = load_api_key()
            while True:
                stat_before = pdf_path.stat()
                pdf_bytes = pdf_path.read_bytes()
                stat_after = pdf_path.stat()
                before = (stat_before.st_size, stat_before.st_mtime_ns)
                after = (stat_after.st_size, stat_after.st_mtime_ns)
                if before == after:
                    break
            source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
            markdown_stream = iter(stream_pdf_markdown(pdf_bytes, api_key))
            first_chunk = next(markdown_stream)
        except FileNotFoundError:
            fcntl.flock(generation_lock_file.fileno(), fcntl.LOCK_UN)
            generation_lock_file.close()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            return 'Gemini API key file not found', 503
        except Exception as error:
            fcntl.flock(generation_lock_file.fileno(), fcntl.LOCK_UN)
            generation_lock_file.close()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            status = 429 if getattr(error, 'code', None) == 429 else 502
            log.warning('Markdown generation failed before streaming: %s', error)
            return str(error), status

        markdown_fd, markdown_temp_name = tempfile.mkstemp(
            dir=cache_dir, prefix=f'.{cache_path.name}.', suffix='.tmp'
        )
        markdown_temp_path = Path(markdown_temp_name)
        metadata_temp_path = None
        markdown_file = None
        markdown_fd_open = True
        cleanup_complete = False
        cleanup_guard = threading.Lock()

        def cleanup_generation():
            nonlocal cleanup_complete, markdown_fd_open
            with cleanup_guard:
                if cleanup_complete:
                    return
                cleanup_complete = True

            close_stream = getattr(markdown_stream, 'close', None)
            if close_stream:
                try:
                    close_stream()
                except Exception as error:
                    log.warning('Failed to close Gemini stream: %s', error)
            if markdown_file and not markdown_file.closed:
                markdown_file.close()
            elif markdown_fd_open:
                os.close(markdown_fd)
                markdown_fd_open = False
            markdown_temp_path.unlink(missing_ok=True)
            if metadata_temp_path:
                metadata_temp_path.unlink(missing_ok=True)
            if not generation_lock_file.closed:
                fcntl.flock(generation_lock_file.fileno(), fcntl.LOCK_UN)
                generation_lock_file.close()
            if not lock_file.closed:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()

        @stream_with_context
        def generate():
            nonlocal markdown_fd_open, markdown_file, metadata_temp_path
            markdown_file = os.fdopen(markdown_fd, 'w', encoding='utf-8')
            markdown_fd_open = False
            try:
                for text in chain((first_chunk,), markdown_stream):
                    markdown_file.write(text)
                    yield text

                markdown_file.flush()
                os.fsync(markdown_file.fileno())
                markdown_file.close()

                publication_stat_before = pdf_path.stat()
                publication_sha256 = pdf_digest(pdf_path)
                publication_stat_after = pdf_path.stat()
                publication_before = (
                    publication_stat_before.st_size,
                    publication_stat_before.st_mtime_ns,
                )
                publication_after = (
                    publication_stat_after.st_size,
                    publication_stat_after.st_mtime_ns,
                )
                if (
                    publication_before != publication_after
                    or publication_sha256 != source_sha256
                ):
                    raise RuntimeError('PDF changed during Markdown generation')

                metadata = {
                    'pdf_sha256': source_sha256,
                    'pdf_size': publication_stat_after.st_size,
                    'pdf_mtime_ns': publication_stat_after.st_mtime_ns,
                    'generator_signature': GENERATOR_SIGNATURE,
                }
                metadata_fd, metadata_temp_name = tempfile.mkstemp(
                    dir=cache_dir, prefix=f'.{metadata_path.name}.', suffix='.tmp'
                )
                metadata_temp_path = Path(metadata_temp_name)
                with os.fdopen(metadata_fd, 'w', encoding='utf-8') as metadata_file:
                    json.dump(metadata, metadata_file, indent=2)
                    metadata_file.flush()
                    os.fsync(metadata_file.fileno())

                final_stat = pdf_path.stat()
                if (
                    final_stat.st_size,
                    final_stat.st_mtime_ns,
                ) != publication_after:
                    raise RuntimeError('PDF changed during Markdown generation')

                os.replace(markdown_temp_path, cache_path)
                os.replace(metadata_temp_path, metadata_path)
            finally:
                cleanup_generation()

        response = Response(generate(), mimetype='text/markdown')
        response.call_on_close(cleanup_generation)
        response.headers['X-Markdown-Cache'] = 'MISS'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Accel-Buffering'] = 'no'
        return response

    @app.get("/api/tree/<item_id>/thumbnail/<int:page_index>")
    def api_thumbnail(item_id, page_index):
        item = index.get(item_id)
        if not isinstance(item, RemarkableDocument):
            return 'Not found', 404
        if not item.thumbnail_pages:
            return 'Not found', 404
        # Look up by page index field, not list position
        thumb_info = None
        for tp in item.thumbnail_pages:
            if tp.get('index') == page_index:
                thumb_info = tp
                break
        if not thumb_info:
            return 'Not found', 404
        thumb_path = index.output_dir / thumb_info['thumbnail_path']
        if not thumb_path.exists():
            return 'Not found', 404
        resp = send_from_directory(str(thumb_path.parent), thumb_path.name)
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    @app.get("/api/tree/<item_id>")
    def api_item(item_id):
        d = index.get_item_dict(item_id)
        if not d:
            return 'Not found', 404
        return jsonify(d)

    @app.get("/api/search")
    def api_search():
        query = request.args.get('q', '')
        results = index.search(query)
        return jsonify({
            'query': query,
            'results': results,
        })

    @app.post("/api/rebuild")
    def api_rebuild():
        refresh_index(force=True)
        return jsonify({"status": "ok", "generation": index_version})

    @app.get("/api/generation")
    def api_generation():
        return jsonify({"generation": index_version})

    @app.get("/api/download/zip")
    def api_download_zip():
        docs = []

        def traverse(folder_id, path_prefix=""):
            folder = index.get(folder_id)
            if not isinstance(folder, RemarkableFolder):
                return
            for child_id in index.get_children(folder_id):
                child = index.get(child_id)
                if isinstance(child, RemarkableDocument) and child.export_pdf:
                    archive_path = f"{path_prefix}{child.name}.pdf" if path_prefix else f"{child.name}.pdf"
                    docs.append((archive_path, child.export_pdf))
                elif isinstance(child, RemarkableFolder):
                    new_prefix = f"{path_prefix}{child.name}/"
                    traverse(child_id, new_prefix)

        traverse('root')

        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as zf:
            for archive_path, pdf_path in docs:
                zf.write(pdf_path, arcname=archive_path)
        buf.seek(0)

        return send_file(buf, mimetype='application/zip',
                         as_attachment=True, download_name='remarkable.zip')

    return app

def rm_view(args: argparse.Namespace):
    output_dir = Path(args.output_dir)
    app = create_app(output_dir)
    log.info(f"Serving {output_dir} on http://{args.host}:{args.port}")
    if args.debug:
        app.run(host=args.host, port=args.port, debug=True)
    else:
        from gunicorn.app.base import BaseApplication

        class GunicornApp(BaseApplication):
            def __init__(self, app, options=None):
                self.application = app
                self.options = options or {}
                super().__init__()

            def load_config(self):
                for key, value in self.options.items():
                    self.cfg.set(key.lower(), value)

            def load(self):
                return self.application

        options = {
            'bind': f'{args.host}:{args.port}',
            'workers': args.workers,
            'threads': args.threads,
            'accesslog': '-',
        }
        GunicornApp(app, options).run()
