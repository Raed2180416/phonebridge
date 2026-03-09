# Tests

PhoneBridge tests are organized into three tiers:

- `tests/unit`
  Deterministic logic and contract tests.
- `tests/qt`
  Headless Qt/controller lifecycle tests.
- `tests/hardware`
  Manual or phone-dependent harnesses that are not part of CI.

Run the default deterministic suite:

```bash
./scripts/run_pytest_nix.sh -q -m "not hardware"
```

Run only the Qt-marked tests:

```bash
./scripts/run_qt_tests.sh
```

Run the hardware call/mic harness in inspect mode:

```bash
PYTHONPATH=. python3 tests/hardware/call_mic_harness.py --no-route-mutation
```

Run the hardware call/mic harness with active route verification:

```bash
PYTHONPATH=. python3 tests/hardware/call_mic_harness.py
```
