"""Database-level integrity guards.

Revision ID: 0002
Revises: 0001

These enforce traceability rules that application code alone cannot guarantee:
  * original OCR text is immutable
  * criteria of an APPROVED/SUPERSEDED rubric version are frozen
  * AI evaluation scores are never rewritten (teacher changes are appended as overrides)
  * teacher overrides are append-only (only the `is_active` flag may flip)
  * the audit log is append-only
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UP = """
CREATE OR REPLACE FUNCTION mw_guard_ocr_immutable() RETURNS trigger AS $$
BEGIN
    IF NEW.text IS DISTINCT FROM OLD.text
       OR NEW.answer_page_id IS DISTINCT FROM OLD.answer_page_id
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.model IS DISTINCT FROM OLD.model
       OR NEW.confidence IS DISTINCT FROM OLD.confidence THEN
        RAISE EXCEPTION 'ocr_results are immutable; store corrections on answers.corrected_text';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_ocr_results_immutable BEFORE UPDATE ON ocr_results
    FOR EACH ROW EXECUTE FUNCTION mw_guard_ocr_immutable();

CREATE OR REPLACE FUNCTION mw_guard_rubric_criteria() RETURNS trigger AS $$
DECLARE v_status text; v_id uuid;
BEGIN
    IF TG_OP = 'INSERT' THEN v_id := NEW.rubric_version_id; ELSE v_id := OLD.rubric_version_id; END IF;
    SELECT status INTO v_status FROM rubric_versions WHERE id = v_id;
    -- version row absent => being removed by a cascade; allow
    IF v_status IS NOT NULL AND v_status <> 'DRAFT' THEN
        RAISE EXCEPTION 'rubric version % is % and its criteria are frozen; create a new version instead', v_id, v_status;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_rubric_criteria_frozen BEFORE INSERT OR UPDATE OR DELETE ON rubric_criteria
    FOR EACH ROW EXECUTE FUNCTION mw_guard_rubric_criteria();

CREATE OR REPLACE FUNCTION mw_guard_evaluations() RETURNS trigger AS $$
BEGIN
    IF NEW.ai_total IS DISTINCT FROM OLD.ai_total
       OR NEW.max_total IS DISTINCT FROM OLD.max_total
       OR NEW.answer_text_snapshot IS DISTINCT FROM OLD.answer_text_snapshot
       OR NEW.rubric_version_id IS DISTINCT FROM OLD.rubric_version_id
       OR NEW.answer_id IS DISTINCT FROM OLD.answer_id
       OR NEW.raw_output IS DISTINCT FROM OLD.raw_output THEN
        RAISE EXCEPTION 'AI evaluations are immutable; record teacher changes as overrides';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_evaluations_immutable BEFORE UPDATE ON evaluations
    FOR EACH ROW EXECUTE FUNCTION mw_guard_evaluations();

CREATE OR REPLACE FUNCTION mw_forbid_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_evaluation_criteria_immutable BEFORE UPDATE ON evaluation_criteria
    FOR EACH ROW EXECUTE FUNCTION mw_forbid_update();

CREATE OR REPLACE FUNCTION mw_guard_overrides() RETURNS trigger AS $$
BEGIN
    IF NEW.ai_score IS DISTINCT FROM OLD.ai_score
       OR NEW.teacher_score IS DISTINCT FROM OLD.teacher_score
       OR NEW.final_score IS DISTINCT FROM OLD.final_score
       OR NEW.reason IS DISTINCT FROM OLD.reason
       OR NEW.teacher_id IS DISTINCT FROM OLD.teacher_id
       OR NEW.evaluation_criterion_id IS DISTINCT FROM OLD.evaluation_criterion_id THEN
        RAISE EXCEPTION 'teacher_overrides are append-only; insert a new override instead';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_teacher_overrides_append_only BEFORE UPDATE ON teacher_overrides
    FOR EACH ROW EXECUTE FUNCTION mw_guard_overrides();

CREATE OR REPLACE FUNCTION mw_guard_audit() RETURNS trigger AS $$
BEGIN
    -- allow only the FK action that nulls actor_id when a user is erased
    IF TG_OP = 'UPDATE' AND NEW.actor_id IS NULL AND OLD.actor_id IS NOT NULL
       AND NEW.action = OLD.action AND NEW.entity_type = OLD.entity_type
       AND NEW.before IS NOT DISTINCT FROM OLD.before AND NEW.after IS NOT DISTINCT FROM OLD.after THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_logs are append-only';
END $$ LANGUAGE plpgsql;
CREATE TRIGGER trg_audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION mw_guard_audit();
"""

DOWN = """
DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs;
DROP TRIGGER IF EXISTS trg_teacher_overrides_append_only ON teacher_overrides;
DROP TRIGGER IF EXISTS trg_evaluation_criteria_immutable ON evaluation_criteria;
DROP TRIGGER IF EXISTS trg_evaluations_immutable ON evaluations;
DROP TRIGGER IF EXISTS trg_rubric_criteria_frozen ON rubric_criteria;
DROP TRIGGER IF EXISTS trg_ocr_results_immutable ON ocr_results;
DROP FUNCTION IF EXISTS mw_guard_audit();
DROP FUNCTION IF EXISTS mw_guard_overrides();
DROP FUNCTION IF EXISTS mw_forbid_update();
DROP FUNCTION IF EXISTS mw_guard_evaluations();
DROP FUNCTION IF EXISTS mw_guard_rubric_criteria();
DROP FUNCTION IF EXISTS mw_guard_ocr_immutable();
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
