# tests/test_validator.py — WorkOrder / mission validation (v4.10)

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Minimal mocks so bot can be imported (same pattern as test_sovereign.py)
msvcrt_mod = types.ModuleType("msvcrt")
msvcrt_mod.LK_LOCK = 1
msvcrt_mod.LK_NBLCK = 2
msvcrt_mod.LK_UNLCK = 0
msvcrt_mod.locking = lambda fd, mode, n: None
sys.modules["msvcrt"] = msvcrt_mod
discord_mod = types.ModuleType("discord")
discord_mod.Client = type("Client", (), {"__init__": lambda *a, **k: None})
discord_mod.Intents = type("Int", (), {"default": classmethod(lambda c: type("_I", (), {"message_content": True})())})
sys.modules["discord"] = discord_mod
sys.modules["litellm"] = type(sys)("litellm")
sys.modules["litellm"].acompletion = lambda *a, **k: None
sys.modules["psutil"] = type(sys)("psutil")
sys.modules["aiohttp"] = type(sys)("aiohttp")

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("OWNER_DISCORD_IDS", "12345")
os.environ.setdefault("RMFRAMEWORK_PERMIT_SECRET", "test-secret")
if not (ROOT / "CEO_MASTER_SOUL_v3.md").exists():
    (ROOT / "CEO_MASTER_SOUL_v3.md").write_text("test", encoding="utf-8")


def test_parse_work_orders_empty():
    from bot import parse_work_orders
    assert parse_work_orders("", "mid") == []
    assert parse_work_orders("no json here", "mid") == []


def test_parse_work_orders_valid():
    from bot import parse_work_orders
    text = """
    Some text
    WORK_ORDERS_JSON
    {"orders": [
        {"work_id": "w1", "worker": "RUNNER", "objective": "Do X", "inputs": {}, "deliverables": []}
    ]}
    """
    orders = parse_work_orders(text, "mid")
    assert len(orders) == 1
    assert orders[0].worker == "RUNNER"
    assert orders[0].objective == "Do X"
    assert orders[0].risk_class == "NONE"
    assert orders[0].side_effects == "NONE"


def test_parse_work_orders_normalizes_risk_side():
    from bot import parse_work_orders
    text = """
    WORK_ORDERS_JSON
    {"orders": [
        {"work_id": "w1", "worker": "RUNNER", "objective": "Y", "risk_class": "PII", "side_effects": "EXECUTE"}
    ]}
    """
    orders = parse_work_orders(text, "mid")
    assert len(orders) == 1
    assert orders[0].risk_class == "PII"
    assert orders[0].side_effects == "EXECUTE"


def test_parse_work_orders_caps_count():
    from bot import parse_work_orders
    WORK_ORDERS_MAX_COUNT = 50
    orders_list = [{"work_id": f"w{i}", "worker": "RUNNER", "objective": f"O{i}", "inputs": {}, "deliverables": []} for i in range(WORK_ORDERS_MAX_COUNT + 10)]
    import json
    text = "WORK_ORDERS_JSON\n" + json.dumps({"orders": orders_list})
    orders = parse_work_orders(text, "mid")
    assert len(orders) <= WORK_ORDERS_MAX_COUNT
