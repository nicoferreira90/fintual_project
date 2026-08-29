import pytest
from django.test import Client


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
def test_readyz_reports_ok_when_database_reachable(client):
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
