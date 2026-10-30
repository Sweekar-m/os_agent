"""
Capability-Driven Tool Registry for Windows OS Assistant.
Exposes actual, verified capabilities of the four specialized agents:
  1. Browser Agent
  2. File Agent
  3. System Agent
  4. Vision Agent

Enforces typed schemas, permission categories, risk levels, executable handlers,
and explicit verification criteria to eliminate fabricated success.
"""

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable, Awaitable, Union

from permission_engine import ActionCategory, RiskLevel
from agents.browser_agent import browser_agent
from agents.file_agent import file_agent
from agents.system_agent import system_agent
from agents.vision_agent import vision_agent

logger = logging.getLogger("tool_registry")
logger.setLevel(logging.INFO)


@dataclass
class ToolDefinition:
    name: str
    agent: str  # "Browser Agent", "File Agent", "System Agent", "Vision Agent"
    description: str
    parameters: Dict[str, Any]
    category: str
    risk_level: str
    handler: Callable[..., Any]
    completion_criterion: str
    verifier: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None

    def to_openai_tool(self) -> Dict[str, Any]:
        """Formats tool for OpenAI-compatible LLM tool calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": f"[{self.agent}] {self.description}",
                "parameters": self.parameters
            }
        }


class ToolRegistry:
    """
    Central registry exposing genuine agent capabilities.
    Prevents the LLM from hallucinating non-existent tools or executing arbitrary code.
    """

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._register_all_tools()

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self, agent_filter: Optional[str] = None) -> List[ToolDefinition]:
        if agent_filter:
            return [t for t in self._tools.values() if t.agent.lower() == agent_filter.lower()]
        return list(self._tools.values())

    def get_openai_tools(self, agent_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        return [t.to_openai_tool() for t in self.list_tools(agent_filter=agent_filter)]

    def get_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    async def execute(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Executes a registered tool with argument validation, verified execution,
        and truthful result structuring.
        """
        tool = self.get(name)
        if not tool:
            return {
                "success": False,
                "error": f"Tool '{name}' is not registered in the tool registry.",
                "verification": {"status": "failed", "reason": "unregistered_tool"}
            }

        params = params or {}
        logger.info(f"Executing tool '{name}' ({tool.agent}) with params: {params}")

        try:
            # Call handler (handling both sync and async functions)
            if inspect.iscoroutinefunction(tool.handler):
                raw_result = await tool.handler(**params)
            else:
                # Run synchronous IO in thread pool to prevent blocking event loop
                raw_result = await asyncio.to_thread(tool.handler, **params)

            # Ensure result is a dictionary
            if not isinstance(raw_result, dict):
                structured_result = {
                    "success": True,
                    "data": raw_result,
                    "message": str(raw_result)
                }
            else:
                structured_result = raw_result

            # Attach metadata
            structured_result.setdefault("tool_name", name)
            structured_result.setdefault("agent", tool.agent)

            # Apply explicit verifier if defined
            if tool.verifier:
                try:
                    verif_meta = tool.verifier(structured_result)
                    structured_result["verification"] = verif_meta
                except Exception as v_err:
                    logger.warning(f"Verifier error for tool '{name}': {v_err}")
                    structured_result["verification"] = {
                        "status": "unverified",
                        "limitation": f"Verification inspection error: {v_err}"
                    }
            else:
                # Default verification based on success flag
                status = "verified" if structured_result.get("success", True) else "failed"
                structured_result.setdefault("verification", {
                    "status": status,
                    "criterion": tool.completion_criterion
                })

            return structured_result

        except TypeError as type_err:
            logger.error(f"Parameter mismatch executing tool '{name}': {type_err}")
            return {
                "success": False,
                "error": f"Invalid arguments for tool '{name}': {type_err}",
                "tool_name": name,
                "agent": tool.agent,
                "verification": {"status": "failed", "reason": "invalid_parameters"}
            }
        except Exception as e:
            logger.error(f"Execution error in tool '{name}': {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "tool_name": name,
                "agent": tool.agent,
                "verification": {"status": "failed", "reason": str(e)}
            }

    # =========================================================================
    # Tool Registration Matrix (All 4 Specialized Agents)
    # =========================================================================

    def _register_all_tools(self):
        # ---------------------------------------------------------------------
        # 1. BROWSER AGENT TOOLS (NO PLAYWRIGHT)
        # ---------------------------------------------------------------------
        self.register(ToolDefinition(
            name="browser_list_tabs",
            agent="Browser Agent",
            description="Enumerate all open tabs across existing Chrome, Edge, and Brave browser sessions on Windows.",
            parameters={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer", "description": "Optional specific browser window handle"}
                }
            },
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.get_open_tabs,
            completion_criterion="Enumerate live browser tab titles and URLs via UIAutomation",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "method": "UIAutomationTabItem",
                "live_session": res.get("success", False),
                "total_tabs": res.get("total_tabs", len(res.get("tabs", [])))
            }
        ))

        self.register(ToolDefinition(
            name="browser_summarize_tabs",
            agent="Browser Agent",
            description="Inspect, read, and summarize contents of open browser tabs across existing Chrome, Edge, or Brave sessions.",
            parameters={
                "type": "object",
                "properties": {
                    "max_tabs": {"type": "integer", "default": 15, "description": "Maximum number of tabs to inspect and summarize"},
                    "hwnd": {"type": "integer", "description": "Optional browser window handle"}
                }
            },
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.summarize_open_tabs,
            completion_criterion="Read visible tab contents and generate concise summary of each open tab",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "method": "UIAutomationTextPattern",
                "live_session": res.get("success", False),
                "total_summarized": len(res.get("summaries", []))
            }
        ))

        self.register(ToolDefinition(
            name="browser_search",
            agent="Browser Agent",
            description="Perform a web search in the user's existing browser (Google, Bing, DuckDuckGo).",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search term or query"},
                    "engine": {"type": "string", "enum": ["google", "bing", "duckduckgo"], "default": "google"}
                },
                "required": ["query"]
            },
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.search,
            completion_criterion="Navigate browser to search engine results page"
        ))

        self.register(ToolDefinition(
            name="browser_navigate",
            agent="Browser Agent",
            description="Navigate the existing browser window to a specific URL.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The target website URL"}
                },
                "required": ["url"]
            },
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.navigate,
            completion_criterion="Focus address bar and navigate to target URL"
        ))

        self.register(ToolDefinition(
            name="browser_new_tab",
            agent="Browser Agent",
            description="Open a new tab in the existing browser window.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Optional URL to open in the new tab"}
                }
            },
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.new_tab,
            completion_criterion="Send new tab command and verify tab creation"
        ))

        self.register(ToolDefinition(
            name="browser_close_tab",
            agent="Browser Agent",
            description="Close the active tab in the existing browser window.",
            parameters={"type": "object", "properties": {}},
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.close_tab,
            completion_criterion="Close current tab"
        ))

        self.register(ToolDefinition(
            name="browser_switch_tab",
            agent="Browser Agent",
            description="Switch to next or previous tab in the existing browser window.",
            parameters={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["next", "prev"], "default": "next"}
                }
            },
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.switch_tab,
            completion_criterion="Switch active tab"
        ))

        self.register(ToolDefinition(
            name="browser_extract_text",
            agent="Browser Agent",
            description="Extract readable text content from the currently active browser page.",
            parameters={"type": "object", "properties": {}},
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.extract_page_text,
            completion_criterion="Extract visible page text via interactive desktop"
        ))

        self.register(ToolDefinition(
            name="browser_scroll",
            agent="Browser Agent",
            description="Scroll the active browser window up or down.",
            parameters={
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["down", "up"], "default": "down"},
                    "amount": {"type": "number", "default": 3.0}
                }
            },
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.scroll,
            completion_criterion="Scroll browser viewport"
        ))

        self.register(ToolDefinition(
            name="browser_click",
            agent="Browser Agent",
            description="Click at screen coordinates (x, y) in the browser window.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right"], "default": "left"},
                    "clicks": {"type": "integer", "default": 1}
                },
                "required": ["x", "y"]
            },
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.click_at,
            completion_criterion="Click target coordinates"
        ))

        self.register(ToolDefinition(
            name="browser_type",
            agent="Browser Agent",
            description="Type text into the focused browser element.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "press_enter": {"type": "boolean", "default": False}
                },
                "required": ["text"]
            },
            category=ActionCategory.BROWSER_ACTION,
            risk_level=RiskLevel.LOW,
            handler=browser_agent.type_text,
            completion_criterion="Type text into browser"
        ))

        # ---------------------------------------------------------------------
        # 2. FILE AGENT TOOLS
        # ---------------------------------------------------------------------
        self.register(ToolDefinition(
            name="file_recent_documents",
            agent="File Agent",
            description="Find recently saved or modified documents across Desktop, Documents, and Downloads on Windows.",
            parameters={
                "type": "object",
                "properties": {
                    "folders": {"type": "array", "items": {"type": "string"}, "description": "Specific folders to scan (e.g. ['Desktop', 'Documents', 'Downloads'])"},
                    "limit": {"type": "integer", "default": 10, "description": "Maximum number of recent documents to return"}
                }
            },
            category=ActionCategory.SAFE_READ,
            risk_level=RiskLevel.LOW,
            handler=file_agent.find_recent_documents,
            completion_criterion="Discover recently modified documents and preserve results for follow-up selection",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "total_found": res.get("total_found", len(res.get("documents", []))),
                "folders_checked": len(res.get("folders_scanned", []))
            }
        ))

        self.register(ToolDefinition(
            name="file_search",
            agent="File Agent",
            description="Search for files by filename, extension, directory, or content.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term, filename, or file extension (e.g. 'docx', 'resume', 'budget')"},
                    "directory": {"type": "string", "description": "Optional search directory alias or path (e.g. 'Desktop', 'Downloads')"},
                    "search_content": {"type": "boolean", "default": False, "description": "Whether to search inside file text content"}
                },
                "required": ["query"]
            },
            category=ActionCategory.FILE_READ,
            risk_level=RiskLevel.LOW,
            handler=file_agent.search_files,
            completion_criterion="Search files across directory and return matches",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "matches_count": res.get("total_found", len(res.get("files", [])))
            }
        ))

        self.register(ToolDefinition(
            name="file_open",
            agent="File Agent",
            description="Open a file or document visibly in its default registered Windows desktop application, with launch verification.",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Target filename, path, or ordinal from recent search (e.g. 'resume.pdf', 'the second one', '1')"},
                    "fallback_dir": {"type": "string", "description": "Optional directory to look in if target is just a filename"}
                },
                "required": ["target"]
            },
            category=ActionCategory.SAFE_READ,
            risk_level=RiskLevel.LOW,
            handler=lambda target=None, target_or_id=None, **kw: file_agent.open_file(target if target is not None else target_or_id),
            completion_criterion="Locate file, dispatch Windows startfile command, and verify window launch",
            verifier=lambda res: {
                "status": "verified" if (res.get("success") and res.get("opened")) else "failed",
                "file_found": res.get("file_found", False),
                "visibly_opened": res.get("visibly_opened", False),
                "filename": res.get("filename"),
                "resolved_path": res.get("resolved_path")
            }
        ))

        self.register(ToolDefinition(
            name="file_read",
            agent="File Agent",
            description="Read content of a text or code file within authorized paths.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "max_lines": {"type": "integer", "default": 300},
                    "offset": {"type": "integer", "default": 0}
                },
                "required": ["path"]
            },
            category=ActionCategory.FILE_READ,
            risk_level=RiskLevel.LOW,
            handler=lambda path=None, file_path=None, max_lines=300, offset=0, **kw: file_agent.read_file(file_path=path or file_path, max_lines=max_lines, offset=offset),
            completion_criterion="Read and return file lines with preserved encoding"
        ))

        self.register(ToolDefinition(
            name="file_write",
            agent="File Agent",
            description="Create or overwrite a file within authorized paths, creating an automatic backup.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write"},
                    "content": {"type": "string", "description": "Content to write into the file"},
                    "overwrite": {"type": "boolean", "default": False}
                },
                "required": ["path", "content"]
            },
            category=ActionCategory.FILE_WRITE,
            risk_level=RiskLevel.MEDIUM,
            handler=lambda path=None, file_path=None, content="", overwrite=False, **kw: file_agent.write_file(file_path=path or file_path, content=content, overwrite=overwrite),
            completion_criterion="Write file content and record backup"
        ))

        self.register(ToolDefinition(
            name="file_edit",
            agent="File Agent",
            description="Edit an existing file by replacing a specific text snippet with new text, generating a unified diff.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to edit"},
                    "find_text": {"type": "string", "description": "Text to find"},
                    "replace_text": {"type": "string", "description": "Replacement text"}
                },
                "required": ["path", "find_text", "replace_text"]
            },
            category=ActionCategory.FILE_WRITE,
            risk_level=RiskLevel.MEDIUM,
            handler=lambda path=None, file_path=None, find_text="", replace_text="", **kw: file_agent.edit_file(file_path=path or file_path, find_text=find_text, replace_text=replace_text),
            completion_criterion="Replace text and generate unified diff"
        ))

        self.register(ToolDefinition(
            name="file_delete",
            agent="File Agent",
            description="Delete a file (requires safety confirmation).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to delete"},
                    "confirm": {"type": "boolean", "default": False}
                },
                "required": ["path"]
            },
            category=ActionCategory.FILE_DELETE,
            risk_level=RiskLevel.HIGH,
            handler=lambda path=None, file_path=None, confirm=False, **kw: file_agent.delete_file(file_path=path or file_path, confirm=confirm),
            completion_criterion="Delete file if confirmed, create backup beforehand"
        ))

        self.register(ToolDefinition(
            name="file_list_dir",
            agent="File Agent",
            description="List directory entries with file sizes and modification timestamps.",
            parameters={
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to list (defaults to workspace)"},
                    "limit": {"type": "integer", "default": 50}
                }
            },
            category=ActionCategory.SAFE_READ,
            risk_level=RiskLevel.LOW,
            handler=lambda directory=None, limit=50, **kw: file_agent.list_dir(directory=directory, limit=limit),
            completion_criterion="Return list of directory items"
        ))

        self.register(ToolDefinition(
            name="file_generate_document",
            agent="File Agent",
            description="Generate formatted documents such as PDF (.pdf), Word (.docx), Excel (.xlsx), PowerPoint (.pptx), or Markdown (.md) with structured sections, headings, and bullet points.",
            parameters={
                "type": "object",
                "properties": {
                    "doc_type": {"type": "string", "enum": ["pdf", "docx", "xlsx", "pptx", "txt", "md"], "default": "pdf", "description": "Document format to generate"},
                    "title": {"type": "string", "description": "Title or topic of the document"},
                    "topic": {"type": "string", "description": "Detailed subject or domain"},
                    "content": {"type": "string", "description": "Optional body text or summary details"},
                    "output_path": {"type": "string", "description": "Optional custom file destination path"},
                    "theme": {"type": "string", "default": "professional"}
                },
                "required": ["doc_type", "title"]
            },
            category=ActionCategory.FILE_WRITE,
            risk_level=RiskLevel.LOW,
            handler=file_agent.generate_document,
            completion_criterion="Generate formatted document on disk and verify file existence and non-zero size",
            verifier=lambda res: {
                "status": "verified" if (res.get("success") and res.get("generated")) else "failed",
                "filename": res.get("filename"),
                "path": res.get("path"),
                "size_bytes": res.get("size_bytes"),
                "doc_type": res.get("doc_type")
            }
        ))

        # ---------------------------------------------------------------------
        # 3. SYSTEM AGENT TOOLS
        # ---------------------------------------------------------------------
        self.register(ToolDefinition(
            name="system_telemetry",
            agent="System Agent",
            description="Read Windows hardware metrics: CPU load, memory utilization, disk usage, and system uptime.",
            parameters={"type": "object", "properties": {}},
            category=ActionCategory.SAFE_READ,
            risk_level=RiskLevel.LOW,
            handler=system_agent.get_system_telemetry,
            completion_criterion="Inspect live hardware utilization metrics",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "source": "psutil",
                "cpu_percent": res.get("cpu", {}).get("percent") if isinstance(res.get("cpu"), dict) else None,
                "memory_percent": res.get("memory", {}).get("percent") if isinstance(res.get("memory"), dict) else None
            }
        ))

        self.register(ToolDefinition(
            name="system_diagnose",
            agent="System Agent",
            description="Analyze system health, finding bottlenecks, high RAM consumers, or disk alerts.",
            parameters={"type": "object", "properties": {}},
            category=ActionCategory.SAFE_READ,
            risk_level=RiskLevel.LOW,
            handler=system_agent.diagnose_system,
            completion_criterion="Analyze system health and top resource consumers",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "health_status": res.get("status")
            }
        ))

        self.register(ToolDefinition(
            name="system_list_processes",
            agent="System Agent",
            description="List running Windows processes with PIDs and memory usage.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 20},
                    "filter_name": {"type": "string", "description": "Optional process name filter"}
                }
            },
            category=ActionCategory.SAFE_READ,
            risk_level=RiskLevel.LOW,
            handler=system_agent.list_processes,
            completion_criterion="Return list of running processes",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "processes_count": len(res.get("processes", []))
            }
        ))

        self.register(ToolDefinition(
            name="system_terminate_process",
            agent="System Agent",
            description="Terminate an active Windows process by PID or Name (requires confirmation).",
            parameters={
                "type": "object",
                "properties": {
                    "pid": {"type": "integer"},
                    "name": {"type": "string"},
                    "confirm": {"type": "boolean", "default": True}
                }
            },
            category=ActionCategory.SYSTEM_KILL,
            risk_level=RiskLevel.HIGH,
            handler=system_agent.terminate_process,
            completion_criterion="Terminate requested process and verify termination"
        ))

        self.register(ToolDefinition(
            name="system_launch_app",
            agent="System Agent",
            description="Launch an application or Windows tool (e.g. notepad, calc, explorer, cmd).",
            parameters={
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "Application name or path (e.g. 'notepad', 'calc', 'explorer')"}
                },
                "required": ["app"]
            },
            category=ActionCategory.SYSTEM_LAUNCH,
            risk_level=RiskLevel.LOW,
            handler=lambda app=None, app_name=None, application=None, target=None, name=None, **kw: system_agent.launch_app(app or app_name or application or target or name or "notepad"),
            completion_criterion="Spawn target application process in interactive desktop session",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "launched": res.get("launched"),
                "pid": res.get("pid")
            }
        ))

        self.register(ToolDefinition(
            name="system_wait_for_window",
            agent="System Agent",
            description="Wait for an application window matching title substring to appear and focus it.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title substring of the target window (e.g. 'Notepad', 'Calculator')"},
                    "timeout": {"type": "number", "default": 10.0}
                },
                "required": ["title"]
            },
            category=ActionCategory.SAFE_READ,
            risk_level=RiskLevel.LOW,
            handler=lambda title=None, window_title=None, title_substr=None, timeout=10.0, **kw: system_agent.wait_for_window(title or window_title or title_substr or "Notepad", timeout=float(timeout)),
            completion_criterion="Find window handle matching title and bring to foreground",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "hwnd": res.get("hwnd"),
                "title": res.get("title")
            }
        ))

        self.register(ToolDefinition(
            name="system_type_into_window",
            agent="System Agent",
            description="Enter text into a target window via UI Automation ValuePattern or keyboard strokes.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to type or set into the window"},
                    "title": {"type": "string", "description": "Optional title substring to ensure target window is focused"}
                },
                "required": ["text"]
            },
            category=ActionCategory.SYSTEM_LAUNCH,
            risk_level=RiskLevel.LOW,
            handler=lambda text=None, content=None, string=None, passage=None, title=None, window_title=None, title_substr=None, **kw: system_agent.type_into_window(text if text is not None else (content or string or passage or ""), title_substr=title or window_title or title_substr),
            completion_criterion="Set text in target window edit control"
        ))

        self.register(ToolDefinition(
            name="system_verify_window_text",
            agent="System Agent",
            description="Verify that an expected text snippet is present inside an active application window via UI Automation.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Target window title substring"},
                    "expected_snippet": {"type": "string", "description": "Expected text snippet to verify"},
                    "timeout": {"type": "number", "default": 5.0}
                },
                "required": ["title", "expected_snippet"]
            },
            category=ActionCategory.SAFE_READ,
            risk_level=RiskLevel.LOW,
            handler=lambda title=None, window_title=None, title_substr=None, expected_snippet=None, expected_text=None, snippet=None, text=None, timeout=5.0, **kw: system_agent.verify_window_text(title or window_title or title_substr or "Notepad", expected_snippet if expected_snippet is not None else (expected_text or snippet or text or ""), timeout=float(timeout)),
            completion_criterion="Inspect window text controls via UI Automation to verify text presence",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "expected_snippet": res.get("expected_snippet"),
                "verified_match": res.get("success", False)
            }
        ))

        self.register(ToolDefinition(
            name="system_open_path",
            agent="System Agent",
            description="Open any file or folder in its default registered Windows application.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to file or folder"}
                },
                "required": ["path"]
            },
            category=ActionCategory.SAFE_READ,
            risk_level=RiskLevel.LOW,
            handler=system_agent.open_in_default_app,
            completion_criterion="Open path with default application"
        ))

        self.register(ToolDefinition(
            name="system_run_command",
            agent="System Agent",
            description="Run a terminal command (PowerShell or CMD) with timeout.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "shell": {"type": "string", "enum": ["powershell", "cmd"], "default": "powershell"}
                },
                "required": ["command"]
            },
            category=ActionCategory.SYSTEM_COMMAND,
            risk_level=RiskLevel.MEDIUM,
            handler=system_agent.run_terminal_command,
            completion_criterion="Run command and capture exit code, stdout, and stderr"
        ))

        self.register(ToolDefinition(
            name="system_press_hotkey",
            agent="System Agent",
            description="Send keyboard shortcut or keystroke sequence (e.g. 'ctrl+s', 'enter', 'tab', 'alt+f4', or calculator math like '25*4=') to the focused window.",
            parameters={
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "Key, shortcut, or sequence (e.g. 'ctrl+s', 'enter', '45*10=')"},
                    "title": {"type": "string", "description": "Optional target window title to focus before pressing keys"}
                },
                "required": ["keys"]
            },
            category=ActionCategory.SYSTEM_LAUNCH,
            risk_level=RiskLevel.LOW,
            handler=lambda keys=None, key=None, shortcut=None, title=None, window_title=None, **kw: system_agent.press_key_or_hotkey(keys or key or shortcut or "enter", title=title or window_title),
            completion_criterion="Send keystrokes to active or specified window",
            verifier=lambda res: {
                "status": "verified" if res.get("success") else "failed",
                "keys": res.get("keys"),
                "window": res.get("window")
            }
        ))

        # ---------------------------------------------------------------------
        # 4. VISION AGENT TOOLS
        # ---------------------------------------------------------------------
        self.register(ToolDefinition(
            name="vision_analyze_screen",
            agent="Vision Agent",
            description="Capture current Windows desktop screen and analyze visible UI elements, active applications, and error dialogs.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Specific question or instruction for visual analysis"}
                }
            },
            category=ActionCategory.VISION_CAPTURE,
            risk_level=RiskLevel.LOW,
            handler=vision_agent.analyze_screen,
            completion_criterion="Capture screenshot and generate multimodal visual assessment",
            verifier=lambda res: {
                "status": "verified" if res.get("success", True) else "failed",
                "active_window": res.get("active_window", {}).get("title")
            }
        ))

        self.register(ToolDefinition(
            name="vision_locate_target",
            agent="Vision Agent",
            description="Locate screen pixel coordinates (x, y) of a named visual UI element.",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Description of element to locate (e.g. 'Submit button', 'Search icon')"}
                },
                "required": ["target"]
            },
            category=ActionCategory.VISION_CAPTURE,
            risk_level=RiskLevel.LOW,
            handler=vision_agent.locate_target_coordinates,
            completion_criterion="Determine pixel coordinates for target element"
        ))

        self.register(ToolDefinition(
            name="vision_explain_error",
            agent="Vision Agent",
            description="Inspect the current screen to locate, read, and explain any visible error or warning dialog.",
            parameters={"type": "object", "properties": {}},
            category=ActionCategory.VISION_CAPTURE,
            risk_level=RiskLevel.LOW,
            handler=vision_agent.explain_error_on_screen,
            completion_criterion="Locate and explain visible screen error"
        ))


tool_registry = ToolRegistry()
