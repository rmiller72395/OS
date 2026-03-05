# sovereign __main__ — python -m sovereign init | self-test | preflight

from __future__ import annotations

import sys

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m sovereign init | self-test | preflight")
        return 1
    cmd = sys.argv[1].lower()
    if cmd == "init":
        from sovereign.init import run_init
        return run_init()
    if cmd == "self-test":
        from sovereign.self_test import run_self_test
        return run_self_test()
    if cmd == "preflight":
        from sovereign.preflight import run_preflight
        return run_preflight()
    print("Unknown command. Use: init | self-test | preflight")
    return 1

if __name__ == "__main__":
    sys.exit(main())
