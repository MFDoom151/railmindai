"""Запуск всех тестов без pytest:  python tests/run_all.py   (с pytest:  python -m pytest tests -q)"""
import importlib.util
import os
import sys
import time
import traceback

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
ok = bad = 0
for fn in sorted(os.listdir(HERE)):
    if not (fn.startswith("test_") and fn.endswith(".py")):
        continue
    spec = importlib.util.spec_from_file_location(fn[:-3], os.path.join(HERE, fn))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in sorted(dir(mod)):
        if name.startswith("test_") and callable(getattr(mod, name)):
            t0 = time.time()
            try:
                getattr(mod, name)()
                ok += 1
                print(f"ok    {fn}::{name}  ({(time.time() - t0) * 1000:.0f} ms)")
            except Exception:
                bad += 1
                print(f"FAIL  {fn}::{name}")
                traceback.print_exc()
print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
