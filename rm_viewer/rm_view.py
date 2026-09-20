import argparse
import fcntl
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
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
    view_parser.add_argument(
        "--debug",
        action="store_true",
        help="Run the development server with detailed viewer diagnostics",
    )
    view_parser.add_argument(
        "--debug-logging",
        action="store_true",
        help="Enable detailed viewer diagnostics while continuing to use Gunicorn",
    )

def create_app(output_dir: Path, debug: bool = False) -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')
    app.config['VIEWER_DEBUG_LOGGING'] = debug

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
        request_id = uuid.uuid4().hex[:12]
        request_started = time.monotonic()

        def debug_log(event: str, **details):
            if not app.config['VIEWER_DEBUG_LOGGING']:
                return
            fields = ' '.join(
                f'{name}={value!r}' for name, value in sorted(details.items())
            )
            suffix = f' {fields}' if fields else ''
            log.debug(
                'markdown request=%s elapsed=%.3fs event=%s%s',
                request_id,
                time.monotonic() - request_started,
                event,
                suffix,
            )

        debug_log(
            'received',
            item_id=item_id,
            remote_addr=request.remote_addr,
            forwarded_for=request.headers.get('X-Forwarded-For'),
            user_agent=request.user_agent.string,
            content_length=request.content_length,
        )
        item = index.get(item_id)
        if (
            not isinstance(item, RemarkableDocument)
            or not item.export_pdf
            or not item.export_pdf.is_file()
        ):
            debug_log(
                'document_not_found',
                index_result_type=type(item).__name__ if item else None,
                has_export_pdf=bool(getattr(item, 'export_pdf', None)),
            )
            response = Response('Not found', status=404, mimetype='text/plain')
            response.headers['X-Markdown-Request-ID'] = request_id
            return response

        options = request.get_json(silent=True) or {}
        regenerate = bool(options.get('regenerate'))
        pdf_path = item.export_pdf
        cache_dir = pdf_path.parent / 'markdown'
        cache_path = cache_dir / f'{pdf_path.stem}.md'
        metadata_path = cache_path.with_suffix('.md.meta.json')
        lock_path = cache_dir / f'.{pdf_path.stem}.lock'
        cache_dir.mkdir(parents=True, exist_ok=True)
        debug_log(
            'document_resolved',
            regenerate=regenerate,
            pdf_path=str(pdf_path),
            cache_path=str(cache_path),
            cache_exists=cache_path.is_file(),
            metadata_exists=metadata_path.is_file(),
        )

        def pdf_digest(path: Path) -> str:
            digest = hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
            return digest.hexdigest()

        def cache_is_current() -> bool:
            if not cache_path.is_file() or not metadata_path.is_file():
                debug_log(
                    'cache_incomplete',
                    cache_exists=cache_path.is_file(),
                    metadata_exists=metadata_path.is_file(),
                )
                return False
            try:
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                stat = pdf_path.stat()
            except (OSError, ValueError, TypeError) as error:
                debug_log('cache_metadata_invalid', error=repr(error))
                return False
            if metadata.get('generator_signature') != GENERATOR_SIGNATURE:
                debug_log(
                    'cache_generator_mismatch',
                    cached_signature=str(metadata.get('generator_signature'))[:12],
                    current_signature=GENERATOR_SIGNATURE[:12],
                )
                return False
            if (
                metadata.get('pdf_size') == stat.st_size
                and metadata.get('pdf_mtime_ns') == stat.st_mtime_ns
            ):
                debug_log(
                    'cache_stat_match',
                    pdf_size=stat.st_size,
                    pdf_mtime_ns=stat.st_mtime_ns,
                )
                return True
            current_digest = pdf_digest(pdf_path)
            if metadata.get('pdf_sha256') != current_digest:
                debug_log(
                    'cache_digest_mismatch',
                    cached_digest=str(metadata.get('pdf_sha256'))[:12],
                    current_digest=current_digest[:12],
                )
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
            debug_log(
                'cache_digest_match_metadata_refreshed',
                pdf_size=stat.st_size,
                pdf_mtime_ns=stat.st_mtime_ns,
            )
            return True

        debug_log('document_lock_acquiring', lock_path=str(lock_path))
        lock_file = lock_path.open('a+')
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            debug_log('document_lock_blocked')
            response = Response(
                'Markdown generation is already in progress',
                status=409,
                mimetype='text/plain',
            )
            response.headers['Retry-After'] = '2'
            response.headers['X-Markdown-Request-ID'] = request_id
            return response
        debug_log('document_lock_acquired', lock_fd=lock_file.fileno())

        if not regenerate and cache_is_current():
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            debug_log('cache_hit', response_path=str(cache_path))
            response = send_file(cache_path, mimetype='text/markdown')
            response.headers['X-Markdown-Cache'] = 'HIT'
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Markdown-Request-ID'] = request_id
            return response

        generation_lock_path = output_dir / '.markdown-generation.lock'
        debug_log('global_lock_acquiring', lock_path=str(generation_lock_path))
        generation_lock_file = generation_lock_path.open('a+')
        try:
            fcntl.flock(
                generation_lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            generation_lock_file.close()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            debug_log('global_lock_blocked')
            response = Response(
                'Another Markdown generation is already in progress',
                status=409,
                mimetype='text/plain',
            )
            response.headers['Retry-After'] = '2'
            response.headers['X-Markdown-Request-ID'] = request_id
            return response
        debug_log('global_lock_acquired', lock_fd=generation_lock_file.fileno())

        try:
            debug_log('api_key_loading')
            api_key = load_api_key()
            debug_log('api_key_loaded')
            read_attempt = 0
            while True:
                read_attempt += 1
                stat_before = pdf_path.stat()
                pdf_bytes = pdf_path.read_bytes()
                stat_after = pdf_path.stat()
                before = (stat_before.st_size, stat_before.st_mtime_ns)
                after = (stat_after.st_size, stat_after.st_mtime_ns)
                debug_log(
                    'pdf_read',
                    attempt=read_attempt,
                    bytes=len(pdf_bytes),
                    stable=before == after,
                    stat_before=before,
                    stat_after=after,
                )
                if before == after:
                    break
            source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
            debug_log(
                'gemini_first_chunk_waiting',
                pdf_bytes=len(pdf_bytes),
                pdf_sha256=source_sha256[:12],
            )
            markdown_stream = iter(
                stream_pdf_markdown(pdf_bytes, api_key, request_id=request_id)
            )
            first_chunk = next(markdown_stream)
            debug_log('gemini_first_chunk_received', chars=len(first_chunk))
        except FileNotFoundError as error:
            fcntl.flock(generation_lock_file.fileno(), fcntl.LOCK_UN)
            generation_lock_file.close()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            debug_log(
                'generation_setup_failed',
                error_type=type(error).__name__,
                error=str(error),
                status=503,
            )
            log.exception(
                'Markdown request %s failed before streaming', request_id
            )
            response = Response(
                'Gemini API key file not found', status=503, mimetype='text/plain'
            )
            response.headers['X-Markdown-Request-ID'] = request_id
            return response
        except Exception as error:
            fcntl.flock(generation_lock_file.fileno(), fcntl.LOCK_UN)
            generation_lock_file.close()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            status = 429 if getattr(error, 'code', None) == 429 else 502
            debug_log(
                'generation_setup_failed',
                error_type=type(error).__name__,
                error=str(error),
                error_code=getattr(error, 'code', None),
                status=status,
            )
            log.exception(
                'Markdown request %s failed before streaming', request_id
            )
            response = Response(str(error), status=status, mimetype='text/plain')
            response.headers['X-Markdown-Request-ID'] = request_id
            return response

        markdown_fd, markdown_temp_name = tempfile.mkstemp(
            dir=cache_dir, prefix=f'.{cache_path.name}.', suffix='.tmp'
        )
        markdown_temp_path = Path(markdown_temp_name)
        metadata_temp_path = None
        markdown_file = None
        markdown_fd_open = True
        cleanup_complete = False
        cleanup_guard = threading.Lock()
        stream_chunks = 0
        stream_chars = 0
        stream_stage = 'response_not_started'
        published = False
        debug_log(
            'temporary_markdown_created',
            temp_path=str(markdown_temp_path),
            temp_fd=markdown_fd,
        )

        def cleanup_generation(source: str):
            nonlocal cleanup_complete, markdown_fd_open
            with cleanup_guard:
                if cleanup_complete:
                    debug_log('cleanup_skipped', source=source)
                    return
                cleanup_complete = True

            debug_log(
                'cleanup_started',
                source=source,
                stage=stream_stage,
                chunks=stream_chunks,
                chars=stream_chars,
                published=published,
                markdown_fd_open=markdown_fd_open,
                markdown_file_open=bool(markdown_file and not markdown_file.closed),
                global_lock_open=not generation_lock_file.closed,
                document_lock_open=not lock_file.closed,
            )
            close_stream = getattr(markdown_stream, 'close', None)
            if close_stream:
                try:
                    close_stream()
                except Exception as error:
                    log.exception(
                        'Markdown request %s failed to close Gemini stream',
                        request_id,
                    )
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
            debug_log(
                'cleanup_finished',
                source=source,
                markdown_temp_exists=markdown_temp_path.exists(),
                metadata_temp_exists=bool(
                    metadata_temp_path and metadata_temp_path.exists()
                ),
                global_lock_open=not generation_lock_file.closed,
                document_lock_open=not lock_file.closed,
            )

        @stream_with_context
        def generate():
            nonlocal markdown_fd_open, markdown_file, metadata_temp_path
            nonlocal published, stream_chars, stream_chunks, stream_stage
            stream_stage = 'generator_started'
            debug_log('response_generator_started')
            markdown_file = os.fdopen(markdown_fd, 'w', encoding='utf-8')
            markdown_fd_open = False
            try:
                stream_stage = 'streaming'
                for text in chain((first_chunk,), markdown_stream):
                    stream_chunks += 1
                    stream_chars += len(text)
                    markdown_file.write(text)
                    debug_log(
                        'response_chunk_yielding',
                        chunk=stream_chunks,
                        chunk_chars=len(text),
                        total_chars=stream_chars,
                    )
                    yield text

                stream_stage = 'gemini_stream_exhausted'
                debug_log(
                    'gemini_stream_exhausted',
                    chunks=stream_chunks,
                    chars=stream_chars,
                )
                markdown_file.flush()
                os.fsync(markdown_file.fileno())
                markdown_file.close()
                stream_stage = 'markdown_temp_synced'
                debug_log('markdown_temp_synced', chars=stream_chars)

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
                debug_log(
                    'publication_pdf_checked',
                    expected_sha256=source_sha256[:12],
                    actual_sha256=publication_sha256[:12],
                    stable=publication_before == publication_after,
                    stat_before=publication_before,
                    stat_after=publication_after,
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
                debug_log(
                    'temporary_metadata_created',
                    temp_path=str(metadata_temp_path),
                )
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

                stream_stage = 'publishing_cache'
                os.replace(markdown_temp_path, cache_path)
                os.replace(metadata_temp_path, metadata_path)
                published = True
                stream_stage = 'published'
                debug_log(
                    'cache_published',
                    cache_path=str(cache_path),
                    metadata_path=str(metadata_path),
                    chars=stream_chars,
                    chunks=stream_chunks,
                )
            except GeneratorExit:
                stream_stage = 'client_disconnected'
                debug_log(
                    'response_generator_closed',
                    chunks=stream_chunks,
                    chars=stream_chars,
                )
                raise
            except Exception as error:
                debug_log(
                    'streaming_failed',
                    stage=stream_stage,
                    error_type=type(error).__name__,
                    error=str(error),
                    chunks=stream_chunks,
                    chars=stream_chars,
                )
                log.exception(
                    'Markdown request %s failed while streaming at stage %s',
                    request_id,
                    stream_stage,
                )
                raise
            finally:
                cleanup_generation('generator_finally')

        response = Response(generate(), mimetype='text/markdown')
        response.call_on_close(lambda: cleanup_generation('response_close'))
        response.headers['X-Markdown-Cache'] = 'MISS'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['X-Markdown-Request-ID'] = request_id
        debug_log('response_created', status=200, cache='MISS')
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

    @app.post("/api/debug/client")
    def api_debug_client():
        if not app.config['VIEWER_DEBUG_LOGGING']:
            return 'Not found', 404
        if request.content_length and request.content_length > 16 * 1024:
            return 'Debug event too large', 413
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return 'Invalid debug event', 400
        log.debug(
            'viewer-client event=%r item_id=%r markdown_request=%r details=%s '
            'remote_addr=%r user_agent=%r',
            payload.get('event'),
            payload.get('item_id'),
            payload.get('markdown_request_id'),
            json.dumps(payload.get('details'), ensure_ascii=True)[:12_000],
            request.remote_addr,
            request.user_agent.string,
        )
        return '', 204

    @app.get("/api/generation")
    def api_generation():
        return jsonify({
            "generation": index_version,
            "viewer_debug": app.config['VIEWER_DEBUG_LOGGING'],
        })

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
    debug_logging = args.debug or getattr(args, 'debug_logging', False)
    if debug_logging:
        logging.getLogger(__package__).setLevel(logging.DEBUG)
    app = create_app(output_dir, debug=debug_logging)
    log.info(f"Serving {output_dir} on http://{args.host}:{args.port}")
    if debug_logging:
        log.warning(
            'Viewer debug logging enabled; logs may include document paths, '
            'client addresses, and browser error stacks'
        )
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
