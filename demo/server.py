"""Indian Legal KG-RAG Interactive Web Demo Server.
Serves a sleek Vanilla HTML/CSS/JS frontend and provides REST endpoints for live
multi-mode queries, community inspection, and benchmark metrics.
"""

import os
import sys
import json
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Ensure root directory is in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)

from src.rag_pipeline import LegalRAGPipeline

PORT = 8080
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# Global pipeline instance
pipeline = None

def get_pipeline():
    global pipeline
    if pipeline is None:
        print("Initializing LegalRAGPipeline on demand...")
        pipeline = LegalRAGPipeline(regulator="RBI", enable_graph=True, context_budget_chars=5000)
        pipeline.initialize()
        print("LegalRAGPipeline ready.")
    return pipeline

# Load cached community metadata & metrics
with open(os.path.join(BASE_DIR, "data", "community_reports", "inspection_metrics.json"), "r", encoding="utf-8") as f:
    INSPECTION_METRICS = json.load(f)

with open(os.path.join(BASE_DIR, "data", "community_reports", "reports.json"), "r", encoding="utf-8") as f:
    REPORTS = json.load(f)

with open(os.path.join(BASE_DIR, "data", "global_benchmark_questions.json"), "r", encoding="utf-8") as f:
    BENCHMARK_QUESTIONS = json.load(f)

with open(os.path.join(BASE_DIR, "data", "global_evaluation_results.json"), "r", encoding="utf-8") as f:
    EVAL_RESULTS = json.load(f)


class LegalRAGRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/metrics":
            self.send_json_response({
                "status": "success",
                "metrics": INSPECTION_METRICS,
                "eval_summary": EVAL_RESULTS.get("summary_statistics", {})
            })
            return

        elif path == "/api/benchmark_questions":
            self.send_json_response({
                "status": "success",
                "questions": BENCHMARK_QUESTIONS
            })
            return

        elif path == "/api/communities":
            top_comms = INSPECTION_METRICS.get("top_l0_communities", [])
            self.send_json_response({
                "status": "success",
                "top_communities": top_comms,
                "total_count": INSPECTION_METRICS.get("total_communities", 0)
            })
            return

        elif path.startswith("/api/report/"):
            comm_id = path.replace("/api/report/", "")
            report = REPORTS.get(comm_id)
            if report:
                self.send_json_response({"status": "success", "report": report})
            else:
                self.send_json_response({"status": "error", "message": "Report not found"}, status=404)
            return

        # Default static file serving
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/query":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode("utf-8"))
            except Exception as e:
                self.send_json_response({"status": "error", "message": f"Invalid JSON: {str(e)}"}, status=400)
                return

            query_text = body.get("query", "").strip()
            mode = body.get("mode", "global_with_evidence")
            level = int(body.get("level", 0))

            if not query_text:
                self.send_json_response({"status": "error", "message": "Query cannot be empty"}, status=400)
                return

            pipe = get_pipeline()
            start_t = time.perf_counter()

            try:
                if mode == "global_community":
                    res = pipe.global_searcher.search(query_text, target_level=level)
                elif mode == "global_with_evidence":
                    res = pipe.global_searcher.search_with_evidence(query_text, target_level=level)
                else:
                    res = pipe.retrieve(query_text, mode=mode, top_k=5)

                elapsed = (time.perf_counter() - start_t) * 1000.0

                self.send_json_response({
                    "status": "success",
                    "query": query_text,
                    "mode": mode,
                    "elapsed_ms": round(elapsed, 2),
                    "result": res
                })
            except Exception as e:
                self.send_json_response({"status": "error", "message": str(e)}, status=500)
            return

        self.send_json_response({"status": "error", "message": "Endpoint not found"}, status=404)

    def send_json_response(self, data, status=200):
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()


def run_server(port=PORT):
    server_address = ("", port)
    httpd = HTTPServer(server_address, LegalRAGRequestHandler)
    print(f"================================================================")
    print(f"  Indian Legal KG-RAG Web Demo running at:")
    print(f"  http://localhost:{port} / http://127.0.0.1:{port}")
    print(f"================================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        httpd.server_close()


if __name__ == "__main__":
    run_server()
