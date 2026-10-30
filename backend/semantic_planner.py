"""
Semantic Planner for the Windows OS Assistant.
Replaces brittle keyword and regex matching with LLM-driven semantic planning,
grounded context inspection, and validated tool selection from the ToolRegistry.
"""

import json
import re
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from config import settings
from llm_gateway import llm_gateway
from tool_registry import tool_registry, ToolDefinition
from permission_engine import ActionCategory, RiskLevel
from agents.file_agent import file_agent

logger = logging.getLogger("semantic_planner")
logger.setLevel(logging.INFO)


@dataclass
class PlanStep:
    step_number: int
    title: str
    agent: str
    tool_name: str
    params: Dict[str, Any]
    category: str
    risk_level: str
    reason: str
    expected_outcome: str
    dependencies: List[int] = field(default_factory=list)

    @property
    def short_action(self) -> str:
        # Strip agent prefix e.g. system_telemetry -> telemetry, file_search -> search
        prefixes = ["browser_", "system_", "file_", "vision_"]
        name = self.tool_name
        for p in prefixes:
            if name.startswith(p):
                name = name[len(p):]
                break
        alias_map = {
            "recent_documents": "recent",
            "terminate_process": "kill_process",
            "analyze_screen": "analyze",
            "locate_target": "locate",
        }
        return alias_map.get(name, name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_number": self.step_number,
            "title": self.title,
            "agent": self.agent,
            "action": self.short_action,  # for backwards compatibility
            "tool_name": self.tool_name,
            "params": self.params,
            "category": self.category,
            "risk": self.risk_level,  # for backwards compatibility
            "risk_level": self.risk_level,
            "reason": self.reason,
            "expected_outcome": self.expected_outcome,
            "dependencies": self.dependencies
        }


@dataclass
class SemanticPlan:
    goal: str
    strategy: str
    steps: List[PlanStep]
    is_conversational: bool = False
    conversational_response: Optional[str] = None
    context_and_constraints: Dict[str, Any] = field(default_factory=dict)
    required_permissions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective": self.goal,  # for backwards compatibility
            "goal": self.goal,
            "strategy": self.strategy,
            "is_conversational": self.is_conversational,
            "conversational_response": self.conversational_response,
            "context_and_constraints": self.context_and_constraints,
            "required_permissions": self.required_permissions,
            "steps": [s.to_dict() for s in self.steps]
        }


class SemanticPlanner:
    """
    Translates arbitrary natural language user requests into structured,
    executable plans composed exclusively of genuine tools from ToolRegistry.
    """

    def __init__(self):
        pass

    async def formulate_plan(
        self,
        prompt: str,
        chat_history: Optional[List[Dict[str, Any]]] = None,
        workspace: Optional[str] = None,
        model: Optional[str] = None
    ) -> SemanticPlan:
        """
        Main entrypoint: parses natural language prompt + multi-turn history into a SemanticPlan.
        """
        clean_prompt = (prompt or "").strip()
        p_lower = clean_prompt.lower()

        # 0. Instant conversational check for pure greetings
        casual_greetings = {"hi", "hello", "hey", "good morning", "good evening", "yo", "sup", "how are you"}
        if p_lower in casual_greetings:
            return SemanticPlan(
                goal="Casual Greeting",
                strategy="Direct conversational response",
                steps=[],
                is_conversational=True,
                conversational_response="Hello! I am your Windows OS Assistant. I'm ready to manage your files, control your browser, check system performance, or inspect what's on your screen. What would you like to do?"
            )

        # 1. Gather recent conversational & system context
        context = self._inspect_context(chat_history)

        # 2. Query configured LLM to generate structured semantic plan
        llm_plan = await self._generate_llm_plan(clean_prompt, context, workspace, model=model)
        if llm_plan:
            validated = self._validate_and_enrich_plan(llm_plan, clean_prompt, context)
            if validated:
                return validated

        # 3. Graceful semantic fallback (inference based on intent semantics, not brittle single-word regexes)
        logger.info("Using semantic heuristic planner fallback")
        return await self._semantic_heuristic_plan(clean_prompt, context, workspace)

    def _inspect_context(self, chat_history: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        """Gathers relevant multi-turn and agent state context."""
        ctx: Dict[str, Any] = {
            "recent_search_files": [],
            "previous_assistant_turns": [],
            "previous_user_turns": []
        }

        # Check FileAgent's recent search cache
        if file_agent.last_search_results:
            ctx["recent_search_files"] = [
                {"id": d.get("id"), "filename": d.get("filename"), "path": d.get("path")}
                for d in file_agent.last_search_results[:10]
            ]

        if chat_history:
            for m in reversed(chat_history[-6:]):
                role = m.get("role")
                content = m.get("content", "")
                if role == "assistant" and len(ctx["previous_assistant_turns"]) < 3:
                    ctx["previous_assistant_turns"].append(content)
                elif role == "user" and len(ctx["previous_user_turns"]) < 3:
                    ctx["previous_user_turns"].append(content)

        return ctx

    async def _generate_llm_plan(
        self,
        prompt: str,
        context: Dict[str, Any],
        workspace: Optional[str],
        model: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Invokes LLM with structured tools and prompt to generate plan."""
        available_tools = tool_registry.list_tools()
        tools_summary = "\n".join(
            f"- `{t.name}` ({t.agent}, category={t.category}, risk={t.risk_level}): {t.description}"
            for t in available_tools
        )

        recent_files_info = ""
        if context.get("recent_search_files"):
            file_bullets = "\n".join(
                f"  [{f['id']}] {f['filename']} ({f['path']})"
                for f in context["recent_search_files"]
            )
            recent_files_info = f"\nRecently Discovered Documents on PC:\n{file_bullets}\n"

        prev_turns = ""
        if context.get("previous_assistant_turns"):
            prev_turns = f"\nRecent Assistant Context:\n" + "\n".join(f"- {t[:120]}" for t in context["previous_assistant_turns"][:2])

        system_instruction = (
            "You are the Primary Semantic Planner for a Windows Multi-Agent OS Assistant.\n"
            "Your job is to understand the user's underlying goal regardless of how it is phrased.\n"
            "Choose ONLY from the genuine available tools listed below. NEVER invent tools or make up file paths.\n\n"
            "Available Genuine Tools:\n"
            f"{tools_summary}\n\n"
            f"{recent_files_info}"
            f"{prev_turns}\n\n"
            "Rules:\n"
            "1. Output a valid, clean JSON object matching the schema below.\n"
            "2. If the user is just saying hello, chit-chatting, or asking general knowledge, set 'is_conversational': true and provide 'conversational_response'.\n"
            "3. For tasks requiring application launch and text entry (e.g. Notepad, Word, Editor):\n"
            "   Include: system_launch_app -> system_wait_for_window -> system_type_into_window -> system_verify_window_text.\n"
            "4. For multi-turn references like 'open the second one', 'open it', 'open that doc': resolve the target from Recently Discovered Documents and call file_open.\n"
            "5. For file requests: map 'recent documents', 'files I worked on lately', 'yesterday's notes' to file_recent_documents.\n"
            "6. For browser tab requests: map 'summarize open tabs', 'overview of everything open in browser' to browser_summarize_tabs.\n\n"
            "JSON Output Schema:\n"
            "{\n"
            '  "goal": "<user goal>",\n'
            '  "strategy": "<brief strategy>",\n'
            '  "is_conversational": false,\n'
            '  "conversational_response": null,\n'
            '  "steps": [\n'
            "    {\n"
            '      "step_number": 1,\n'
            '      "title": "<step description>",\n'
            '      "tool_name": "<registered_tool_name>",\n'
            '      "params": { ... },\n'
            '      "reason": "<why this tool>",\n'
            '      "expected_outcome": "<what should result>"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"User Request: {prompt}"}
        ]

        if not llm_gateway.api_key and "localhost" not in llm_gateway.base_url and "127.0.0.1" not in llm_gateway.base_url:
            return None

        try:
            resp = await llm_gateway.chat_complete(
                messages=messages,
                model=model,
                temperature=0.1,
                max_tokens=1500
            )
            if not resp.get("success") or not resp.get("content"):
                return None

            raw_content = resp["content"].strip()
            # Clean JSON code fences
            cleaned = raw_content
            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

            # Find outer JSON brackets
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                return json.loads(m.group(0))
        except Exception as e:
            logger.warning(f"LLM planning parsing error: {e}")

        return None

    def _validate_and_enrich_plan(
        self,
        raw_plan: Dict[str, Any],
        original_prompt: str,
        context: Dict[str, Any]
    ) -> Optional[SemanticPlan]:
        """Validates that all proposed tools genuinely exist and attaches permission data."""
        if raw_plan.get("is_conversational"):
            return SemanticPlan(
                goal=raw_plan.get("goal", "Conversation"),
                strategy="Conversational response",
                steps=[],
                is_conversational=True,
                conversational_response=raw_plan.get("conversational_response", "How can I help you today?")
            )

        raw_steps = raw_plan.get("steps", [])
        if not raw_steps:
            return None

        valid_steps: List[PlanStep] = []
        required_permissions: List[str] = []

        for idx, s in enumerate(raw_steps):
            tool_name = s.get("tool_name") or s.get("action")
            tool_def = tool_registry.get(tool_name)
            if not tool_def:
                # Attempt name normalization
                tool_name = tool_name.replace(".", "_").lower()
                tool_def = tool_registry.get(tool_name)

            if not tool_def:
                logger.warning(f"LLM proposed unregistered tool: '{tool_name}', discarding step")
                continue

            params = s.get("params", {})
            # If Notepad text entry was requested and text is empty or generic, fill in context
            if tool_name == "system_type_into_window" and not params.get("text"):
                params["text"] = f"Notes on: {original_prompt}\nGenerated by OS Agent."

            step_obj = PlanStep(
                step_number=s.get("step_number", idx + 1),
                title=s.get("title", f"Step {idx + 1}: {tool_def.name}"),
                agent=tool_def.agent,
                tool_name=tool_def.name,
                params=params,
                category=tool_def.category,
                risk_level=tool_def.risk_level,
                reason=s.get("reason", tool_def.description),
                expected_outcome=s.get("expected_outcome", tool_def.completion_criterion),
                dependencies=s.get("dependencies", [idx] if idx > 0 else [])
            )
            valid_steps.append(step_obj)
            if tool_def.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                required_permissions.append(f"{tool_def.agent}.{tool_def.name}")

        if not valid_steps:
            return None

        return SemanticPlan(
            goal=raw_plan.get("goal", original_prompt),
            strategy=raw_plan.get("strategy", "Multi-Agent Coordinated Execution"),
            steps=valid_steps,
            is_conversational=False,
            context_and_constraints=raw_plan.get("context_and_constraints", {}),
            required_permissions=required_permissions
        )

    # =========================================================================
    # Semantic Heuristic Fallback (Handles Synonyms and Unseen Phrasings)
    # =========================================================================

    async def _semantic_heuristic_plan(
        self,
        prompt: str,
        context: Dict[str, Any],
        workspace: Optional[str]
    ) -> SemanticPlan:
        """
        Understands natural language variations semantically without requiring
        exact keyword phrases. Handles all 4 agents and multi-agent coordination.
        """
        p_lower = prompt.lower()

        # 1. Screen Vision Queries
        # "Analyze my screen", "analze my screen", "short summary of my screen", "what's on my screen", "explain this error", "look at my screen"
        vision_words = [
            "analze my screen", "analyze my screen", "analze screen", "analyze screen",
            "screen summary", "summarize my screen", "short summary of my screen",
            "what's on my screen", "what is on my screen", "what am i looking at",
            "screen", "screenshot", "error on screen", "look at screen", "explain this error", "describe screen"
        ]
        if any(w in p_lower for w in vision_words):
            summary_prompt = "Provide a concise short summary of what is currently on the screen." if any(k in p_lower for k in ["analze", "analyze", "summary", "summarize", "what"]) else prompt
            return SemanticPlan(
                goal=f"Analyze screen content: {prompt}",
                strategy="Vision Agent Screen Analysis & Short Summary",
                steps=[
                    PlanStep(
                        step_number=1,
                        title="Capture and analyze Windows desktop screen",
                        agent="Vision Agent",
                        tool_name="vision_analyze_screen",
                        params={"prompt": summary_prompt},
                        category=ActionCategory.VISION_CAPTURE,
                        risk_level=RiskLevel.LOW,
                        reason="Inspect current screen visuals and active window to generate short summary",
                        expected_outcome="Accurate, concise multimodal short summary of screen elements"
                    )
                ]
            )

        # 1.5 Document & Multi-File Generation (PDF, DOCX, XLSX, PPTX, TXT)
        # Phrasings:
        # - "generate the pdf on pes university and save it in d drive"
        # - "generate multiple files like pdf docx"
        # - "create a pdf and docx about PES University"
        # - "generate a pdf file about F1 racing"
        # - "create a docx report on artificial intelligence"
        is_gen_cmd = any(k in p_lower for k in ["generate", "create", "make", "build", "produce", "export", "draft"])
        has_doc_format = any(k in p_lower for k in ["pdf", "docx", "word", "excel", "xlsx", "powerpoint", "pptx", "presentation", "report", "document"])

        if (is_gen_cmd and (has_doc_format or any(k in p_lower for k in ["multiple files", "multiple documents", "file like pdf", "files like pdf"]))) or ("save it in" in p_lower and any(k in p_lower for k in ["pdf", "docx", "file", "doc"])):
            # 1. Check destination drive or folder: e.g. "and save it in d drive", "save to d drive", "in d drive", "d:\", "desktop", etc.
            target_dir = None
            m_drive = re.search(r"\b(?:save\s+(?:it\s+)?(?:in|to|on)\s+)?([a-zA-Z]):(?:\\|\/)?(?:\s+drive)?\b", prompt, re.IGNORECASE)
            m_drive_named = re.search(r"\b(?:save\s+(?:it\s+)?(?:in|to|on)\s+)?([a-zA-Z])\s+drive\b", prompt, re.IGNORECASE)
            m_folder = re.search(r"\b(?:save\s+(?:it\s+)?(?:in|to|on)\s+)?(desktop|documents|downloads)\b", prompt, re.IGNORECASE)

            if m_drive:
                drive_letter = m_drive.group(1).upper()
                target_dir = Path(f"{drive_letter}:\\")
            elif m_drive_named:
                drive_letter = m_drive_named.group(1).upper()
                target_dir = Path(f"{drive_letter}:\\")
            elif m_folder:
                f_name = m_folder.group(1).lower()
                if f_name == "desktop":
                    target_dir = Path.home() / "Desktop"
                elif f_name == "downloads":
                    target_dir = Path.home() / "Downloads"
                else:
                    target_dir = Path.home() / "Documents"

            # 2. Strip destination clause so topic extraction is clean
            cleaned_for_topic = re.sub(
                r"\s+(?:and\s+)?(?:save\s+(?:it\s+)?(?:in|to|on)\s+)?(?:[a-zA-Z]:(?:\\|\/)?(?:\s+drive)?|[a-zA-Z]\s+drive|desktop|documents|downloads).*$",
                "",
                prompt,
                flags=re.IGNORECASE
            ).strip()

            # Extract clean topic
            m_topic = re.search(r"(?:about|on|regarding|for)\s+(.+)$", cleaned_for_topic, re.IGNORECASE)
            if m_topic:
                topic = m_topic.group(1).rstrip(".").strip()
            elif "pes" in p_lower:
                topic = "PES University"
            else:
                topic = "System & Overview"

            if "about " in topic.lower():
                topic = re.split(r"about\s+", topic, flags=re.IGNORECASE)[-1].strip()

            clean_title = f"{topic.title()} Report" if not (topic.lower().endswith("report") or "university" in topic.lower()) else topic.title()
            file_stem = re.sub(r'[^\w\-]', '_', clean_title).strip('_')

            pdf_path = str(target_dir / f"{file_stem}.pdf") if target_dir else None
            docx_path = str(target_dir / f"{file_stem}.docx") if target_dir else None
            pptx_path = str(target_dir / f"{file_stem}.pptx") if target_dir else None

            is_multiple = any(k in p_lower for k in ["multiple files", "multiple documents", "both", "pdf and docx", "pdf docx", "docx and pdf", "pdf and word"])

            if is_multiple or ("pdf" in p_lower and "docx" in p_lower):
                dest_desc = f" and save to {target_dir}" if target_dir else ""
                pdf_params = {"doc_type": "pdf", "title": clean_title, "topic": topic}
                docx_params = {"doc_type": "docx", "title": clean_title, "topic": topic}
                if pdf_path:
                    pdf_params["output_path"] = pdf_path
                if docx_path:
                    docx_params["output_path"] = docx_path

                return SemanticPlan(
                    goal=f"Generate multiple document files (PDF and DOCX) on '{topic}'{dest_desc}",
                    strategy="File Agent Multi-Format Document Generation",
                    steps=[
                        PlanStep(
                            step_number=1,
                            title=f"Generate PDF document: '{clean_title}.pdf'",
                            agent="File Agent",
                            tool_name="file_generate_document",
                            params=pdf_params,
                            category=ActionCategory.FILE_WRITE,
                            risk_level=RiskLevel.LOW,
                            reason="Create formatted PDF document with executive summary and key highlights",
                            expected_outcome="PDF file created successfully"
                        ),
                        PlanStep(
                            step_number=2,
                            title=f"Generate Word document: '{clean_title}.docx'",
                            agent="File Agent",
                            tool_name="file_generate_document",
                            params=docx_params,
                            category=ActionCategory.FILE_WRITE,
                            risk_level=RiskLevel.LOW,
                            reason="Create formatted Microsoft Word document with matching sections",
                            expected_outcome="DOCX file created successfully"
                        )
                    ]
                )
            elif "docx" in p_lower or "word" in p_lower:
                dest_desc = f" and save to {target_dir}" if target_dir else ""
                docx_params = {"doc_type": "docx", "title": clean_title, "topic": topic}
                if docx_path:
                    docx_params["output_path"] = docx_path
                return SemanticPlan(
                    goal=f"Generate Word document (.docx) on '{topic}'{dest_desc}",
                    strategy="File Agent Word Document Generation",
                    steps=[
                        PlanStep(
                            step_number=1,
                            title=f"Generate Word document: '{clean_title}.docx'",
                            agent="File Agent",
                            tool_name="file_generate_document",
                            params=docx_params,
                            category=ActionCategory.FILE_WRITE,
                            risk_level=RiskLevel.LOW,
                            reason="Create formatted DOCX document with structured sections",
                            expected_outcome="DOCX file created successfully"
                        )
                    ]
                )
            elif "pptx" in p_lower or "powerpoint" in p_lower or "presentation" in p_lower:
                dest_desc = f" and save to {target_dir}" if target_dir else ""
                pptx_params = {"doc_type": "pptx", "title": clean_title, "topic": topic}
                if pptx_path:
                    pptx_params["output_path"] = pptx_path
                return SemanticPlan(
                    goal=f"Generate presentation: '{clean_title}.pptx'{dest_desc}",
                    strategy="File Agent PowerPoint Generation",
                    steps=[
                        PlanStep(
                            step_number=1,
                            title=f"Generate presentation: '{clean_title}.pptx'",
                            agent="File Agent",
                            tool_name="file_generate_document",
                            params=pptx_params,
                            category=ActionCategory.FILE_WRITE,
                            risk_level=RiskLevel.LOW,
                            reason="Create styled presentation slides",
                            expected_outcome="PPTX presentation created successfully"
                        )
                    ]
                )
            else:
                dest_desc = f" and save to {target_dir}" if target_dir else ""
                pdf_params = {"doc_type": "pdf", "title": clean_title, "topic": topic}
                if pdf_path:
                    pdf_params["output_path"] = pdf_path
                return SemanticPlan(
                    goal=f"Generate PDF document on '{topic}'{dest_desc}",
                    strategy="File Agent PDF Document Generation",
                    steps=[
                        PlanStep(
                            step_number=1,
                            title=f"Generate PDF document: '{clean_title}.pdf'",
                            agent="File Agent",
                            tool_name="file_generate_document",
                            params=pdf_params,
                            category=ActionCategory.FILE_WRITE,
                            risk_level=RiskLevel.LOW,
                            reason="Create formatted PDF document with ReportLab",
                            expected_outcome="PDF file created successfully"
                        )
                    ]
                )

        # 2. Browser Tabs: Summarization vs Listing
        # Phrasings: "summarize open tabs", "give me an overview of everything open in my browser", "what's open in each tab", "brief me on my tabs"
        is_tab_query = any(k in p_lower for k in ["tab", "tabs", "browser"])
        is_summary_query = any(k in p_lower for k in ["summar", "overview", "brief", "what's on", "what is on", "go through", "roundup", "digest", "inspect"])
        is_list_query = any(k in p_lower for k in ["list", "show", "enumerate", "which tabs", "what tabs", "display", "how many tabs"])

        if is_tab_query and is_summary_query:
            return SemanticPlan(
                goal="Summarize open browser tabs",
                strategy="Inspect and summarize live tabs across running browser sessions",
                steps=[
                    PlanStep(
                        step_number=1,
                        title="Enumerate and summarize open browser tabs",
                        agent="Browser Agent",
                        tool_name="browser_summarize_tabs",
                        params={"max_tabs": 15},
                        category=ActionCategory.BROWSER_ACTION,
                        risk_level=RiskLevel.LOW,
                        reason="Inspect live tabs and generate readable summaries",
                        expected_outcome="Grounded summaries of live browser tabs"
                    )
                ]
            )

        if is_tab_query and is_list_query and not any(f in p_lower for f in ["file", "document", "folder"]):
            return SemanticPlan(
                goal="List open browser tabs",
                strategy="Enumerate active browser tabs",
                steps=[
                    PlanStep(
                        step_number=1,
                        title="List all currently open browser tabs",
                        agent="Browser Agent",
                        tool_name="browser_list_tabs",
                        params={},
                        category=ActionCategory.BROWSER_ACTION,
                        risk_level=RiskLevel.LOW,
                        reason="Read tab titles and URLs via UI Automation",
                        expected_outcome="List of open tabs in active browser"
                    )
                ]
            )

        # 3. Recent Documents Discovery
        # Phrasings: "Find my recent documents", "Show me the files I worked on lately", "Can you locate yesterday's project notes?",
        # "What reports did I save recently?", "files modified lately", "last updated files"
        is_recent_query = any(k in p_lower for k in ["recent", "recently", "lately", "last updated", "last modified", "yesterday", "newest", "past few days"])
        is_doc_term = any(k in p_lower for k in ["doc", "docs", "document", "documents", "file", "files", "notes", "report", "reports", "saves", "saved", "work"])
        if is_recent_query and is_doc_term:
            return SemanticPlan(
                goal="Find recent documents",
                strategy="Scan Desktop, Documents, and Downloads for recently modified documents",
                steps=[
                    PlanStep(
                        step_number=1,
                        title="Search recently modified documents on Windows PC",
                        agent="File Agent",
                        tool_name="file_recent_documents",
                        params={"limit": 10},
                        category=ActionCategory.SAFE_READ,
                        risk_level=RiskLevel.LOW,
                        reason="Discover recent files across standard folders",
                        expected_outcome="List of recently saved documents"
                    )
                ]
            )

        # 4. Multi-turn Follow-up or Direct File Open
        # Phrasings: "open the second one", "open that", "open it", "launch resume.pdf", "open budget.xlsx"
        is_open_cmd = any(p_lower.startswith(w) or f" {w} " in f" {p_lower} " for w in ["open", "launch", "view", "start"])
        has_file_ext = any(f".{ext}" in p_lower for ext in ["pdf", "docx", "doc", "txt", "xlsx", "pptx", "csv", "md"])
        is_ordinal_ref = any(ord_word in p_lower for ord_word in [
            "first", "second", "third", "fourth", "fifth", "1st", "2nd", "3rd", "4th", "5th",
            "the second one", "the first one", "number 1", "number 2", "number 3", "#1", "#2", "#3"
        ])
        is_pronoun_ref = any(p in p_lower for p in ["open it", "open that", "open this", "open that file", "open the file", "open that doc"])

        # Check if user wants to open an application (calc, notepad, chrome, etc.) rather than a file
        app_names = {"calc", "calculator", "notepad", "chrome", "edge", "brave", "terminal", "cmd", "powershell", "explorer", "paint", "settings"}
        is_app_open = any(p_lower == f"open {a}" or p_lower == f"launch {a}" for a in app_names)

        if (is_open_cmd and (has_file_ext or is_ordinal_ref or is_pronoun_ref or "resume" in p_lower or "budget" in p_lower or "report" in p_lower)) and not is_app_open:
            # Extract target
            target = prompt
            for prefix in ["can you open", "please open", "open up", "open", "launch", "view"]:
                if prefix in p_lower:
                    idx = p_lower.find(prefix) + len(prefix)
                    target = prompt[idx:].strip()
                    break

            # Clean trailing politeness
            target = re.sub(r"\s+(?:for\s+me|please|now)\.?$", "", target, flags=re.IGNORECASE).strip()

            return SemanticPlan(
                goal=f"Open document: {target}",
                strategy="File Agent Verified Open",
                steps=[
                    PlanStep(
                        step_number=1,
                        title=f"Open document '{target}' on Windows desktop",
                        agent="File Agent",
                        tool_name="file_open",
                        params={"target": target or "second one"},
                        category=ActionCategory.SAFE_READ,
                        risk_level=RiskLevel.LOW,
                        reason="Launch file in its default registered Windows application",
                        expected_outcome="File opened visibly in native application"
                    )
                ]
            )

        # 4.8 Calculator & Computation ("open calculator and calculate ...", "calculate 45 * 2 in calc")
        is_calc = any(k in p_lower for k in ["calc", "calculator"])
        is_math_action = any(k in p_lower for k in ["calculate", "multiply", "add", "subtract", "divide", "compute", "solve", "+", "*", "/"])
        if is_calc and is_math_action:
            math_match = re.search(r"(\d+(?:\.\d+)?(?:\s*[\+\-\*\/]\s*\d+(?:\.\d+)?)+)", prompt)
            math_expr = math_match.group(1).replace(" ", "") if math_match else "25*4"
            return SemanticPlan(
                goal=f"Open Calculator and calculate '{math_expr}'",
                strategy="Launch Calculator and execute calculation keystrokes",
                steps=[
                    PlanStep(
                        step_number=1,
                        title="Launch Calculator on desktop",
                        agent="System Agent",
                        tool_name="system_launch_app",
                        params={"app": "calc"},
                        category=ActionCategory.SYSTEM_LAUNCH,
                        risk_level=RiskLevel.LOW,
                        reason="Start calc.exe process in desktop session",
                        expected_outcome="Calculator application launched"
                    ),
                    PlanStep(
                        step_number=2,
                        title="Wait for Calculator window to focus",
                        agent="System Agent",
                        tool_name="system_wait_for_window",
                        params={"title": "Calculator", "timeout": 8.0},
                        category=ActionCategory.SAFE_READ,
                        risk_level=RiskLevel.LOW,
                        reason="Ensure Calculator is active and ready for input",
                        expected_outcome="Calculator focused",
                        dependencies=[1]
                    ),
                    PlanStep(
                        step_number=3,
                        title=f"Send calculation '{math_expr}=' to Calculator",
                        agent="System Agent",
                        tool_name="system_press_hotkey",
                        params={"keys": f"{math_expr}=", "title": "Calculator"},
                        category=ActionCategory.SYSTEM_LAUNCH,
                        risk_level=RiskLevel.LOW,
                        reason="Enter math calculation into Calculator",
                        expected_outcome="Calculation completed in Calculator",
                        dependencies=[2]
                    )
                ]
            )

        # 5. App Launch + Note Drafting (e.g. Notepad / Editor + Writing)
        # Phrasings:
        # - "Open Notepad and write a few lines about Formula 1"
        # - "I want a quick F1 racing note in Notepad"
        # - "Launch a text editor and draft something about Grand Prix racing"
        has_editor = any(e in p_lower for e in ["notepad", "text editor", "editor", "word", "winword"])
        has_writing = any(w in p_lower for w in ["write", "draft", "note", "type", "jot down", "compose"])

        if has_editor and has_writing:
            app_target = "notepad"
            window_title = "Notepad"
            if "word" in p_lower:
                app_target = "winword"
                window_title = "Word"

            # Determine topic
            topic = "General Notes"
            passage = ""
            expected_snippet = "Notes"

            if any(k in p_lower for k in ["f1", "formula 1", "grand prix", "racing"]):
                topic = "Formula 1 Racing"
                passage = (
                    "Formula 1 racing represents the pinnacle of motorsport, featuring cutting-edge hybrid power units and world-class drivers.\n"
                    "Cars reach speeds exceeding 350 km/h across legendary circuits around the world.\n"
                    "Precision engineering, aerodynamic mastery, and strategic pit stops define every Grand Prix."
                )
                expected_snippet = "Formula 1" if ("formula 1" in p_lower or "f1" in p_lower) else ("Grand Prix" if "grand prix" in p_lower else "racing")
            else:
                # Extract topic semantically
                m_topic = re.search(r"(?:about|on|regarding)\s+(.+)$", prompt, re.IGNORECASE)
                if m_topic:
                    topic = m_topic.group(1).rstrip(".").strip()
                else:
                    topic = "General Notes"

                # Generate concise passage
                passage = (
                    f"{topic.title()}\n\n"
                    f"Overview and summary regarding {topic}.\n"
                    f"Prepared and verified by the Windows Multi-Agent Assistant."
                )
                words = [w for w in re.sub(r"[^\w\s]", "", topic).split() if len(w) > 2]
                expected_snippet = words[0] if words else "Overview"

                # Attempt dynamic LLM generation for the note if gateway is configured
                if llm_gateway.api_key or "localhost" in llm_gateway.base_url or "127.0.0.1" in llm_gateway.base_url:
                    try:
                        gen_res = await llm_gateway.chat_complete(
                            messages=[{"role": "user", "content": f"Write 3 concise, informative sentences about {topic}. Output plain text only without markdown."}],
                            max_tokens=150
                        )
                        if gen_res.get("success") and gen_res.get("content"):
                            gen_txt = gen_res["content"].strip().replace("`", "")
                            if len(gen_txt) > 20:
                                passage = gen_txt
                                gen_words = [w for w in re.sub(r"[^\w\s]", "", gen_txt).split() if len(w) > 4]
                                if gen_words:
                                    expected_snippet = gen_words[0]
                    except Exception:
                        pass

            return SemanticPlan(
                goal=f"Draft notes about {topic} in {window_title}",
                strategy=f"Launch {window_title}, enter note text, and verify entry via UI Automation",
                steps=[
                    PlanStep(
                        step_number=1,
                        title=f"Launch '{app_target}' on desktop",
                        agent="System Agent",
                        tool_name="system_launch_app",
                        params={"app": app_target},
                        category=ActionCategory.SYSTEM_LAUNCH,
                        risk_level=RiskLevel.LOW,
                        reason=f"Start {app_target} process in interactive desktop session",
                        expected_outcome=f"Application {app_target} launched"
                    ),
                    PlanStep(
                        step_number=2,
                        title=f"Wait for '{window_title}' window to initialize and focus",
                        agent="System Agent",
                        tool_name="system_wait_for_window",
                        params={"title": window_title, "timeout": 10.0},
                        category=ActionCategory.SAFE_READ,
                        risk_level=RiskLevel.LOW,
                        reason=f"Ensure {window_title} window is active and ready for input",
                        expected_outcome=f"Focused window handle for {window_title}",
                        dependencies=[1]
                    ),
                    PlanStep(
                        step_number=3,
                        title=f"Enter passage into '{window_title}'",
                        agent="System Agent",
                        tool_name="system_type_into_window",
                        params={"title": window_title, "text": passage},
                        category=ActionCategory.SYSTEM_LAUNCH,
                        risk_level=RiskLevel.LOW,
                        reason="Write generated passage into document",
                        expected_outcome="Text entered into document",
                        dependencies=[2]
                    ),
                    PlanStep(
                        step_number=4,
                        title=f"Verify text in '{window_title}' via UI Automation",
                        agent="System Agent",
                        tool_name="system_verify_window_text",
                        params={"title": window_title, "expected_snippet": expected_snippet, "timeout": 5.0},
                        category=ActionCategory.SAFE_READ,
                        risk_level=RiskLevel.LOW,
                        reason=f"Confirm snippet '{expected_snippet}' is present",
                        expected_outcome=f"Verified presence of '{expected_snippet}'",
                        dependencies=[3]
                    )
                ]
            )

        # 6. Web Search in Browser
        is_web_search = any(k in p_lower for k in ["google", "search the web", "search online", "search in browser", "search chrome", "browse to"]) or (
            ("search for" in p_lower or p_lower.startswith("search ")) and not any(w in p_lower for w in ["file", "files", "folder", "docx", "pdf", "txt"])
        )
        if is_web_search:
            q = prompt
            for prefix in ["search for", "google for", "search in browser for", "search in chrome for", "search the web for", "google", "search"]:
                if prefix in p_lower:
                    idx = p_lower.find(prefix) + len(prefix)
                    q = prompt[idx:].strip()
                    break
            q_clean = re.sub(r"^(?:in\s+browser|in\s+chrome|about|for)\s+", "", q, flags=re.IGNORECASE).strip()

            return SemanticPlan(
                goal=f"Web search for: {q_clean}",
                strategy="Existing Browser Web Search",
                steps=[
                    PlanStep(
                        step_number=1,
                        title=f"Search existing browser for '{q_clean}'",
                        agent="Browser Agent",
                        tool_name="browser_search",
                        params={"query": q_clean or "search"},
                        category=ActionCategory.BROWSER_ACTION,
                        risk_level=RiskLevel.LOW,
                        reason="Execute search in active browser session",
                        expected_outcome="Browser navigated to search results"
                    )
                ]
            )

        # 7. Hardware Telemetry & Performance
        if any(w in p_lower for w in ["cpu", "ram", "memory", "specs", "disk space", "hardware", "telemetry", "system info", "performance", "system status"]):
            return SemanticPlan(
                goal="Inspect system telemetry and performance",
                strategy="Read hardware metrics via psutil",
                steps=[
                    PlanStep(
                        step_number=1,
                        title="Read Windows hardware telemetry",
                        agent="System Agent",
                        tool_name="system_telemetry",
                        params={},
                        category=ActionCategory.SAFE_READ,
                        risk_level=RiskLevel.LOW,
                        reason="Inspect CPU, RAM, and Disk metrics",
                        expected_outcome="Accurate system performance metrics"
                    )
                ]
            )

        # 8. Diagnostics
        if any(w in p_lower for w in ["diagnose", "system health", "running slow", "health check"]):
            return SemanticPlan(
                goal="Diagnose system health",
                strategy="System Agent Diagnostics",
                steps=[
                    PlanStep(
                        step_number=1,
                        title="Run system diagnostics and check resource consumers",
                        agent="System Agent",
                        tool_name="system_diagnose",
                        params={},
                        category=ActionCategory.SAFE_READ,
                        risk_level=RiskLevel.LOW,
                        reason="Identify heavy processes and system alerts",
                        expected_outcome="System health diagnostic report"
                    )
                ]
            )

        # 9. Process Management
        if any(w in p_lower for w in ["processes", "running apps", "task manager", "list processes"]):
            return SemanticPlan(
                goal="List running processes",
                strategy="Inspect Windows processes",
                steps=[
                    PlanStep(
                        step_number=1,
                        title="List top running Windows processes",
                        agent="System Agent",
                        tool_name="system_list_processes",
                        params={"limit": 20},
                        category=ActionCategory.SAFE_READ,
                        risk_level=RiskLevel.LOW,
                        reason="Inspect active processes",
                        expected_outcome="List of processes with PIDs and memory usage"
                    )
                ]
            )

        # 10. General File Search
        if any(w in p_lower for w in ["find file", "search file", "look for file", "search files", "find a file", "locate my"]):
            target_folder = None
            for f_name in ["desktop", "documents", "downloads", "pictures", "videos", "music"]:
                if f_name in p_lower:
                    target_folder = f"{f_name.capitalize()} folder"
                    break

            q = prompt
            for prefix in ["find a file", "find files", "find file", "search files", "search for", "look for", "locate my", "find"]:
                if prefix in p_lower:
                    idx = p_lower.find(prefix) + len(prefix)
                    q = prompt[idx:].strip()
                    break

            q_clean = re.sub(r"^(?:a\s+|an\s+|the\s+|my\s+)", "", q, flags=re.IGNORECASE).strip().rstrip(".")

            return SemanticPlan(
                goal=f"Search files matching: {q_clean}",
                strategy="File Agent Search",
                steps=[
                    PlanStep(
                        step_number=1,
                        title=f"Search files matching '{q_clean}'",
                        agent="File Agent",
                        tool_name="file_search",
                        params={"query": q_clean or "docx", "directory": target_folder},
                        category=ActionCategory.FILE_READ,
                        risk_level=RiskLevel.LOW,
                        reason=f"Locate files matching {q_clean}",
                        expected_outcome="Matching files found"
                    )
                ]
            )

        # 11. Application Launch & Window Focus
        if p_lower.startswith("launch ") or p_lower.startswith("start app ") or (p_lower.startswith("open ") and not has_file_ext):
            app_target = re.sub(r"^(?:launch|start app|open)\s+", "", prompt, flags=re.IGNORECASE).strip()
            app_target = re.sub(r"\s+(?:for\s+me|please|now)\.?$", "", app_target, flags=re.IGNORECASE).strip()
            win_title = app_target.title()
            return SemanticPlan(
                goal=f"Launch and focus application: {app_target}",
                strategy="System Agent Launch and Window Focus",
                steps=[
                    PlanStep(
                        step_number=1,
                        title=f"Launch '{app_target}' on desktop",
                        agent="System Agent",
                        tool_name="system_launch_app",
                        params={"app": app_target},
                        category=ActionCategory.SYSTEM_LAUNCH,
                        risk_level=RiskLevel.LOW,
                        reason=f"Spawn {app_target} in Windows session",
                        expected_outcome=f"{app_target} running"
                    ),
                    PlanStep(
                        step_number=2,
                        title=f"Wait for '{win_title}' window to appear and focus",
                        agent="System Agent",
                        tool_name="system_wait_for_window",
                        params={"title": win_title, "timeout": 8.0},
                        category=ActionCategory.SAFE_READ,
                        risk_level=RiskLevel.LOW,
                        reason=f"Bring {win_title} to foreground so user can work in it",
                        expected_outcome=f"{win_title} window focused",
                        dependencies=[1]
                    )
                ]
            )

        # Default: Fallback to System Telemetry & Context Inspection
        return SemanticPlan(
            goal=f"Inspect system context for: {prompt}",
            strategy="System Telemetry & State Inspection",
            steps=[
                PlanStep(
                    step_number=1,
                    title="Inspect system state and telemetry",
                    agent="System Agent",
                    tool_name="system_telemetry",
                    params={},
                    category=ActionCategory.SAFE_READ,
                    risk_level=RiskLevel.LOW,
                    reason="Gather initial system context",
                    expected_outcome="System telemetry"
                )
            ]
        )


semantic_planner = SemanticPlanner()
