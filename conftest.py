"""agent/*.py and github_integration/*.py import each other by bare module
name (e.g. `import broker`) -- see the matching comment in
scenarios/run_scenario.py for why. Tests need both directories on sys.path
for the same reason the entry-point scripts do.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _subdir in ("agent", "github_integration"):
    _path = os.path.join(_ROOT, _subdir)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# agent_bedrock.py reads BEDROCK_GUARDRAIL_ID/VERSION (or falls back to the
# gitignored, environment-specific guardrail_config.json) at *import* time.
# Tests that import it (directly, or transitively via github_issue_agent)
# shouldn't depend on that file existing -- set placeholders if unset so
# import never fails in a fresh checkout/CI with no real guardrail set up.
os.environ.setdefault("BEDROCK_GUARDRAIL_ID", "test-guardrail-id")
os.environ.setdefault("BEDROCK_GUARDRAIL_VERSION", "1")
