from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from agents.graphs.generation_graph import GenerationState, JobStatus, run_generation_job


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _pipeline(**kwargs):
    defaults = dict(
        job_id=str(uuid4()), tenant_id="t-integ",
        postgres_dsn=None, llm_client=None,
        spec_snapshot={}, stack_profile="java_spring",
    )
    defaults.update(kwargs)
    return _run(run_generation_job(**defaults))


class TestPipelineCompletion:
    def test_status_delivered(self):
        assert _pipeline().job_status == JobStatus.DELIVERED

    def test_preview_url_set(self):
        r = _pipeline()
        assert r.preview_url and len(r.preview_url) > 10

    def test_gitea_url_set(self):
        assert _pipeline().gitea_repo_url is not None

    def test_minio_key_set(self):
        assert _pipeline().minio_bundle_key is not None

    def test_sbom_ref_set(self):
        r = _pipeline()
        assert r.sbom_ref and ".cdx.json" in r.sbom_ref

    def test_completed_at_set(self):
        assert _pipeline().completed_at is not None

    def test_module_plan_populated(self):
        r = _pipeline()
        assert isinstance(r.module_plan, list) and len(r.module_plan) >= 1

    def test_assembled_files_is_dict(self):
        assert isinstance(_pipeline().assembled_files, dict)

    def test_unique_job_ids_give_unique_urls(self):
        r1 = _pipeline(job_id=str(uuid4()))
        r2 = _pipeline(job_id=str(uuid4()))
        assert r1.preview_url != r2.preview_url

    def test_pipeline_idempotent_on_second_run(self):
        r1 = _pipeline()
        r2 = _pipeline()
        assert r1.job_status == r2.job_status == JobStatus.DELIVERED


class TestEventSequence:
    def _phases(self, r):
        return [e["phase"] if isinstance(e, dict) else e.phase.value for e in r.events]

    def test_has_events(self):
        assert len(_pipeline().events) >= 4

    def test_planning_phase_present(self):
        assert "planning" in self._phases(_pipeline())

    def test_delivering_phase_present(self):
        assert "delivering" in self._phases(_pipeline())

    def test_planning_before_delivering(self):
        phases = self._phases(_pipeline())
        assert phases.index("planning") < phases.index("delivering")

    def test_all_events_have_job_id(self):
        for e in _pipeline().events:
            jid = e.get("job_id") if isinstance(e, dict) else e.job_id
            assert jid


class TestDeliveryClientWiring:
    def test_gitea_client_called(self):
        gitea = AsyncMock()
        gitea.push_files = AsyncMock(return_value="https://git.test.io/t/r")
        r = _pipeline(gitea_client=gitea)
        gitea.push_files.assert_called_once()
        assert r.gitea_repo_url == "https://git.test.io/t/r"

    def test_minio_client_called(self):
        minio = AsyncMock()
        minio.upload_bundle = AsyncMock()
        r = _pipeline(minio_client=minio)
        minio.upload_bundle.assert_called_once()
        assert r.minio_bundle_key is not None

    def test_both_clients_wired(self):
        gitea = AsyncMock()
        gitea.push_files = AsyncMock(return_value="https://git.test.io/t/r")
        minio = AsyncMock()
        minio.upload_bundle = AsyncMock()
        r = _pipeline(gitea_client=gitea, minio_client=minio)
        assert r.job_status == JobStatus.DELIVERED
        gitea.push_files.assert_called_once()
        minio.upload_bundle.assert_called_once()

    def test_gitea_failure_does_not_abort(self):
        gitea = AsyncMock()
        gitea.push_files = AsyncMock(side_effect=RuntimeError("gitea down"))
        r = _pipeline(gitea_client=gitea)
        assert r.job_status == JobStatus.DELIVERED
        assert "git.vibeforge.io" in r.gitea_repo_url

    def test_minio_failure_does_not_abort(self):
        minio = AsyncMock()
        minio.upload_bundle = AsyncMock(side_effect=RuntimeError("minio down"))
        r = _pipeline(minio_client=minio)
        assert r.job_status == JobStatus.DELIVERED