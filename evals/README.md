# Yuki Evaluation & Adversarial Safety Harness

This harness runs scenario-based evaluations and adversarial safety tests separately from unit tests.

## Running Evals

```sh
python evals/run_evals.py
```

## Scenario Sets

- **Scenario Set A**: Provider contract & resilience (availability, truncation, malformed JSON, timeout handling).
- **Scenario Set B**: Task agent safety & prompt injection defense (unregistered skill blocking, observation injection containment, output limits, approval denial).
- **Scenario Set C**: Conversational chat quality & memory injection smoke tests.
