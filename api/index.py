"""Vercel entry point: a read-only hosted demo of Trust Issues.

Serverless functions have a read-only filesystem and no long-running background work, so this
serves the bundled fictional demo portfolio (copied to /tmp at cold start) in mock mode and turns
"Run review" off. Real reviews run on a machine with a persistent disk (uvicorn).
"""
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault("CRD_MOCK", "1")
os.environ.setdefault("CRD_HOSTED", "1")
os.environ.setdefault("CRD_DB_PATH", "/tmp/crd.db")
os.environ.setdefault("CRD_VAR", "/tmp/var")
os.environ.setdefault("CRD_CONFIG", os.path.join(ROOT, "config.yaml"))
os.environ.setdefault("TEAM_INBOX", "team-inbox@example.com")
if not os.path.exists(os.environ["CRD_DB_PATH"]):
    shutil.copy(os.path.join(ROOT, "deploy", "demo.db"), os.environ["CRD_DB_PATH"])

from crd.web.app import app  # noqa: E402,F401
