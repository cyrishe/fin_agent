from __future__ import annotations

from typing import Any, Dict

from src.services.file_io_tool_service import FileIoToolService


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    return FileIoToolService().run(args or {})
