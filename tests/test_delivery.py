from __future__ import annotations

import asyncio
import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.delivery.gitea_client import GiteaClient, build_gitea_client
from services.delivery.minio_client import MinIOClient, build_minio_client
from agents.graphs.generation_graph import GenerationState


def _run(coro):
    return asyncio.run(coro)


FILES = {
    "src/main/App.java": "public class App {}",
    "pom.xml": "<project/>",
    "README.md": "# VibeForge AI",
}


def _state(files=None):
    s = GenerationState(job_id="job-test-123", tenant_id="tenant-abc", project_id="proj-xyz")
    s.assembled_files = files if files is not None else FILES
    return s


class TestGiteaClient:
    def test_base_url_trailing_slash_stripped(self):
        c = GiteaClient("https://git.example.io/", "tok")
        assert c.base_url == "https://git.example.io"

    def test_auth_header_set(self):
        c = GiteaClient("https://git.example.io", "my-token")
        assert c._headers["Authorization"] == "token my-token"

    def test_push_files_calls_ensure_repo(self):
        c = GiteaClient("https://git.example.io", "tok")
        c._ensure_repo = AsyncMock(return_value="https://git.example.io/o/r")
        c._upsert_file = AsyncMock()
        async def run():
            mock_client = AsyncMock()
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cm.__aexit__  = AsyncMock(return_value=False)
            with patch("httpx.AsyncClient", return_value=mock_cm):
                return await c.push_files("o", "r", {"f.py": "x"})
        url = _run(run())
        assert url == "https://git.example.io/o/r"
        c._ensure_repo.assert_called_once()

    def test_push_files_calls_upsert_for_each_file(self):
        c = GiteaClient("https://git.example.io", "tok")
        c._ensure_repo = AsyncMock(return_value="https://git.example.io/o/r")
        c._upsert_file = AsyncMock()
        async def run():
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_cm.__aexit__  = AsyncMock(return_value=False)
            with patch("httpx.AsyncClient", return_value=mock_cm):
                await c.push_files("o", "r", FILES)
        _run(run())
        assert c._upsert_file.call_count == len(FILES)

    def test_build_none_without_env(self):
        with patch.dict("os.environ", {}, clear=True):
            assert build_gitea_client() is None

    def test_build_none_without_token(self):
        with patch.dict("os.environ", {"GITEA_BASE_URL": "http://git.local"}, clear=True):
            assert build_gitea_client() is None

    def test_build_none_without_url(self):
        with patch.dict("os.environ", {"GITEA_TOKEN": "tok"}, clear=True):
            assert build_gitea_client() is None

    def test_build_returns_client_with_both(self):
        env = {"GITEA_BASE_URL": "http://git.local", "GITEA_TOKEN": "tok"}
        with patch.dict("os.environ", env):
            c = build_gitea_client()
            assert isinstance(c, GiteaClient)
            assert c.base_url == "http://git.local"


class TestMinIOClient:
    def _client(self):
        return MinIOClient("http://minio:9000", "admin", "pass", "artifacts")

    def test_zip_contains_all_files(self):
        data = self._client()._create_zip(FILES)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for f in FILES:
                assert f in zf.namelist()

    def test_zip_content_preserved(self):
        data = self._client()._create_zip({"app.py": "print('hi')"})
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.read("app.py").decode() == "print('hi')"

    def test_zip_returns_bytes(self):
        assert isinstance(self._client()._create_zip(FILES), bytes)

    def test_upload_calls_put_object(self):
        c = self._client()
        c._upload_sync = MagicMock()
        _run(c.upload_bundle("k.zip", FILES))
        c._upload_sync.assert_called_once()

    def test_upload_correct_bucket(self):
        c = self._client()
        c._upload_sync = MagicMock()
        _run(c.upload_bundle("k.zip", FILES))
        assert c.bucket == "artifacts"

    def test_upload_correct_key(self):
        c = self._client()
        c._upload_sync = MagicMock()
        _run(c.upload_bundle("my/key.zip", FILES))
        assert c._upload_sync.call_args[0][0] == "my/key.zip"

    def test_upload_returns_url(self):
        c = self._client()
        c._upload_sync = MagicMock()
        url = _run(c.upload_bundle("b.zip", FILES))
        assert "minio:9000" in url and "b.zip" in url

    def test_build_none_without_env(self):
        with patch.dict("os.environ", {}, clear=True):
            assert build_minio_client() is None

    def test_build_returns_client_with_env(self):
        env = {"MINIO_ENDPOINT": "http://minio:9000",
               "MINIO_ACCESS_KEY": "admin", "MINIO_SECRET_KEY": "pass"}
        with patch.dict("os.environ", env):
            c = build_minio_client()
            assert isinstance(c, MinIOClient)
            assert c.endpoint == "http://minio:9000"

    def test_build_default_bucket(self):
        env = {"MINIO_ENDPOINT": "http://minio:9000",
               "MINIO_ACCESS_KEY": "admin", "MINIO_SECRET_KEY": "pass"}
        with patch.dict("os.environ", env):
            assert build_minio_client().bucket == "vibeforge-artifacts"

    def test_build_custom_bucket(self):
        env = {"MINIO_ENDPOINT": "http://minio:9000", "MINIO_ACCESS_KEY": "a",
               "MINIO_SECRET_KEY": "s", "MINIO_BUCKET": "custom"}
        with patch.dict("os.environ", env):
            assert build_minio_client().bucket == "custom"


class TestNodeDeliver:
    def test_stub_urls_without_clients(self):
        from agents.graphs.generation_graph import node_deliver
        result = _run(node_deliver(_state()))
        assert "git.vibeforge.io" in result["gitea_repo_url"]
        assert result["minio_bundle_key"].startswith("artifacts/")

    def test_status_delivered(self):
        from agents.graphs.generation_graph import node_deliver
        assert _run(node_deliver(_state()))["job_status"] == "delivered"

    def test_preview_url_set(self):
        from agents.graphs.generation_graph import node_deliver
        r = _run(node_deliver(_state()))
        assert r["preview_url"] and "preview" in r["preview_url"]

    def test_sbom_ref_set(self):
        from agents.graphs.generation_graph import node_deliver
        assert _run(node_deliver(_state()))["sbom_ref"].endswith(".cdx.json")

    def test_completed_at_set(self):
        from agents.graphs.generation_graph import node_deliver
        assert _run(node_deliver(_state()))["completed_at"] is not None

    def test_gitea_client_used(self):
        from agents.graphs.generation_graph import node_deliver
        gitea = AsyncMock()
        gitea.push_files = AsyncMock(return_value="https://git.real.io/t/r")
        cfg = {"configurable": {"gitea_client": gitea, "minio_client": None}}
        result = _run(node_deliver(_state(), cfg))
        gitea.push_files.assert_called_once()
        assert result["gitea_repo_url"] == "https://git.real.io/t/r"

    def test_minio_client_used(self):
        from agents.graphs.generation_graph import node_deliver
        minio = AsyncMock()
        minio.upload_bundle = AsyncMock()
        cfg = {"configurable": {"gitea_client": None, "minio_client": minio}}
        _run(node_deliver(_state(), cfg))
        minio.upload_bundle.assert_called_once()

    def test_gitea_failure_fallback(self):
        from agents.graphs.generation_graph import node_deliver
        gitea = AsyncMock()
        gitea.push_files = AsyncMock(side_effect=RuntimeError("down"))
        cfg = {"configurable": {"gitea_client": gitea, "minio_client": None}}
        result = _run(node_deliver(_state(), cfg))
        assert "git.vibeforge.io" in result["gitea_repo_url"]

    def test_minio_failure_fallback(self):
        from agents.graphs.generation_graph import node_deliver
        minio = AsyncMock()
        minio.upload_bundle = AsyncMock(side_effect=RuntimeError("down"))
        cfg = {"configurable": {"gitea_client": None, "minio_client": minio}}
        result = _run(node_deliver(_state(), cfg))
        assert result["minio_bundle_key"].startswith("artifacts/")

    def test_delivering_event_emitted(self):
        from agents.graphs.generation_graph import node_deliver
        result = _run(node_deliver(_state()))
        assert "delivering" in [e["phase"] for e in result["events"]]

    def test_no_gitea_call_with_empty_files(self):
        from agents.graphs.generation_graph import node_deliver
        gitea = AsyncMock()
        cfg = {"configurable": {"gitea_client": gitea, "minio_client": None}}
        _run(node_deliver(_state(files={}), cfg))
        gitea.push_files.assert_not_called()

    def test_no_minio_call_with_empty_files(self):
        from agents.graphs.generation_graph import node_deliver
        minio = AsyncMock()
        cfg = {"configurable": {"gitea_client": None, "minio_client": minio}}
        _run(node_deliver(_state(files={}), cfg))
        minio.upload_bundle.assert_not_called()

    def test_bundle_key_contains_job_id(self):
        from agents.graphs.generation_graph import node_deliver
        minio = AsyncMock()
        minio.upload_bundle = AsyncMock()
        cfg = {"configurable": {"gitea_client": None, "minio_client": minio}}
        _run(node_deliver(_state(), cfg))
        assert "job-test-123" in minio.upload_bundle.call_args[0][0]
    def test_sbom_json_generated_in_assembled_files(self):
        from agents.graphs.generation_graph import node_deliver
        state = _state()
        state.assembled_files = {"src/App.java": "class App {}"}
        out = _run(node_deliver(state))
        assert "sbom.json" in out["assembled_files"]
        assert "CycloneDX" in out["assembled_files"]["sbom.json"]

