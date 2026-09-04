"""Minimal NSLS-II style startup file: creates its own RunEngine, as nslsii.configure_base does."""

import threading

from bluesky.run_engine import RunEngine
from IPython import get_ipython

RE = RunEngine({})
created_thread_ident = threading.get_ident()

# Documents the engine emits, captured like a BestEffortCallback would see them.
docs = []
RE.subscribe(lambda name, doc: docs.append((name, doc)))

# A pre-existing waiting hook, standing in for a ProgressBarManager; qs must chain it.
hook_calls = []
RE.waiting_hook = lambda status_objs: hook_calls.append(status_objs is None)

# Talk to IPython the way profiles do; under qs this reaches the patched object.
get_ipython().user_ns["ipython_marker"] = "set-by-profile"
get_ipython().run_line_magic("matplotlib", "inline")
