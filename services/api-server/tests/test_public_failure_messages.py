import json

from use_cases import approval_readiness_gate, legal_corpus_worker
from use_cases.legal_corpus_registry import build_default_official_corpus_source_registry


def test_gate_read_failure_remains_blocking_without_raw_exception(monkeypatch):
    secret = "private-db-host/password-and-document-path"

    def fail(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(approval_readiness_gate, "list_deal_state_records", fail)
    result = approval_readiness_gate.build_deal_state_approval_readiness("safe-test")
    assert secret not in json.dumps(result)
    assert result["readable"] is False
    assert any(row["code"] == "DEAL_STATE_RECORDS_READABLE" for row in result["gates"])


def test_worker_configuration_failure_does_not_expose_raw_exception(monkeypatch):
    secret = "private-provider-token-and-file-path"

    def fail(*args, **kwargs):
        raise legal_corpus_worker.LegalCorpusWorkerConfigurationError(secret)

    monkeypatch.setattr(legal_corpus_worker, "load_legal_corpus_runtime_configuration", fail)
    result = legal_corpus_worker.inspect_legal_corpus_runtime_configuration(
        environment={}, registry=build_default_official_corpus_source_registry(),
    )
    assert result["configurationStatus"] == "invalid"
    assert result["workerEnabled"] is False
    assert secret not in json.dumps(result)
