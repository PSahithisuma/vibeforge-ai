from __future__ import annotations

from unittest.mock import MagicMock

from agents.graphs.nodes.completeness_validator import (
    CompletenessValidator,
    _deduplicate,
)


class TestCompletenessValidator:
    def _v(self, llm=None):
        return CompletenessValidator(llm_client=llm)

    # ── Base checks ───────────────────────────────────────────────────────────

    def test_empty_spec_returns_gaps(self):
        assert len(self._v().validate({})) >= 4

    def test_returns_list(self):
        assert isinstance(self._v().validate({}), list)

    def test_all_items_are_strings(self):
        assert all(isinstance(g, str) for g in self._v().validate({}))

    def test_auth_question_present_for_empty_spec(self):
        gaps = self._v().validate({})
        assert any("auth" in q.lower() for q in gaps)

    def test_database_question_present_for_empty_spec(self):
        gaps = self._v().validate({})
        assert any("database" in q.lower() for q in gaps)

    def test_file_storage_question_present(self):
        gaps = self._v().validate({})
        assert any("file" in q.lower() for q in gaps)

    def test_pagination_question_present(self):
        gaps = self._v().validate({})
        assert any("pagination" in q.lower() for q in gaps)

    # ── Spec hints suppress questions ─────────────────────────────────────────

    def test_jwt_suppresses_auth_question(self):
        gaps = self._v().validate({"security": "JWT"})
        assert not any("auth" in q.lower() for q in gaps)

    def test_oauth_suppresses_auth_question(self):
        gaps = self._v().validate({"auth_type": "oauth"})
        assert not any("auth" in q.lower() for q in gaps)

    def test_postgres_suppresses_db_question(self):
        gaps = self._v().validate({"database": "postgresql"})
        assert not any("database" in q.lower() for q in gaps)

    def test_mysql_suppresses_db_question(self):
        gaps = self._v().validate({"db": "mysql"})
        assert not any("database" in q.lower() for q in gaps)

    def test_s3_suppresses_file_question(self):
        gaps = self._v().validate({"storage": "s3"})
        assert not any("file" in q.lower() for q in gaps)

    def test_minio_suppresses_file_question(self):
        gaps = self._v().validate({"storage": "minio"})
        assert not any("file" in q.lower() for q in gaps)

    def test_pagination_hint_suppresses_question(self):
        gaps = self._v().validate({"api": {"pagination": "cursor"}})
        assert not any("pagination" in q.lower() for q in gaps)

    def test_fully_specified_spec_returns_empty(self):
        spec = {
            "security": "jwt",
            "database": "postgresql",
            "storage": "s3",
            "pagination": "cursor",
            "build": "maven",
            "spring_security": "yes",
        }
        gaps = self._v().validate(spec, stack_profile="java_spring")
        assert gaps == []

    # ── Stack-specific checks ─────────────────────────────────────────────────

    def test_java_spring_adds_build_tool_question(self):
        gaps = self._v().validate({}, stack_profile="java_spring")
        assert any("build tool" in q.lower() for q in gaps)

    def test_java_spring_adds_spring_security_question(self):
        gaps = self._v().validate({}, stack_profile="java_spring")
        assert any("spring security" in q.lower() for q in gaps)

    def test_maven_suppresses_build_tool_question(self):
        gaps = self._v().validate({"build": "maven"}, stack_profile="java_spring")
        assert not any("build tool" in q.lower() for q in gaps)

    def test_python_fastapi_adds_alembic_question(self):
        gaps = self._v().validate({}, stack_profile="python_fastapi")
        assert any("alembic" in q.lower() for q in gaps)

    def test_python_fastapi_adds_async_question(self):
        gaps = self._v().validate({}, stack_profile="python_fastapi")
        assert any("async" in q.lower() for q in gaps)

    def test_node_express_adds_orm_question(self):
        gaps = self._v().validate({}, stack_profile="node_express")
        assert any("orm" in q.lower() for q in gaps)

    def test_node_express_adds_typescript_question(self):
        gaps = self._v().validate({}, stack_profile="node_express")
        assert any("typescript" in q.lower() for q in gaps)

    def test_unknown_stack_only_base_checks(self):
        gaps = self._v().validate({}, stack_profile="cobol_mainframe")
        assert len(gaps) == 4

    # ── Domain keyword detection ──────────────────────────────────────────────

    def test_payment_keyword_adds_provider_question(self):
        gaps = self._v().validate({"description": "payment processing"})
        assert any("payment provider" in q.lower() for q in gaps)

    def test_payment_keyword_adds_currency_question(self):
        gaps = self._v().validate({"description": "payment processing"})
        assert any("currenc" in q.lower() for q in gaps)

    def test_email_keyword_adds_provider_question(self):
        gaps = self._v().validate({"features": "email notification"})
        assert any("email provider" in q.lower() for q in gaps)

    def test_notification_keyword_adds_channel_question(self):
        gaps = self._v().validate({"description": "push notification system"})
        assert any("notification channel" in q.lower() for q in gaps)

    def test_search_keyword_adds_backend_question(self):
        gaps = self._v().validate({"description": "full text search"})
        assert any("search backend" in q.lower() for q in gaps)

    def test_sendgrid_suppresses_email_provider_question(self):
        gaps = self._v().validate({"email": "sendgrid notification system"})
        assert not any("email provider" in q.lower() for q in gaps)

    # ── Existing answers ──────────────────────────────────────────────────────

    def test_answered_auth_skips_auth_question(self):
        gaps = self._v().validate({}, existing_answers={"auth": "JWT"})
        assert not any("auth" in q.lower() for q in gaps)

    def test_answered_database_skips_db_question(self):
        gaps = self._v().validate({}, existing_answers={"database": "PostgreSQL"})
        assert not any("database" in q.lower() for q in gaps)

    def test_partial_answers_still_gap_remaining(self):
        gaps = self._v().validate(
            {}, existing_answers={"auth": "JWT", "database": "postgres"}
        )
        assert any("file" in q.lower() for q in gaps)

    def test_all_base_answers_no_base_questions(self):
        answers = {
            "auth": "JWT", "database": "postgres",
            "file_storage": "no", "pagination": "yes",
        }
        gaps = self._v().validate({}, existing_answers=answers)
        assert not any("auth" in q.lower() for q in gaps)
        assert not any("database" in q.lower() for q in gaps)

    def test_all_answers_for_stack_returns_empty(self):
        answers = {
            "auth": "JWT", "database": "postgres",
            "file_storage": "no", "pagination": "yes",
            "build_tool": "Maven", "spring_security": "yes",
        }
        gaps = self._v().validate({}, stack_profile="java_spring", existing_answers=answers)
        assert gaps == []

    # ── LLM integration ───────────────────────────────────────────────────────

    def test_llm_failure_still_returns_rule_gaps(self):
        llm = MagicMock()
        llm.invoke = MagicMock(side_effect=RuntimeError("llm down"))
        gaps = CompletenessValidator(llm_client=llm).validate({})
        assert len(gaps) >= 4

    def test_no_llm_works_correctly(self):
        assert len(CompletenessValidator().validate({})) >= 4

    # ── GenerationState gap fields ────────────────────────────────────────────

    def test_generation_state_has_gap_questions(self):
        from agents.graphs.generation_graph import GenerationState
        s = GenerationState()
        assert hasattr(s, "gap_questions")
        assert isinstance(s.gap_questions, list)

    def test_generation_state_has_gap_answers(self):
        from agents.graphs.generation_graph import GenerationState
        s = GenerationState()
        assert hasattr(s, "gap_answers")
        assert isinstance(s.gap_answers, dict)

    def test_gap_questions_default_empty(self):
        from agents.graphs.generation_graph import GenerationState
        assert GenerationState().gap_questions == []

    def test_gap_answers_default_empty(self):
        from agents.graphs.generation_graph import GenerationState
        assert GenerationState().gap_answers == {}


# ── TestDeduplicate ───────────────────────────────────────────────────────────

    def test_llm_enrich_parses_json_array(self):
        llm = MagicMock()
        response = MagicMock()
        response.content = '["What is the rate limiting strategy?", "Is audit logging required?"]'
        llm.invoke.return_value = response

        validator = CompletenessValidator(llm_client=llm)
        gaps = validator.validate({"security": "jwt", "database": "postgresql"})
        assert any("rate limiting" in q.lower() for q in gaps)
        assert any("audit logging" in q.lower() for q in gaps)

    def test_llm_enrich_parses_bullet_fallback(self):
        llm = MagicMock()
        response = MagicMock()
        response.content = "Here are the questions:\n1. What is the cache eviction policy?\n2. How are webhooks secured?"
        llm.invoke.return_value = response

        validator = CompletenessValidator(llm_client=llm)
        gaps = validator.validate({"security": "jwt", "database": "postgresql"})
        assert any("cache eviction" in q.lower() for q in gaps)
        assert any("webhooks" in q.lower() for q in gaps)

    def test_llm_enrich_handles_empty_response(self):
        llm = MagicMock()
        response = MagicMock()
        response.content = "[]"
        llm.invoke.return_value = response

        validator = CompletenessValidator(llm_client=llm)
        gaps = validator.validate({"security": "jwt", "database": "postgresql"})
        assert not any("rate limiting" in q.lower() for q in gaps)


class TestDeduplicate:
    def test_removes_duplicates(self):
        assert _deduplicate(["a", "b", "a", "c"]) == ["a", "b", "c"]

    def test_preserves_insertion_order(self):
        assert _deduplicate(["c", "b", "a"]) == ["c", "b", "a"]

    def test_empty_list(self):
        assert _deduplicate([]) == []

    def test_no_duplicates_unchanged(self):
        assert _deduplicate(["x", "y", "z"]) == ["x", "y", "z"]

    def test_single_element(self):
        assert _deduplicate(["x"]) == ["x"]
