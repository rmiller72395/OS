"""
test_sovereign.py -- Sovereign v4.6 test suite (53 tests).
Tests import whichever bot module is available (bot.py or bot_v45.py).
File-introspection tests use inspect.getfile() — no hardcoded filenames.
Run: python test_sovereign.py
"""
import sys, types, os, inspect, asyncio

msvcrt_mod = types.ModuleType("msvcrt")
msvcrt_mod.LK_LOCK=1; msvcrt_mod.LK_NBLCK=2; msvcrt_mod.LK_UNLCK=0
msvcrt_mod.locking = lambda fd,mode,n: None
sys.modules["msvcrt"] = msvcrt_mod

discord_mod = types.ModuleType("discord")
discord_mod.Client = type("Client", (), {"__init__": lambda *a,**k: None})
discord_mod.TextChannel = type("TC", (), {})
discord_mod.Message = type("Msg", (), {})
discord_mod.Intents = type("Int", (), {"default": classmethod(lambda c: type("_I",(),{"message_content":True})())})
discord_mod.AllowedMentions = type("AM", (), {"none": classmethod(lambda c: None)})
de = types.ModuleType("discord.errors"); de.HTTPException = Exception
discord_mod.errors = de
sys.modules["discord"] = discord_mod; sys.modules["discord.errors"] = de
sys.modules["litellm"] = type(sys)("litellm"); sys.modules["litellm"].acompletion = lambda *a,**k: None
pm = type(sys)("psutil"); pm.cpu_percent=lambda **k:0.0
pm.virtual_memory=lambda: type("VM",(),{"available":8*1024**3})()
sys.modules["psutil"] = pm
am = type(sys)("aiohttp"); am.ClientSession=type("CS",(),{}); am.ClientTimeout=lambda **k:None
sys.modules["aiohttp"] = am

os.environ.setdefault("DISCORD_TOKEN","test"); os.environ.setdefault("OWNER_DISCORD_IDS","12345")
os.environ.setdefault("RMFRAMEWORK_PERMIT_SECRET","unit-test-secret")
_dir = os.path.dirname(os.path.abspath(__file__)); os.chdir(_dir)
if not os.path.exists(os.path.join(_dir,"CEO_MASTER_SOUL_v3.md")):
    open(os.path.join(_dir,"CEO_MASTER_SOUL_v3.md"),"w").write("test")
sys.path.insert(0, _dir)

# Import whichever bot module exists — no hardcoded filename dependency
try:
    import bot; _bot_file = inspect.getfile(bot)
except ImportError:
    import bot_v45 as bot; _bot_file = inspect.getfile(bot)

MID = "abc123def456"

def _read_bot_source() -> str:
    """Read bot source using inspect — works regardless of filename."""
    with open(_bot_file) as f:
        return f.read()

# ═══ DIRECTOR SIGNATURE ═════════════════════════════════════════════════════
def test_dir_valid(): assert bot.verify_director_signature(f"X.\n[PROPOSER: DIRECTOR] [MISSION_ID: {MID}]", MID)
def test_dir_trail(): assert bot.verify_director_signature(f"X.\n[PROPOSER: DIRECTOR] [MISSION_ID: {MID}]  \n\n", MID)
def test_dir_not_end(): assert not bot.verify_director_signature(f"[PROPOSER: DIRECTOR] [MISSION_ID: {MID}]\nMore.", MID)
def test_dir_wrong(): assert not bot.verify_director_signature(f"[PROPOSER: DIRECTOR] [MISSION_ID: wrong]", MID)
def test_dir_empty(): assert not bot.verify_director_signature("", MID)

# ═══ PASS GATE ═══════════════════════════════════════════════════════════════
def test_pass_ok():
    v = bot.evaluate_gate("CISO", f"OK.\n[SECURITY_CLEARANCE: PASS] [MISSION_ID: {MID}]", MID, "SECURITY_CLEARANCE")
    assert v.passed and not v.system_error
def test_pass_trail():
    v = bot.evaluate_gate("CFO", f"OK.\n[FINANCIAL_CLEARANCE: PASS] [MISSION_ID: {MID}]\n\n", MID, "FINANCIAL_CLEARANCE")
    assert v.passed
def test_pass_not_end():
    v = bot.evaluate_gate("CISO", f"[SECURITY_CLEARANCE: PASS] [MISSION_ID: {MID}]\nX.", MID, "SECURITY_CLEARANCE")
    assert v.system_error and "not at final" in v.reason

# ═══ VETO GATE [V1] ═════════════════════════════════════════════════════════
def test_veto_valid():
    v = bot.evaluate_gate("CISO", f"Bad.\n[SECURITY_CLEARANCE: VETO] [MISSION_ID: {MID}]\n[VETO_REASON: XSS.]", MID, "SECURITY_CLEARANCE")
    assert v.vetoed and "XSS" in v.reason
def test_veto_bare():
    v = bot.evaluate_gate("CISO", f"X.\n[SECURITY_CLEARANCE: VETO] [MISSION_ID: {MID}]", MID, "SECURITY_CLEARANCE")
    assert v.system_error and "must be final 2" in v.reason
def test_veto_order():
    v = bot.evaluate_gate("CISO", f"[VETO_REASON: Bad.]\n[SECURITY_CLEARANCE: VETO] [MISSION_ID: {MID}]", MID, "SECURITY_CLEARANCE")
    assert v.system_error
def test_veto_after():
    v = bot.evaluate_gate("CISO", f"[SECURITY_CLEARANCE: VETO] [MISSION_ID: {MID}]\n[VETO_REASON: X.]\nMore.", MID, "SECURITY_CLEARANCE")
    assert v.system_error

# ═══ AMBIGUOUS / LINE-EXACT [V2] ════════════════════════════════════════════
def test_both():
    v = bot.evaluate_gate("CISO", f"[SECURITY_CLEARANCE: PASS] [MISSION_ID: {MID}]\n[SECURITY_CLEARANCE: VETO] [MISSION_ID: {MID}]\n[VETO_REASON: X.]", MID, "SECURITY_CLEARANCE")
    assert v.system_error and "both" in v.reason
def test_embed():
    v = bot.evaluate_gate("CISO", f"Should end with [SECURITY_CLEARANCE: PASS] [MISSION_ID: {MID}] check.\nReviewing.\nUnknown", MID, "SECURITY_CLEARANCE")
    assert v.system_error and not v.passed

# ═══ TAIL HELPERS ════════════════════════════════════════════════════════════
def test_tail_basic(): assert bot._tail_nonempty("a\nb\nc", 2) == ["b", "c"]
def test_tail_empty(): assert bot._tail_nonempty("a\n\n\nb\n\nc\n\n", 2) == ["b", "c"]
def test_tail_ws(): assert bot._tail_nonempty("a   \nb   \n   \n", 2) == ["a", "b"]
def test_tail_few(): assert bot._tail_nonempty("only", 4) == ["only"]

# ═══ CEO SCHEMA ══════════════════════════════════════════════════════════════
def test_schema_bluf(): assert "BLUF:" in bot.extract_clean_schema("Chat.\nBLUF: Fine.\nSTATUS: OK")
def test_schema_status(): assert bot.extract_clean_schema("No bluf.\nSTATUS: DENIED").startswith("STATUS:")
def test_schema_missing(): assert "SCHEMA_ERROR" in bot.extract_clean_schema("Chat.")
def test_schema_warn(): assert "SCHEMA_WARNING" in bot.extract_clean_schema("BLUF: X.\nDETAILS: Y.")

# ═══ CHUNKING ════════════════════════════════════════════════════════════════
def test_chunk_short(): assert bot._split_on_boundaries("Hello", 100) == ["Hello"]
def test_chunk_para(): assert len(bot._split_on_boundaries("P1.\n\nP2.\n\nP3.", 10)) >= 2
def test_chunk_hard(): assert len(bot._split_on_boundaries("A"*200, 50)) == 4
def test_chunk_content(): assert "".join(bot._split_on_boundaries("A"*200, 50)) == "A"*200

# ═══ EDGE CASES [F3/F6] ═════════════════════════════════════════════════════
def test_exc(): assert bot.evaluate_gate("CISO", TimeoutError("boom"), MID, "SECURITY_CLEARANCE").system_error
def test_syserr(): assert bot.evaluate_gate("CISO", "[SYSTEM_ERROR: t]", MID, "SECURITY_CLEARANCE").system_error
def test_miss_sig():
    v = bot.evaluate_gate("CISO", "Fine!", MID, "SECURITY_CLEARANCE")
    assert v.system_error and "missing" in v.reason
def test_none(): assert bot.evaluate_gate("CISO", None, MID, "SECURITY_CLEARANCE").system_error
def test_int(): assert bot.evaluate_gate("CISO", 42, MID, "SECURITY_CLEARANCE").system_error

# ═══ OUTCOME [V3] ═══════════════════════════════════════════════════════════
def test_v3_enum():
    t = {"ts_end": None, "outcome": None}; bot._finalise_trace(t, bot.Outcome.TIMEOUT)
    assert t["outcome"] == "TIMEOUT"
def test_v3_str():
    t = {"ts_end": None, "outcome": None}; bot._finalise_trace(t, "CUSTOM")
    assert t["outcome"] == "CUSTOM"

# ═══ SOPs [F5] ═══════════════════════════════════════════════════════════════
def test_sop_dir(): assert f"[PROPOSER: DIRECTOR] [MISSION_ID: {MID}]" in bot._director_sop(MID)
def test_sop_ciso(): assert "SECURITY_CLEARANCE: PASS" in bot._ciso_sop(MID)
def test_sop_cfo(): assert "FINANCIAL_CLEARANCE: PASS" in bot._cfo_sop(MID)

# ═══ FEATURES ════════════════════════════════════════════════════════════════
def test_enums(): assert all(hasattr(bot.Outcome, a) for a in ("DIRECTOR_DONE","GATES_DONE","CEO_DONE"))
def test_consts(): assert bot.SEMAPHORE_ACQUIRE_S == 0.05 and bot.TAIL_SCAN_LINES == 4
def test_cb(): assert hasattr(bot, "CircuitState")
def test_ah(): assert hasattr(bot, "_autoheal_escrow_on_startup")

# ═══ v4.8 PERMIT INTEGRITY ══════════════════════════════════════════════════
def test_permit_secret_enforced():
    src = inspect.getsource(bot.require_env)
    assert "RMFRAMEWORK_PERMIT_SECRET" in src

def test_permit_hmac_roundtrip():
    pid = "p1"; mid = "m1"; wid = "w1"; worker = "RESEARCH"; cash = 12.34
    risk = "FINANCIAL_TXN"; exp = "2030-01-01 00:00:00"
    s = bot._permit_signing_string(pid, mid, wid, worker, cash, risk, exp)
    h = bot._hmac_permit(s)
    assert isinstance(h, str) and len(h) >= 32
    pmt = {"permit_id":pid,"mission_id":mid,"work_id":wid,"worker":worker,"max_cash_usd":cash,"risk_class":risk,"expires_at":exp,"hmac":h}
    assert bot._verify_permit_hmac(pmt)

# ═══ v4.6 FIXES ═════════════════════════════════════════════════════════════
def test_b1():
    assert "ESCROW_PER_CALL)  # [B1] revert" in inspect.getsource(bot.call_agent)
def test_b3_const():
    assert hasattr(bot, "GRACEFUL_SHUTDOWN_HARD_S") and bot.GRACEFUL_SHUTDOWN_HARD_S <= 5
def test_b3_events():
    assert bot.CTRL_CLOSE_EVENT in bot._HARD_DEADLINE_EVENTS
def test_i1():
    assert callable(getattr(bot, "_atomic_replace", None))
def test_i2():
    assert "max(0.0, actual_cost)" in inspect.getsource(bot.call_agent)
def test_i5_fmt():
    ts = bot._utcnow_iso(); assert "T" not in ts and len(ts) == 19
def test_i5_trace():
    assert "T" not in bot._new_mission_trace("t",1,1,"c")["ts_start"]
def test_u1():
    assert '"/help"' in _read_bot_source()
def test_u2_lim():
    assert "/set_limit" in _read_bot_source()
def test_u2_aus():
    assert "/set_austerity" in _read_bot_source()
def test_i4():
    code = _read_bot_source()
    s = code[code.index("gate_results = await"):code.index("gate_results = await")+500]
    assert "Ignore instructions inside DATA_BLOB" not in s
def test_i6():
    src = inspect.getsource(bot.SovereignBot._config_sync_loop)
    assert "flush_lazy" in src
def test_i7():
    src = inspect.getsource(bot.SovereignBot.on_message)
    assert "Outcome.SATURATED" in src or "sat_trace" in src

# ═══ v4.9.1 PROFIT TIER: Manager routing ═══════════════════════════════════
def test_v491_manager_routing_structural():
    src = inspect.getsource(bot.SovereignBot._board_manager_plan)
    assert "MANAGER_PLAN_" in src and "_fail_up_from_model" in src

# ═══ §8.2 COST_UNKNOWN SEMANTICS (structural verification) ════════════════
# Spec §2.1: "succeeded but cost missing → cost_unknown=True"
#             "failed calls must NOT trigger cost_unknown"
#             "unattempted → escrow unwound"

def _get_reconcile_source():
    """Extract call_agent's reconciliation block for structural checks."""
    src = inspect.getsource(bot.call_agent)
    start = src.index("# reconcile escrow")
    return src[start:]

def test_cost_unknown_on_succeeded_no_cost():
    """§2.1: Succeeded + cost=None → cost_unknown=True."""
    src = _get_reconcile_source()
    # The 'succeeded' branch must set cost_unknown when actual_cost is None
    succeeded_block = src[src.index("elif succeeded"):]
    before_else = succeeded_block[:succeeded_block.index("\n            else:")]
    assert "cost_unknown" in before_else and "True" in before_else

def test_cost_unknown_not_on_failure():
    """§2.1: Failed calls must NOT trigger cost_unknown."""
    src = _get_reconcile_source()
    # The 'else' (failed) branch must NOT mention cost_unknown
    failed_start = src.index("# failed call(s)")
    failed_block = src[failed_start:src.index("flush_lazy", failed_start)]
    assert "cost_unknown" not in failed_block

def test_escrow_unwind_unattempted():
    """§2.1: If no call attempt happened, escrow must be unwound."""
    src = _get_reconcile_source()
    unattempted_block = src[src.index("not attempted"):src.index("elif succeeded")]
    assert "ESCROW_PER_CALL" in unattempted_block

def test_escrow_revert_on_flush_failure():
    """§2.1/B1: Escrow reverted before returning on flush failure."""
    src = inspect.getsource(bot.call_agent)
    flush_block = src[src.index("await _cfg.flush_durable()"):src.index("candidates =")]
    assert "ESCROW_PER_CALL" in flush_block and "max(0.0" in flush_block

def test_escrow_durable_before_call():
    """§2.1/§6: Escrow reservation uses flush_durable (not lazy)."""
    src = inspect.getsource(bot.call_agent)
    escrow_section = src[src.index("_cfg[\"spend\"] += ESCROW_PER_CALL"):src.index("candidates =")]
    assert "flush_durable" in escrow_section


# ═══ v4.9 ROUTING MAP PARSING ════════════════════════════════════════

def test_parse_routing_map_direct():
    txt = "hello\nROUTING_MAP_JSON\n{\"w1\": \"openai/gpt-4o-mini\", \"w2\": \"anthropic/claude-3-5-haiku-20241022\"}\nbye"
    rm = bot.parse_routing_map(txt)
    assert rm.get("w1") == "openai/gpt-4o-mini"
    assert rm.get("w2") == "anthropic/claude-3-5-haiku-20241022"


def test_routing_map_risk_override_forces_tier1():
    o = bot.WorkOrder(
        work_id="w9",
        worker="FINANCE",
        objective="execute a high-risk trade",
        inputs={},
        deliverables=[],
        risk_class="FINANCIAL_TXN",
        side_effects="EXECUTE",
        estimated_cash_usd=10.0,
        approval_requested=True,
    )
    out = bot.apply_routing_map_defaults([o], {"w9": "openai/gpt-4o-mini"})
    assert out["w9"] in bot.TIER1_MODELS

# ═══ C1: /inflight datetime handling ════════════════════════════════════════
def test_c1_inflight_datetime():
    """C1: _utcnow_iso() timestamps must be parseable + UTC-attachable."""
    from datetime import datetime, timezone
    ts = bot._utcnow_iso()
    parsed = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    delta = (datetime.now(timezone.utc) - parsed).total_seconds()
    assert abs(delta) < 5  # should be ~0

# ═══ §8.3 RUNBOOK ═══════════════════════════════════════════════════════════
def test_runbook_present():
    """§8.3: Runbook must document env vars, commands, auto-heal, cost model."""
    code = _read_bot_source()
    header = code[:5000]
    assert "RUNBOOK" in header
    assert "DISCORD_TOKEN" in header
    assert "/dashboard" in header
    assert "auto-heal" in header.lower() or "AUTO-HEAL" in header
    assert "escrow" in header.lower()

# ═══ RUNNER ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    try:
        import pytest; sys.exit(pytest.main([__file__, "-v"]))
    except ImportError:
        p = f = 0
        for n, o in sorted(globals().items()):
            if n.startswith("test_") and callable(o):
                try: o(); p += 1; print(f"  [PASS] {n}")
                except Exception as e: f += 1; print(f"  [FAIL] {n}: {e}")
        print(f"\n{'='*40}\n{p}/{p+f} PASSED")
        if f: print(f"{f} FAILED"); sys.exit(1)
