import logging
import argparse

from pathlib import Path

import zipfile
from io import BytesIO

from flask import Flask, send_from_directory, send_file, request, jsonify

log = logging.getLogger(__name__)
from .utils import validate_path
from .rm_index import RemarkableIndex
from .rm_items import RemarkableDocument, RemarkableFolder

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
    view_parser.add_argument("--debug", action="store_true")

def create_app(output_dir: Path) -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')

    index = RemarkableIndex(output_dir)
    generation = 0

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
        nonlocal index, generation
        index = RemarkableIndex(output_dir)
        generation += 1
        return jsonify({"status": "ok"})

    @app.get("/api/generation")
    def api_generation():
        return jsonify({"generation": generation})

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

        options = {'bind': f'{args.host}:{args.port}', 'workers': args.workers, 'accesslog': '-'}
        GunicornApp(app, options).run()
