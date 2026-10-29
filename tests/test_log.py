import logging

from searchbrain import log


def test_api_key_is_hidden_in_log(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-secret")
    record = logging.LogRecord("searchbrain", logging.ERROR, "", 0, "Request failed for key %s", ("tvly-test-secret",), None)

    log.HideSecrets().filter(record)

    assert "tvly-test-secret" not in record.getMessage()
    assert "***" in record.getMessage()


def test_main_module_logs_into_searchbrain_log():
    assert log.get_logger("__main__").name == "searchbrain.main"
