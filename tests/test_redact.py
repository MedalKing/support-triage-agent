from support_agent.redact import Redactor


def test_redacts_and_restores_contact_details():
    r = Redactor()
    text = "Call +358 40 123 4567 or 044 222 3333, mail anna@x.example. Again: anna@x.example"
    out = r.redact(text)
    assert "anna@x.example" not in out and "123 4567" not in out and "222 3333" not in out
    assert out.count("[EMAIL_1]") == 2  # same value, same placeholder
    assert r.restore(out) == text


def test_redacts_iban_and_identity_code_without_eating_them_as_phones():
    r = Redactor()
    out = r.redact("Pay to FI21 1234 5600 0007 85, my code is 131052-308T.")
    assert "[IBAN_1]" in out and "[HETU_1]" in out
    assert "PHONE" not in out


def test_leaves_order_ids_and_amounts_alone():
    r = Redactor()
    text = "Order SO-5001, 500 boxes at 0.42 EUR, promised 2026-11-10, invoice INV-7003."
    assert r.redact(text) == text
