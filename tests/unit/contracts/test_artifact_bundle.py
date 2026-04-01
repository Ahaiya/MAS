"""
Tests for Artifact Bundle Contracts

Tests cover:
- ArtifactRef creation and validation
- ArtifactBundle structure and methods
- RubricSnapshot lookup and validation
- PolicySnapshot structure
- ResolvedArtifactBundle version tracking
"""

import pytest
from datetime import datetime

from src.contracts.artifact_bundle import (
    SchemaVersion,
    ArtifactRef,
    ArtifactBundle,
    RubricSnapshot,
    PolicySnapshot,
    ResolvedArtifactBundle,
    create_artifact_ref,
)


class TestArtifactRef:
    """Tests for ArtifactRef contract."""

    def test_create_artifact_ref(self):
        """Test creating an artifact reference."""
        ref = ArtifactRef(
            ref_uri="rubric://asap_set8_writing_traits/v1",
            source_file="rubrics/asap_set8_baseline.yaml",
            loaded_data=None,
            content_hash=None
        )

        assert ref.ref_uri == "rubric://asap_set8_writing_traits/v1"
        assert ref.source_file == "rubrics/asap_set8_baseline.yaml"
        assert ref.loaded_data is None
        assert ref.content_hash is None

    def test_create_artifact_ref_with_data(self):
        """Test creating an artifact reference with loaded data."""
        sample_data = {"rubric_id": "test", "dimensions": []}
        ref = ArtifactRef(
            ref_uri="rubric://test/v1",
            source_file="test.yaml",
            loaded_data=sample_data,
            content_hash="abc123"
        )

        assert ref.loaded_data == sample_data
        assert ref.content_hash == "abc123"

    def test_factory_function(self):
        """Test create_artifact_ref factory function."""
        ref = create_artifact_ref(
            ref_uri="policy://test/v1",
            source_file="policies/test.yaml"
        )

        assert ref.ref_uri == "policy://test/v1"
        assert ref.source_file == "policies/test.yaml"


class TestArtifactBundle:
    """Tests for ArtifactBundle contract."""

    def test_artifact_bundle_structure(self):
        """Test creating a complete artifact bundle."""
        rubric_ref = create_artifact_ref(
            "rubric://asap_set8_writing_traits/v1",
            "rubrics/asap_set8_baseline.yaml"
        )
        adj_ref = create_artifact_ref(
            "policy://asap_set8_default_adjudication/v1",
            "policies/adjudication/asap_set8_default.yaml"
        )
        agg_ref = create_artifact_ref(
            "policy://asap_set8_composite/v1",
            "policies/aggregation/asap_set8_composite.yaml"
        )
        exp_ref = create_artifact_ref(
            "explain://engineering_eval_explanation/v1",
            "policies/explanation/engineering_eval_explanation.yaml"
        )
        prompt_ref = create_artifact_ref(
            "ops://prompts/evidence_extraction/v1",
            "prompts/evidence_extraction.yaml"
        )

        bundle = ArtifactBundle(
            bundle_id="asap_set8_baseline",
            bundle_version="2026-03-12",
            bundle_name="ASAP Set 8 Baseline Bundle",
            description="Complete configuration bundle for ASAP Set 8",
            schema_version=SchemaVersion.V2_0,
            rubric_ref=rubric_ref,
            adjudication_policy_ref=adj_ref,
            aggregation_policy_ref=agg_ref,
            explanation_policy_ref=exp_ref,
            prompt_refs=[prompt_ref],
            source_documents=[
                "docs/Rubric_Guidelines.md",
                "docs/Adjudication_Rules.md",
                "docs/Example.md"
            ],
            validation_rules=[
                {"rule_id": "all_refs_resolvable", "type": "reference_resolution"}
            ],
            metadata={"target_dataset": "ASAP Set 8"}
        )

        assert bundle.bundle_id == "asap_set8_baseline"
        assert bundle.bundle_version == "2026-03-12"
        assert bundle.schema_version == SchemaVersion.V2_0
        assert len(bundle.source_documents) == 3
        assert not bundle.is_frozen()  # Not frozen yet

    def test_get_all_refs(self):
        """Test getting all artifact references from bundle."""
        rubric_ref = create_artifact_ref("rubric://test/v1", "test.yaml")
        adj_ref = create_artifact_ref("policy://adj/v1", "adj.yaml")
        agg_ref = create_artifact_ref("policy://agg/v1", "agg.yaml")
        exp_ref = create_artifact_ref("explain://exp/v1", "exp.yaml")
        prompt_refs = [
            create_artifact_ref("ops://p1/v1", "p1.yaml"),
            create_artifact_ref("ops://p2/v1", "p2.yaml"),
        ]

        bundle = ArtifactBundle(
            bundle_id="test",
            bundle_version="v1",
            bundle_name="Test",
            description="Test",
            schema_version=SchemaVersion.V2_0,
            rubric_ref=rubric_ref,
            adjudication_policy_ref=adj_ref,
            aggregation_policy_ref=agg_ref,
            explanation_policy_ref=exp_ref,
            prompt_refs=prompt_refs,
            source_documents=[],
        )

        all_refs = bundle.get_all_refs()
        assert len(all_refs) == 6  # 4 policy refs + 2 prompt refs

    def test_bundle_immutability(self):
        """Test that ArtifactBundle is frozen (immutable)."""
        bundle = ArtifactBundle(
            bundle_id="test",
            bundle_version="v1",
            bundle_name="Test",
            description="Test",
            schema_version=SchemaVersion.V2_0,
            rubric_ref=create_artifact_ref("rubric://test/v1", "test.yaml"),
            adjudication_policy_ref=create_artifact_ref("policy://adj/v1", "adj.yaml"),
            aggregation_policy_ref=create_artifact_ref("policy://agg/v1", "agg.yaml"),
            explanation_policy_ref=create_artifact_ref("explain://exp/v1", "exp.yaml"),
            prompt_refs=[],
            source_documents=[],
        )

        # Should raise error when trying to modify frozen dataclass
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError is not always available
            bundle.bundle_id = "modified"


class TestRubricSnapshot:
    """Tests for RubricSnapshot contract."""

    @pytest.fixture
    def sample_dimension(self):
        """Sample dimension for testing."""
        return {
            "dimension_id": "ideas_content",
            "code": "I",
            "name": "Ideas and Content",
            "scale_ref": "ordinal_6",
        }

    @pytest.fixture
    def sample_scale(self):
        """Sample scale for testing."""
        return {
            "scale_id": "ordinal_6",
            "type": "ordinal",
            "min": 1,
            "max": 6,
        }

    def test_rubric_snapshot_creation(self, sample_dimension, sample_scale):
        """Test creating a rubric snapshot with lookup maps."""
        snapshot = RubricSnapshot(
            rubric_id="asap_set8_writing_traits",
            rubric_version="v1",
            rubric_name="ASAP Set 8 Writing Traits",
            dimensions=[sample_dimension],
            scales=[sample_scale],
            dimension_by_id={"ideas_content": sample_dimension},
            dimension_by_code={"I": sample_dimension},
            scale_by_id={"ordinal_6": sample_scale},
        )

        assert snapshot.rubric_id == "asap_set8_writing_traits"
        assert snapshot.rubric_version == "v1"
        assert len(snapshot.dimensions) == 1

    def test_get_dimension_by_id(self, sample_dimension, sample_scale):
        """Test getting dimension by ID."""
        snapshot = RubricSnapshot(
            rubric_id="test",
            rubric_version="v1",
            rubric_name="Test",
            dimensions=[sample_dimension],
            scales=[sample_scale],
            dimension_by_id={"ideas_content": sample_dimension},
        )

        dimension = snapshot.get_dimension("ideas_content")
        assert dimension is not None
        assert dimension["code"] == "I"

    def test_get_dimension_by_code(self, sample_dimension, sample_scale):
        """Test getting dimension by code."""
        snapshot = RubricSnapshot(
            rubric_id="test",
            rubric_version="v1",
            rubric_name="Test",
            dimensions=[sample_dimension],
            scales=[sample_scale],
            dimension_by_code={"I": sample_dimension},
        )

        dimension = snapshot.get_dimension_by_code("I")
        assert dimension is not None
        assert dimension["dimension_id"] == "ideas_content"

    def test_get_dimension_not_found(self, sample_dimension, sample_scale):
        """Test getting non-existent dimension returns None."""
        snapshot = RubricSnapshot(
            rubric_id="test",
            rubric_version="v1",
            rubric_name="Test",
            dimensions=[sample_dimension],
            scales=[sample_scale],
            dimension_by_id={},
        )

        dimension = snapshot.get_dimension("nonexistent")
        assert dimension is None

    def test_get_scale(self, sample_scale):
        """Test getting scale by ID."""
        snapshot = RubricSnapshot(
            rubric_id="test",
            rubric_version="v1",
            rubric_name="Test",
            dimensions=[],
            scales=[sample_scale],
            scale_by_id={"ordinal_6": sample_scale},
        )

        scale = snapshot.get_scale("ordinal_6")
        assert scale is not None
        assert scale["min"] == 1
        assert scale["max"] == 6

    def test_validate_score_valid(self, sample_dimension, sample_scale):
        """Test validating a score within range."""
        snapshot = RubricSnapshot(
            rubric_id="test",
            rubric_version="v1",
            rubric_name="Test",
            dimensions=[sample_dimension],
            scales=[sample_scale],
            dimension_by_id={"ideas_content": sample_dimension},
            scale_by_id={"ordinal_6": sample_scale},
        )

        assert snapshot.validate_score("ideas_content", 3) is True
        assert snapshot.validate_score("ideas_content", 6) is True
        assert snapshot.validate_score("ideas_content", 1) is True

    def test_validate_score_out_of_range(self, sample_dimension, sample_scale):
        """Test validating a score out of range."""
        snapshot = RubricSnapshot(
            rubric_id="test",
            rubric_version="v1",
            rubric_name="Test",
            dimensions=[sample_dimension],
            scales=[sample_scale],
            dimension_by_id={"ideas_content": sample_dimension},
            scale_by_id={"ordinal_6": sample_scale},
        )

        assert snapshot.validate_score("ideas_content", 0) is False
        assert snapshot.validate_score("ideas_content", 7) is False

    def test_validate_score_nonexistent_dimension(self, sample_dimension, sample_scale):
        """Test validating score for non-existent dimension."""
        snapshot = RubricSnapshot(
            rubric_id="test",
            rubric_version="v1",
            rubric_name="Test",
            dimensions=[sample_dimension],
            scales=[sample_scale],
            dimension_by_id={"ideas_content": sample_dimension},
            scale_by_id={"ordinal_6": sample_scale},
        )

        assert snapshot.validate_score("nonexistent", 3) is False


class TestPolicySnapshot:
    """Tests for PolicySnapshot contract."""

    def test_policy_snapshot_creation(self):
        """Test creating a policy snapshot."""
        snapshot = PolicySnapshot(
            adjudication_policy={"triggers": []},
            aggregation_policy={"formula": "test"},
            explanation_policy={"requirements": []},
            policy_version="v1"
        )

        assert snapshot.policy_version == "v1"
        assert "triggers" in snapshot.adjudication_policy
        assert "formula" in snapshot.aggregation_policy
        assert "requirements" in snapshot.explanation_policy

    def test_policy_snapshot_immutability(self):
        """Test that PolicySnapshot is frozen (immutable)."""
        snapshot = PolicySnapshot(
            adjudication_policy={},
            aggregation_policy={},
            explanation_policy={},
            policy_version="v1"
        )

        # Should raise error when trying to modify frozen dataclass
        with pytest.raises(Exception):
            snapshot.policy_version = "modified"


class TestResolvedArtifactBundle:
    """Tests for ResolvedArtifactBundle contract."""

    @pytest.fixture
    def artifact_bundle(self):
        """Sample artifact bundle for testing."""
        return ArtifactBundle(
            bundle_id="test_bundle",
            bundle_version="v1",
            bundle_name="Test Bundle",
            description="Test description",
            schema_version=SchemaVersion.V2_0,
            rubric_ref=create_artifact_ref("rubric://test/v1", "test.yaml"),
            adjudication_policy_ref=create_artifact_ref("policy://adj/v1", "adj.yaml"),
            aggregation_policy_ref=create_artifact_ref("policy://agg/v1", "agg.yaml"),
            explanation_policy_ref=create_artifact_ref("explain://exp/v1", "exp.yaml"),
            prompt_refs=[],
            source_documents=[],
            freeze_hash="abc123",
            freeze_timestamp=datetime(2026, 3, 12, 10, 30),
        )

    @pytest.fixture
    def rubric_snapshot(self):
        """Sample rubric snapshot for testing."""
        return RubricSnapshot(
            rubric_id="test_rubric",
            rubric_version="v1",
            rubric_name="Test Rubric",
            dimensions=[],
            scales=[],
            dimension_by_id={},
            dimension_by_code={},
            scale_by_id={},
        )

    @pytest.fixture
    def policy_snapshot(self):
        """Sample policy snapshot for testing."""
        return PolicySnapshot(
            adjudication_policy={},
            aggregation_policy={},
            explanation_policy={},
            policy_version="v1"
        )

    def test_resolved_bundle_creation(self, artifact_bundle, rubric_snapshot, policy_snapshot):
        """Test creating a resolved artifact bundle."""
        resolved = ResolvedArtifactBundle(
            artifact_bundle=artifact_bundle,
            rubric_snapshot=rubric_snapshot,
            policy_snapshot=policy_snapshot,
            prompt_templates={},
            resolved_at=datetime(2026, 3, 12, 11, 0),
            resolver_version="0.1.0",
            total_hash="hash456"
        )

        assert resolved.artifact_bundle.bundle_id == "test_bundle"
        assert resolved.resolved_at.hour == 11
        assert resolved.resolver_version == "0.1.0"
        assert resolved.total_hash == "hash456"

    def test_get_version_info(self, artifact_bundle, rubric_snapshot, policy_snapshot):
        """Test getting formatted version information."""
        resolved = ResolvedArtifactBundle(
            artifact_bundle=artifact_bundle,
            rubric_snapshot=rubric_snapshot,
            policy_snapshot=policy_snapshot,
            prompt_templates={},
            resolved_at=datetime(2026, 3, 12, 11, 0),
            resolver_version="0.1.0",
            total_hash="hash456"
        )

        version_info = resolved.get_version_info()
        assert "test_bundle@v1" in version_info
        assert "rubric:v1" in version_info
        assert "policy:v1" in version_info

    def test_get_frozen_config_summary(self, artifact_bundle, rubric_snapshot, policy_snapshot):
        """Test getting frozen configuration summary for trace metadata."""
        resolved = ResolvedArtifactBundle(
            artifact_bundle=artifact_bundle,
            rubric_snapshot=rubric_snapshot,
            policy_snapshot=policy_snapshot,
            prompt_templates={},
            resolved_at=datetime(2026, 3, 12, 11, 0),
            resolver_version="0.1.0",
            total_hash="hash456"
        )

        summary = resolved.get_frozen_config_summary()
        assert summary["bundle_id"] == "test_bundle"
        assert summary["bundle_version"] == "v1"
        assert summary["rubric_id"] == "test_rubric"
        assert summary["rubric_version"] == "v1"
        assert summary["policy_version"] == "v1"
        assert summary["total_hash"] == "hash456"
        assert "2026-03-12" in summary["resolved_at"]

    def test_resolved_bundle_immutability(self, artifact_bundle, rubric_snapshot, policy_snapshot):
        """Test that ResolvedArtifactBundle is frozen (immutable)."""
        resolved = ResolvedArtifactBundle(
            artifact_bundle=artifact_bundle,
            rubric_snapshot=rubric_snapshot,
            policy_snapshot=policy_snapshot,
            prompt_templates={},
            resolved_at=datetime.now(),
            resolver_version="0.1.0",
            total_hash="hash"
        )

        # Should raise error when trying to modify frozen dataclass
        with pytest.raises(Exception):
            resolved.resolver_version = "modified"


class TestSchemaVersion:
    """Tests for SchemaVersion enum."""

    def test_schema_version_v2_0(self):
        """Test SchemaVersion.V2_0 enum value."""
        assert SchemaVersion.V2_0.value == "2.0"

    def test_schema_version_from_value(self):
        """Test getting SchemaVersion from string value."""
        version = SchemaVersion("2.0")
        assert version == SchemaVersion.V2_0
