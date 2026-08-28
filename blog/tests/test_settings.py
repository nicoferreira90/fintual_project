import pytest

from core.settings import _database_config, _env_bool, _env_list


def test_database_url_is_parsed_and_credentials_decoded(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://u%40b:p%2Fw@db.internal:5433/appdb?sslmode=require",
    )
    config = _database_config()

    assert config["NAME"] == "appdb"
    assert config["USER"] == "u@b"
    assert config["PASSWORD"] == "p/w"
    assert config["HOST"] == "db.internal"
    assert config["PORT"] == "5433"
    assert config["OPTIONS"] == {"sslmode": "require"}


def test_discrete_vars_used_when_no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_DB", "other")
    config = _database_config()

    assert config["HOST"] == "db"
    assert config["NAME"] == "other"
    assert "OPTIONS" not in config


def test_conn_max_age_always_set(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CONN_MAX_AGE", "120")
    assert _database_config()["CONN_MAX_AGE"] == 120


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
     ("0", False), ("false", False), ("", False), ("nope", False)],
)
def test_env_bool(monkeypatch, raw, expected):
    monkeypatch.setenv("SOME_FLAG", raw)
    assert _env_bool("SOME_FLAG", False) is expected


def test_env_list_splits_and_strips(monkeypatch):
    monkeypatch.setenv("SOME_LIST", " a , b ,, c ")
    assert _env_list("SOME_LIST") == ["a", "b", "c"]
