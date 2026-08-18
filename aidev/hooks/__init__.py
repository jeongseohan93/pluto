"""Claude Code hooks the pipeline hands to a stage through ``--settings``.

Nothing here is imported by the pipeline: each file is a self-contained script
run by the CLI in the target repository, so it must not depend on ``aidev``
being importable from there.
"""
