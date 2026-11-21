"""Built-in tools. Connector and MCP-wrapped tools are registered elsewhere."""

from bioquorum.tools.builtin.citation import CITATION_LOOKUP
from bioquorum.tools.builtin.download import DOWNLOAD_FILE
from bioquorum.tools.builtin.files import LIST_FILES, READ_FILE
from bioquorum.tools.builtin.kernel import PYTHON_EXECUTE, R_EXECUTE
from bioquorum.tools.spec import ToolSpec

BUILTIN_TOOLS: tuple[ToolSpec, ...] = (
    CITATION_LOOKUP,
    DOWNLOAD_FILE,
    LIST_FILES,
    PYTHON_EXECUTE,
    R_EXECUTE,
    READ_FILE,
)

__all__ = ["BUILTIN_TOOLS"]
