"""Stand-in models behind ``agent.llm.StructuredLLM`` (``PP_MODEL=mock``).

The real model wrapper stays in ``agent/llm.py``; this package holds models
that are not LLMs but answer through the same interface.
"""
