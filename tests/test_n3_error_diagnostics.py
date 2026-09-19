"""Task diagnostics stay bounded and free of embedded payloads."""

from scholartrace.delivery.service import _bounded_error_chain


def test_error_chain_keeps_types_and_strips_payloads() -> None:
    inner = ValueError(
        "1 validation error for SemanticVerificationDraft\n"
        "  Invalid JSON: EOF while parsing a value [type=json_invalid, "
        "input_value='secret model text', input_type=str]"
    )
    outer = RuntimeError("provider returned an invalid verification draft")
    outer.__cause__ = inner

    chain = _bounded_error_chain(outer)

    assert [item["type"] for item in chain] == ["RuntimeError", "ValueError"]
    assert chain[0]["message"] == "provider returned an invalid verification draft"
    assert "secret model text" not in chain[1]["message"]
    assert len(chain[1]["message"]) <= 200


def test_error_chain_is_bounded_to_four_single_line_levels() -> None:
    current: BaseException = ValueError("x" * 500)
    for _ in range(5):
        wrapper = RuntimeError("wrapper")
        wrapper.__cause__ = current
        current = wrapper

    chain = _bounded_error_chain(current)

    assert len(chain) == 4
    assert all("\n" not in item["message"] for item in chain)
    assert all(len(item["message"]) <= 200 for item in chain)
