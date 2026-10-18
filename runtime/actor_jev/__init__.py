"""Optional official TypeSafe provider; importing this package requires no SDK."""


def classifier_factory(**options):
    from .classifier import JevEventClassifier
    return JevEventClassifier(**options)
