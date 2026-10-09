"""Load the pure modules without importing Home Assistant.

The package __init__ imports Home Assistant, so tests import quiet.py and
state.py as a standalone package instead.
"""

import importlib.util
import pathlib
import sys
import types

PKG = "tv_mgmt_pure"
SRC = pathlib.Path(__file__).parent.parent / "custom_components" / "tv_mgmt"

pkg = types.ModuleType(PKG)
pkg.__path__ = [str(SRC)]
sys.modules[PKG] = pkg

for name in ("quiet", "state", "activity", "names", "apps", "media"):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", SRC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{PKG}.{name}"] = module
    spec.loader.exec_module(module)
