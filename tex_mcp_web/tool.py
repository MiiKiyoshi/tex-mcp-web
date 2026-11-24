"""tex-mcp-web as the modules it shares with the other review tools know it (shared/tool.py)."""

from . import page_token
from .shared.tool import Tool

TOOL = Tool(name="tex-mcp-web", package="tex_mcp_web", server="review server", event="[TeX review event]",
            headers=page_token.headers, pypi="tex-mcp-web")
