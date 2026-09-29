import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FOUNDRY = Path.home() / ".foundry" / "bin"
have = shutil.which("anvil") or any((FOUNDRY / n).exists() for n in ("anvil", "anvil.exe"))


@pytest.mark.skipif(not have, reason="Foundry (anvil, forge) not installed")
def test_full_demo_on_seed_a():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PATH": os.environ["PATH"] + os.pathsep + str(FOUNDRY)}
    out = subprocess.run([sys.executable, "-m", "ariadne", "demo", "--seed", "A", "--port", "8599"], cwd=ROOT,
                         capture_output=True, text=True, timeout=900, env=env).stdout
    assert "Attacks rejected with no state change: 3/3" in out
    assert out.count("FINAL STATUS: VERIFIED") == 2
    assert "FINAL STATUS: NOT VERIFIED  (POOL_RULES, ATTESTATIONS)" in out
    assert "DEMO RESULT: as designed" in out
