"""Optional Codex Brain plugin. Official SDK imports are lazy."""

def brain_factory(**options):
    from .brain import CodexBrain
    return CodexBrain(**options)
