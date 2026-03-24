from pathlib import Path

from fastapi.testclient import TestClient

from repograph.api import routes


def test_health_returns_200(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(routes, "DEFAULT_DB_PATH", str(tmp_path / "graph"))
    client = TestClient(routes.create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_index_temp_repo_returns_files_indexed(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")

    monkeypatch.setattr(routes, "DEFAULT_DB_PATH", str(tmp_path / "graph"))
    monkeypatch.setattr(routes, "parse_file", lambda *args, **kwargs: [])
    client = TestClient(routes.create_app())

    response = client.post("/index", json={"repo_path": str(repo), "force": True})

    assert response.status_code == 200
    assert response.json()["files_indexed"] >= 0


def test_status_after_index_returns_indexed_true(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")

    monkeypatch.setattr(routes, "DEFAULT_DB_PATH", str(tmp_path / "graph"))
    monkeypatch.setattr(routes, "parse_file", lambda *args, **kwargs: [])
    client = TestClient(routes.create_app())

    index_response = client.post("/index", json={"repo_path": str(repo), "force": True})
    status_response = client.get("/status")

    assert index_response.status_code == 200
    assert status_response.status_code == 200
    assert status_response.json()["indexed"] is True
