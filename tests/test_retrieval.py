import pytest

from support_agent.retrieval import load_runbooks


@pytest.mark.parametrize("query, expected", [
    ("order late shipment held at carrier hub", "late-delivery"),
    ("received fewer units than ordered backorder", "short-delivery"),
    ("invoice price differs from quote", "pricing-dispute"),
    ("crushed pallets damaged on arrival", "transport-damage"),
    ("deliver to a new address instead", "address-change"),
    ("order number does not exist", "order-not-found"),
])
def test_top_hit_is_the_right_runbook(query, expected):
    hits = load_runbooks().search(query, k=1)
    assert hits and hits[0][0].doc_id == expected
