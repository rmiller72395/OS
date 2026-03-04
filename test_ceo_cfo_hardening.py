"""Quick sanity checks for CEO/CFO hardening (no pytest required)."""
import bot

def test_gate_max_length():
    v = bot.evaluate_gate("CFO", "x" * (256 * 1024 + 1), "mid", "FINANCIAL_CLEARANCE")
    assert v.system_error and "exceeded max length" in v.reason

def test_status_warning():
    out = bot._append_status_warning_if_invalid("BLUF: ok\nSTATUS: FOO")
    assert "SCHEMA_WARNING" in out and "STATUS" in out

def test_ceo_routing_overrides_allowlist():
    s = 'ROUTING_OVERRIDES_JSON\n{"w1": "anthropic/claude-3-5-sonnet-20241022", "w2": "evil/model"}'
    over = bot.parse_ceo_routing_overrides(s)
    assert over.get("w1") == "anthropic/claude-3-5-sonnet-20241022"
    assert "w2" not in over

if __name__ == "__main__":
    test_gate_max_length()
    test_status_warning()
    test_ceo_routing_overrides_allowlist()
    print("All OK")
