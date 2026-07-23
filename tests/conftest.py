import os


# Unit tests must not inherit local developer switches that launch real background Agent sessions.
os.environ["FINANCE_CC_SHADOW_ENABLED"] = "0"
os.environ["FINANCE_CC_TOOL_DEVELOPMENT_ENABLED"] = "0"
