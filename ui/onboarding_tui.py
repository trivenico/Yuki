"""Small terminal onboarding frontend; core/onboarding owns validation and persistence."""
from __future__ import annotations
from pathlib import Path
from typing import Callable
from core.onboarding import DEFAULT_CONFIG, run_onboarding

def run(*, path: Path = DEFAULT_CONFIG, input_fn: Callable[[str],str] = input, validator=None):
    return run_onboarding(input_fn, path=path, validator=validator)

if __name__ == "__main__":
    config=run()
    print(f"Yuki configured with provider={config.get('provider','mock')!r}.")
