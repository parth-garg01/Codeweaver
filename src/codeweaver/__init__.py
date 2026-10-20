"""CodeWeaver: local-first, LLM-free code migration through a shared IR.

Every stage of the pipeline runs on the local machine using only the Python
standard library.  No part of the runtime contacts a model provider, a hosted
inference service, or any network endpoint unless the user explicitly asks for
an action that requires it (for example a Git clone).
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
