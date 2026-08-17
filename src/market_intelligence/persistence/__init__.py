"""Public article persistence API."""

from market_intelligence.persistence.alert_candidates import (
    AlertCandidatePersistenceError,
    AlertCandidateRepository,
    AlertCandidateSaveOutcome,
    AlertCandidateSaveResult,
)
from market_intelligence.persistence.articles import (
    ArticlePersistenceError,
    ArticleRepository,
    PersistenceConfigurationError,
)
from market_intelligence.persistence.classification_work import (
    ClassificationWorkReader,
    ClassificationWorkReadError,
    DiscoveryScope,
)
from market_intelligence.persistence.classifications import (
    ClassificationClaim,
    ClassificationFailure,
    ClassificationKey,
    ClassificationLineage,
    ClassificationLineageMismatchError,
    ClassificationPersistenceError,
    ClassificationRecord,
    ClassificationRepository,
    ClassificationStatus,
    CompletionOutcome,
    CompletionResult,
    EnqueueOutcome,
    EnqueueResult,
    FailureDisposition,
    FailureOutcome,
    FailureResult,
    LeaseRenewalOutcome,
    LeaseRenewalResult,
)
from market_intelligence.persistence.discovery_observations import (
    BENCHMARK_EVIDENCE_RETENTION_DAYS,
    MAX_OBSERVATION_SAMPLES,
    OBSERVATION_RETENTION_DAYS,
    SAMPLE_RETENTION_DAYS,
    DiscoveryBenchmarkEvidence,
    DiscoveryBenchmarkEvidenceRecordResult,
    DiscoveryObservation,
    DiscoveryObservationKey,
    DiscoveryObservationRecordResult,
    DiscoveryObservationRepository,
    DiscoveryObservationSample,
    DiscoveryPersistenceError,
    DiscoveryPruneResult,
    DiscoveryRecordOutcome,
    DiscoveryRetentionCutoffs,
)
from market_intelligence.persistence.supabase_alert_candidate_repository import (
    SupabaseAlertCandidateRepository,
    create_alert_candidate_repository_from_environment,
)
from market_intelligence.persistence.supabase_classification_repository import (
    SupabaseClassificationRepository,
    create_classification_repository_from_environment,
)
from market_intelligence.persistence.supabase_classification_work_reader import (
    SupabaseClassificationWorkReader,
    create_classification_work_reader_from_environment,
)
from market_intelligence.persistence.supabase_discovery_repository import (
    SupabaseDiscoveryRepository,
    create_discovery_repository_from_environment,
)
from market_intelligence.persistence.supabase_repository import (
    SupabaseArticleRepository,
    create_article_repository_from_environment,
)
from market_intelligence.persistence.user_preferences import (
    UserPreferencePage,
    UserPreferenceReader,
    UserPreferenceReadError,
)

__all__ = [
    "ClassificationWorkReadError",
    "ClassificationWorkReader",
    "DiscoveryScope",
    "UserPreferencePage",
    "UserPreferenceReadError",
    "UserPreferenceReader",
    "SupabaseClassificationWorkReader",
    "create_classification_work_reader_from_environment",
    "ArticlePersistenceError",
    "ArticleRepository",
    "ClassificationClaim",
    "ClassificationFailure",
    "ClassificationKey",
    "ClassificationLineage",
    "ClassificationLineageMismatchError",
    "ClassificationPersistenceError",
    "ClassificationRecord",
    "ClassificationRepository",
    "ClassificationStatus",
    "CompletionOutcome",
    "CompletionResult",
    "EnqueueOutcome",
    "EnqueueResult",
    "FailureDisposition",
    "FailureOutcome",
    "FailureResult",
    "LeaseRenewalOutcome",
    "LeaseRenewalResult",
    "PersistenceConfigurationError",
    "SupabaseArticleRepository",
    "SupabaseClassificationRepository",
    "create_article_repository_from_environment",
    "create_classification_repository_from_environment",
    "AlertCandidatePersistenceError",
    "AlertCandidateRepository",
    "AlertCandidateSaveOutcome",
    "AlertCandidateSaveResult",
    "SupabaseAlertCandidateRepository",
    "create_alert_candidate_repository_from_environment",
    "BENCHMARK_EVIDENCE_RETENTION_DAYS",
    "MAX_OBSERVATION_SAMPLES",
    "OBSERVATION_RETENTION_DAYS",
    "SAMPLE_RETENTION_DAYS",
    "DiscoveryBenchmarkEvidence",
    "DiscoveryBenchmarkEvidenceRecordResult",
    "DiscoveryObservation",
    "DiscoveryObservationKey",
    "DiscoveryObservationRecordResult",
    "DiscoveryObservationRepository",
    "DiscoveryObservationSample",
    "DiscoveryPersistenceError",
    "DiscoveryPruneResult",
    "DiscoveryRecordOutcome",
    "DiscoveryRetentionCutoffs",
    "SupabaseDiscoveryRepository",
    "create_discovery_repository_from_environment",
]
