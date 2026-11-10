"""
scripts/show_interfaces.py -- read-only. Prints the PUBLIC surface of our modules
(function signatures, one-line docs, and the names/sizes of tables) so Claude can
write agent.py against your real code.

Run from the project root:   uv run python -m scripts.show_interfaces
(If that fails with 'No module named scripts', use:  uv run python scripts/show_interfaces.py)
"""
import importlib
import inspect
import sys
import types

sys.stdout.reconfigure(encoding="utf-8")  # Gurmukhi on a Windows console
sys.path.insert(0, ".")  # make `app` importable when run as a plain script

MODULES = ["app.aliases", "app.parser", "app.lookup", "app.punjabi_numbers"]


def short(value, limit=6):
    """Show a table compactly: type, size, first few keys/items."""
    if isinstance(value, dict):
        keys = list(value.keys())[:limit]
        return f"dict, {len(value)} keys, first keys: {keys}"
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)[:limit]
        return f"{type(value).__name__}, {len(value)} items, first: {items}"
    return f"{type(value).__name__}: {repr(value)[:80]}"


for name in MODULES:
    print("=" * 70)
    print(name)
    try:
        mod = importlib.import_module(name)
    except Exception as exc:  # show the problem instead of crashing
        print(f"  COULD NOT IMPORT: {type(exc).__name__}: {exc}")
        continue

    for attr, obj in vars(mod).items():
        if attr.startswith("_"):
            continue
        if isinstance(obj, types.ModuleType):
            continue
        if inspect.isfunction(obj) and obj.__module__ == name:
            doc = (inspect.getdoc(obj) or "").splitlines()
            print(f"  def {attr}{inspect.signature(obj)}")
            if doc:
                print(f"      {doc[0]}")
        elif inspect.isclass(obj) and obj.__module__ == name:
            print(f"  class {attr}  fields/methods: "
                  f"{[m for m in vars(obj) if not m.startswith('_')][:10]}")
            if hasattr(obj, "__dataclass_fields__"):
                print(f"      dataclass fields: {list(obj.__dataclass_fields__)}")
        elif isinstance(obj, (dict, list, tuple, set, frozenset, str, int, float)):
            print(f"  {attr} = {short(obj)}")