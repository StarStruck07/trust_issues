import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CRD_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from crd import db
    db.init_db()
    yield
