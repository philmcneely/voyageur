"""GET /docs and GET /openapi.json: the API page and the contract it runs. The examples are the contract fixtures, the page loads
nothing from anywhere else, and the first example heals on a service with no model."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voyageur.apidocs import ASSETS, EXAMPLES, examples
from voyageur.app import create_app
from tests.unit.scenarios import JSON, FakeModel, session

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def client(token="tok"):
    return TestClient(create_app(session(FakeModel()), token), base_url="http://127.0.0.1", headers=JSON)


def test_the_page_and_its_assets_are_served_without_a_token_under_a_policy_that_allows_only_this_service():
    c = client()
    r = c.get("/docs")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "connect-src 'self'" in csp
    assert "unsafe-eval" not in csp and "http:" not in csp and "https:" not in csp and "frame-ancestors 'none'" in csp
    for src in ("/docs/swagger-ui.css", "/docs/swagger-ui-bundle.js", "/docs/init.js"):
        assert src in r.text
    for name, kind in ASSETS.items():
        a = c.get(f"/docs/{name}")
        assert a.status_code == 200 and a.headers["content-type"].startswith(kind)
        assert a.headers["x-content-type-options"] == "nosniff"
    assert c.get("/docs/SWAGGER-UI-LICENSE").status_code == 404 and c.get("/docs/..%2fapp.py").status_code == 404


def test_the_page_references_nothing_outside_this_service():
    c = client()
    for text in (c.get("/docs").text, c.get("/docs/init.js").text):
        assert "http://" not in text and "https://" not in text


def test_the_document_lists_every_api_route_with_bodies_examples_and_the_bearer_scheme():
    doc = client().get("/openapi.json").json()
    paths = doc["paths"]
    for path in ("/healthz", "/v1/capabilities", "/v1/heal", "/v1/heals/{heal_id}/result", "/v1/heals/{heal_id}",
                 "/v1/heals", "/v1/metrics", "/v1/heals/{heal_id}/followup", "/v1/audit", "/v1/overlay", "/v1/diagnose"):
        assert path in paths, path
    assert "/dashboard" not in paths and "/docs" not in paths and "/openapi.json" not in paths
    assert doc["components"]["securitySchemes"]["bearer"]["scheme"] == "bearer"
    assert "security" not in paths["/healthz"]["get"] and paths["/v1/heal"]["post"]["security"] == [{"bearer": []}]
    for path in ("/v1/heal", "/v1/heals/{heal_id}/result", "/v1/heals/{heal_id}/followup", "/v1/audit", "/v1/overlay", "/v1/diagnose"):
        body = paths[path]["post"]["requestBody"]["content"]["application/json"]
        name = body["schema"]["$ref"].rsplit("/", 1)[1]
        assert name in doc["components"]["schemas"] and body["examples"], path
    for path in ("/v1/heal", "/v1/heals/{heal_id}/result"):
        assert paths[path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/HealResponse")
    assert list(paths["/v1/heal"]["post"]["requestBody"]["content"]["application/json"]["examples"]) == EXAMPLES


@pytest.mark.parametrize("name", EXAMPLES)
def test_each_example_is_its_contract_fixture_unchanged(name):
    fixture = json.loads((ROOT / "fixtures" / "contract" / name / "request.json").read_text(encoding="utf-8"))
    script = json.loads((ROOT / "fixtures" / "contract" / name / "script.json").read_text(encoding="utf-8"))
    assert examples()[name]["value"] == fixture
    assert examples()[name]["description"] == script["description"]


def test_every_example_in_the_document_is_accepted_by_its_route_and_the_first_heals_with_no_model():
    c = TestClient(create_app(session(None), ""), base_url="http://127.0.0.1", headers=JSON)
    doc = c.get("/openapi.json").json()
    heal_examples = doc["paths"]["/v1/heal"]["post"]["requestBody"]["content"]["application/json"]["examples"]
    for name, ex in heal_examples.items():
        r = c.post("/v1/heal", json=ex["value"])
        assert r.status_code == 200, (name, r.text)
    first = c.post("/v1/heal", json=heal_examples[EXAMPLES[0]]["value"]).json()
    assert first["status"] == "try" and first["heal_id"]
    passed = doc["paths"]["/v1/heals/{heal_id}/result"]["post"]["requestBody"]["content"]["application/json"]["examples"]["passed"]
    done = c.post(f"/v1/heals/{first['heal_id']}/result", json=passed["value"]).json()
    assert done["status"] == "healed"
    audit = doc["paths"]["/v1/audit"]["post"]["requestBody"]["content"]["application/json"]["examples"]
    for ex in audit.values():
        assert c.post("/v1/audit", json=ex["value"]).status_code == 200
