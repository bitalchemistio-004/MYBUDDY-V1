"""
test_lab — isolated hardware/pipeline test benches.

Each sub-package is one self-contained test: its own test firmware, its own
web interface, and its own server. Nothing here is imported by the production
backend, and nothing here modifies it; the benches only *read* from production
modules (e.g. the STT stage) where a test needs to exercise the real code path.
"""
