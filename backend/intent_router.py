import re
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple

class AgentIntent(str, Enum):
    CASUAL_CONVERSATION = "casual_conversation"
    ASSISTANT_IDENTITY = "assistant_identity"
    SCREEN_QUESTION = "screen_question"
    BROWSER_OPERATION = "browser_operation"
    FILE_OPERATION = "file_operation"
    SYSTEM_OPERATION = "system_operation"
    VOICE_OPERATION = "voice_operation"
    MULTI_AGENT_TASK = "multi_agent_task"

class IntentRouter:
    """
    Lightweight, deterministic, zero-latency Intent Classification layer.
    Routes user messages to either:
    - Direct conversational LLM (casual chat, identity, Q&A)
    - Specialized agents (Vision, Browser, File, System, Speech)
    - Orchestrator (complex multi-step tasks)
    """

    @classmethod
    def is_recent_documents_request(cls, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        recent_terms = ["recent", "recently", "lately", "last updated", "last modified", "yesterday", "newest", "past few days", "latest", "worked on lately", "saved lately", "last", "saved recently", "edited lately"]
        doc_terms = ["doc", "docs", "document", "documents", "file", "files", "note", "notes", "report", "reports", "save", "saves", "saved", "work"]
        has_recent = any(r in t for r in recent_terms)
        has_docs = any(d in t for d in doc_terms)
        if has_recent and has_docs:
            return True
        if re.search(r"\brecent(?:ly)?\s+(?:docs?|documents?|files?|saves?|saved|work)\b", t):
            return True
        if re.search(r"\b(?:find|search|show|get|list|display|check|locate)\s+(?:me\s+)?(?:the\s+|all\s+)?(?:my\s+)?(?:recent|last|latest)\b", t):
            return True
        if re.search(r"\b(?:docs?|documents?|files?)\s+(?:that\s+)?i\s+(?:have\s+)?(?:saved|worked\s+on|edited)\b", t):
            return True
        if re.search(r"\b(?:saved|created|updated|modified|edited)\s+(?:recently|lately)\b", t):
            return True
        if re.search(r"\b(?:files?|docs?|documents?)\s+(?:updated|modified|saved|created|edited)\s+(?:recently|lately)\b", t):
            return True
        if re.search(r"\b(?:last|latest)\s+(?:updated|modified|saved|created|edited)\s+(?:files?|docs?|documents?)\b", t):
            return True
        if re.search(r"\b(?:last|latest)\s+(?:files?|docs?|documents?)\b", t):
            return True
        if "recent doc" in t or "recent files" in t or "recent documents" in t:
            return True
        if "last updated" in t or "last modified" in t or "latest updated" in t or "latest modified" in t:
            return True
        return False

    @classmethod
    def is_browser_search_request(cls, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        is_file_query = any(w in t for w in [
            "file", "files", "folder", "directory", ".docx", ".pdf", ".txt", ".csv", ".xlsx", ".py", ".zip", ".pptx",
            "docx", "desktop", "downloads", "documents", "document"
        ])
        if is_file_query:
            return False

        if re.search(r"\bsearch\s+(?:in|on|using)?\s*(?:chrome|browser|google|edge|brave|web|internet)\s+(?:about|for)?\s*(.+)", t):
            return True
        if re.search(r"\bsearch\s+(?:about|for)?\s*(.+?)\s+(?:in|on|using)\s+(?:chrome|browser|google|edge|brave|web|internet)\b", t):
            return True
        if re.search(r"\bsearch\s+(.+?)\s+(?:in|on)\s+(?:chrome|browser|google|edge|brave)\b", t):
            return True
        if re.search(r"^(?:google|search\s+on\s+google|search\s+the\s+web\s+for|search\s+google\s+for)\s+.+$", t):
            return True
        if re.search(r"^search\s+(?:for|about)\s+.+$", t):
            return True
        if re.search(r"^search\s+(?:chrome|edge|brave|browser|google)\s+(?:for|about)\s+(.+)", t):
            return True
        return False

    @classmethod
    def extract_browser_search_query(cls, text: str) -> Optional[str]:
        if not text:
            return None
        t = text.strip()
        t_lower = t.lower()

        # 1. "search in chrome about ai", "search in browser for ai"
        m1 = re.search(r"\bsearch\s+(?:in|on|using)?\s*(?:chrome|browser|google|edge|brave|web|internet)\s+(?:about|for)?\s*(.+)$", t, flags=re.IGNORECASE)
        if m1:
            q = m1.group(1).strip()
            q = re.sub(r"\s+(?:please|for\s+me|now)\.?$", "", q, flags=re.IGNORECASE).strip()
            if q:
                return q

        # 2. "search about ai in browser", "search for ai in chrome"
        m2 = re.search(r"\bsearch\s+(?:about|for)?\s*(.+?)\s+(?:in|on|using)\s+(?:chrome|browser|google|edge|brave|web|internet)\b", t, flags=re.IGNORECASE)
        if m2:
            q = m2.group(1).strip()
            q = re.sub(r"\s+(?:please|for\s+me|now)\.?$", "", q, flags=re.IGNORECASE).strip()
            if q:
                return q

        # 3. "search ai in chrome"
        m3 = re.search(r"\bsearch\s+(.+?)\s+(?:in|on)\s+(?:chrome|browser|google|edge|brave)\b", t, flags=re.IGNORECASE)
        if m3:
            q = m3.group(1).strip()
            q = re.sub(r"\s+(?:please|for\s+me|now)\.?$", "", q, flags=re.IGNORECASE).strip()
            if q:
                return q

        # 4. "search the web for ai", "search on google for ai"
        m4 = re.search(r"^(?:google|search\s+on\s+google|search\s+the\s+web\s+for|search\s+google\s+for)\s+(.+)$", t, flags=re.IGNORECASE)
        if m4:
            q = m4.group(1).strip()
            q = re.sub(r"\s+(?:please|for\s+me|now)\.?$", "", q, flags=re.IGNORECASE).strip()
            if q:
                return q

        # 5. "search for ai", "search about ai"
        m5 = re.search(r"^search\s+(?:for|about)\s+(.+)$", t, flags=re.IGNORECASE)
        if m5:
            q = m5.group(1).strip()
            q = re.sub(r"\s+(?:please|for\s+me|now)\.?$", "", q, flags=re.IGNORECASE).strip()
            if q:
                return q

        # 6. Fallback stripping common prefixes
        stripped = t
        for p in ["search in browser", "search browser", "search in chrome", "search chrome", "search on google", "search google", "search the web for", "search for", "search about", "google"]:
            if t_lower.startswith(p):
                stripped = t[len(p):].strip()
                break

        stripped = re.sub(r"^(?:for|about|in|on)\s+", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"\s+(?:in|on|using)\s+(?:chrome|browser|google|edge|brave)\.?$", "", stripped, flags=re.IGNORECASE).strip()
        return stripped if stripped else None

    @classmethod
    def is_summarize_tabs_request(cls, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        has_tab = any(k in t for k in ["tab", "tabs", "browser"])
        has_summary = any(k in t for k in ["summar", "overview", "brief", "what is on each", "what's on each", "short summary", "give me a summary", "go through", "roundup", "digest", "everything open in my browser", "everything open in browser"])
        if has_tab and has_summary:
            return True
        if re.search(r"\b(?:summarize|summary\s+of)\s+(?:all\s+|the\s+|my\s+)?(?:open\s+)?(?:browser\s+)?tabs?\b", t):
            return True
        if "summary of each tab" in t or "summarize each tab" in t or "summary of tabs" in t:
            return True
        if re.search(r"\bgo\s+through\s+(?:all\s+)?(?:the\s+)?(?:open\s+)?tabs\b", t):
            return True
        if "overview of everything open in my browser" in t or "overview of my browser" in t:
            return True
        return False

    @classmethod
    def is_list_tabs_request(cls, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        has_tab = any(k in t for k in ["tab", "tabs"])
        has_list = any(k in t for k in ["list", "show", "what", "which", "display", "check", "enumerate", "how many", "inspect"])
        if has_tab and has_list and any(b in t for b in ["open", "browser", "chrome", "edge", "brave", "my"]):
            return True
        if re.search(r"\b(?:list|show|get|display|check|enumerate)\s+(?:all\s+|the\s+|my\s+)?(?:open\s+)?(?:browser\s+)?tabs?\b", t):
            return True
        if "what tabs are open" in t or "what tabs do i have open" in t or "what's open in my browser" in t:
            return True
        return False

    @classmethod
    def is_browser_tabs_request(cls, text: str) -> bool:
        return cls.is_summarize_tabs_request(text) or cls.is_list_tabs_request(text)

    @classmethod
    def is_app_control_and_typing_task(cls, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        # Single app name alone is NOT an app control and typing task
        if t in ["notepad", "calc", "calculator", "word", "winword", "chrome", "edge", "brave"]:
            return False
        # If it is a process kill command, it is not a typing task
        if any(t.startswith(w) for w in ["kill ", "terminate ", "stop "]):
            return False

        has_editor = any(k in t for k in ["notepad", "text editor", "editor", "word", "winword", "calc", "calculator"])
        # Do not match 'note' inside the word 'notepad'
        t_without_editor = re.sub(r"\b(?:notepad|calc|calculator|word|winword)\b", "", t)
        has_typing = any(re.search(rf"\b{re.escape(w)}\b", t_without_editor) for w in [
            "write", "type", "enter", "note", "notes", "draft", "jot down", "put down", "input", "compose",
            "f1", "racing", "grand prix", "formula 1", "pes", "pes university"
        ])
        if has_editor and has_typing:
            return True
        has_open_app = any(k in t for k in ["open notepad", "launch notepad", "start notepad", "open text editor", "launch text editor", "open word", "open editor", "open calc"])
        if has_open_app and has_typing:
            return True
        if re.search(r"\b(?:open|launch|start)\s+(?:notepad|text editor|editor|word)\b.*?\b(?:write|type|enter|input|draft|note)\b", t):
            return True
        return False

    @classmethod
    def is_document_generation_request(cls, text: str) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        gen_verbs = ["generate", "create", "make", "build", "produce", "export", "draft", "write"]
        doc_terms = [
            "pdf", "docx", "doc", "word", "excel", "xlsx", "presentation", "pptx", "powerpoint",
            "slides", "spreadsheet", "report", "document", "documents", "file like pdf",
            "files like pdf", "multiple files", "multiple documents"
        ]
        has_verb = any(v in t for v in gen_verbs)
        has_doc = any(d in t for d in doc_terms)
        if has_verb and has_doc:
            return True
        if re.search(r"\b(?:generate|create|make|export|produce|draft|write)\s+(?:a\s+|an\s+|the\s+|multiple\s+)?(?:pdf|docx?|word|pptx?|powerpoint|presentation|excel|xlsx?|report|document|files?)\b", t):
            return True
        if re.search(r"\b(?:save\s+(?:it\s+)?(?:to|in|on)\s+[a-zA-Z]:|\b(?:save\s+(?:it\s+)?(?:to|in|on)\s+(?:[a-zA-Z]\s+drive|desktop|documents|downloads)))\b", t) and any(d in t for d in ["pdf", "docx", "file", "doc", "presentation", "report"]):
            return True
        return False

    @classmethod
    def is_open_file_request(cls, text: str, chat_history: Optional[List[Dict[str, str]]] = None) -> bool:
        if not text:
            return False
        t = text.lower().strip()
        # Clean leading politeness ("can you open...", "please open...", "could you open...")
        t_clean = re.sub(r"^(?:can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?|please\s+|kindly\s+|would\s+you\s+(?:mind\s+)?|i\s+want\s+to\s+|i\s+need\s+to\s+)", "", t, flags=re.IGNORECASE).strip()
        t_clean = re.sub(r"\s+(?:for\s+me|please|up|now)\.?$", "", t_clean, flags=re.IGNORECASE).strip()

        # 1. Pronouns and direct referents ("open it", "open that", "open that file", "open that pdf", "open the file", "open the pdf", "open this")
        if re.search(r"^(?:open|launch|start|view|show)\s+(?:it|that|this|that\s+(?:file|doc|document|pdf|docx?|txt|xlsx?|pptx?|csv)|this\s+(?:file|doc|document|pdf|docx?|txt|xlsx?|pptx?|csv)|the\s+(?:file|doc|document|pdf|docx?|txt|xlsx?|pptx?|csv)|the\s+file\s+you\s+found)\.?$", t_clean):
            return True

        # 2. Direct "open docs", "open the document", "open file", "open the second one", "open the 2nd one", "open 2"
        if re.search(r"^open\s+(?:the\s+)?(?:docs?|documents?|files?|recent\s+docs?)\.?$", t_clean):
            return True
        if re.search(r"^open\s+(?:the\s+)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th|\d+)(?:\s+one)?(?:\s+doc|\s+file|\s+document)?\.?$", t_clean):
            return True
        if re.search(r"^open\s+(?:number\s+|#)?\d+$", t_clean):
            return True

        # Pure system applications / browsers should NOT be treated as file open requests
        system_apps = {
            "calc", "calculator", "notepad", "explorer", "terminal", "cmd",
            "powershell", "paint", "taskmgr", "task manager", "settings",
            "chrome", "edge", "brave", "firefox", "browser"
        }
        app_match = re.search(r"^(?:open|launch|start|run)\s+(?:app\s+|program\s+)?([a-zA-Z0-9_\-\s]+)$", t_clean)
        if app_match and app_match.group(1).strip() in system_apps:
            return False

        # 3. Explicit filenames with extension: "open resume.pdf", "open draft.docx"
        if re.search(r"^open\s+.+\.(?:docx?|pdf|txt|md|rtf|xlsx?|pptx?|csv|json|py|bat|ps1)\.?$", t_clean):
            return True

        # 4. Natural language requests with document extension: "open my resume PDF", "open the budget xlsx"
        if re.search(r"^(?:open|launch|start|view)\s+(?:the\s+|my\s+)?[a-zA-Z0-9_\-\s]+\s+(?:pdf|docx?|txt|xlsx?|pptx?|csv)\.?$", t_clean):
            return True

        # 5. Document-oriented names: "open my resume", "open project summary", "open draft"
        if re.search(r"^(?:open|launch|view)\s+(?:the\s+|my\s+)?(?:resume|cv|project\s+summary|draft|notes|summary|document|file)", t_clean):
            return True
        if t_clean.startswith("open ") and any(ext in t_clean for ext in [".docx", ".doc", ".pdf", ".txt", ".md", ".xlsx", ".pptx"]):
            return True

        # 6. Multi-turn follow-up check: If prior assistant turn asked which doc to open, listed documents, or returned search results
        if chat_history:
            prev_assistant = ""
            for m in reversed(chat_history):
                if m.get("role") == "assistant" and not prev_assistant:
                    prev_assistant = m.get("content", "").lower()
                    break

            if prev_assistant:
                is_open_prompt = any(k in prev_assistant for k in [
                    "which doc", "which document", "which file", "would you like me to open",
                    "would you like to open", "recent saves", "recent documents",
                    "multiple matching files", "choose between", "found a few files",
                    "matching", "found", "recent document", "saved on your pc"
                ])
                # Ensure it's not a folder selection for search like "Go for Desktop folder"
                is_folder_reply = any(ft in t for ft in ["desktop", "documents", "downloads", "folder", "directory", "entire system", "whole pc", "scan"])
                if is_open_prompt and not is_folder_reply:
                    # Exclude browser, screen, or system commands from multi-turn file opening
                    if any(w in t_clean for w in ["tab", "tabs", "browser", "chrome", "edge", "brave", "screen", "cpu", "ram", "processes", "kill ", "powershell", "terminal", "weather"]):
                        return False
                    # Check bare ordinal responses like "second one", "the second one", "2nd one", "2", "first one", "1"
                    if re.search(r"^(?:open\s+)?(?:the\s+)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th|\d+)(?:\s+one)?(?:\s+doc|\s+file|\s+document)?\.?$", t_clean):
                        return True
                    if t_clean in ["it", "that", "that one", "this one", "the file", "the doc", "the document", "the file you found", "that pdf", "the pdf", "this pdf", "that docx", "the docx", "this docx"]:
                        return True
                    if any(ext in t_clean for ext in [".pdf", ".docx", ".doc", ".txt", ".xlsx", ".pptx", ".csv"]):
                        return True
                    clean_choice = re.sub(r"[^\w\s\.-]", "", t_clean).strip()
                    norm_prev = re.sub(r"[_\-\.]+", " ", prev_assistant.lower())
                    norm_choice = re.sub(r"[_\-\.]+", " ", clean_choice.lower())
                    if clean_choice and len(clean_choice.split()) <= 4:
                        if clean_choice in prev_assistant or clean_choice.replace(" ", "_") in prev_assistant or norm_choice in norm_prev:
                            return True
                        tokens = [tok for tok in norm_choice.split() if len(tok) >= 3 and tok not in ["the", "doc", "file"]]
                        if tokens and all(tok in norm_prev for tok in tokens):
                            return True

        return False

    @classmethod
    def extract_open_file_target(cls, text: str, chat_history: Optional[List[Dict[str, str]]] = None) -> Optional[str]:
        if not text:
            return None
        t = text.strip()
        # Clean leading polite prefixes
        cleaned = re.sub(r"^(?:can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?|please\s+|kindly\s+|would\s+you\s+(?:mind\s+)?|i\s+want\s+to\s+|i\s+need\s+to\s+)", "", t, flags=re.IGNORECASE).strip()
        # Clean trailing politeness
        cleaned = re.sub(r"\s+(?:for\s+me|please|up|now)\.?$", "", cleaned, flags=re.IGNORECASE).strip()

        if re.search(r"^open\s+(?:the\s+)?(?:docs?|documents?|files?|recent\s+docs?)\.?$", cleaned, re.IGNORECASE):
            return ""

        cleaned = re.sub(r"^(?:open|launch|start|show|view)\s+(?:the\s+|my\s+)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^(?:the\s+|my\s+)", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = cleaned.rstrip(".").strip()

        # Pronoun & demonstrative targets with optional extension (e.g. "it", "that", "that pdf", "the pdf", "this docx")
        pronouns = {
            "it", "that", "this", "the file", "this file", "that file", "the document", "the doc",
            "the pdf", "it up", "it please", "the file you found", "file you found", "that one",
            "this one", "that pdf", "this pdf", "that docx", "this docx", "the docx", "that doc",
            "this doc", "the pptx", "that pptx", "that.pdf", "this.pdf", "the.pdf", "that.docx",
            "this.docx", ".pdf", ".docx", ".pptx", ".txt"
        }
        pronoun_match = re.search(r"^(?:it|that|this|that\s+one|this\s+one|the\s+one)(?:\s+(?:file|doc|document|pdf|docx?|txt|xlsx?|pptx?|csv))?$", cleaned.lower())
        the_match = re.search(r"^(?:the|that|this)\s+(?:file|doc|document|pdf|docx?|txt|xlsx?|pptx?|csv)$", cleaned.lower())
        dot_match = re.search(r"^(?:that|this|the)?\.?(?:pdf|docx?|txt|xlsx?|pptx?|csv)$", cleaned.lower())
        is_pronoun_target = bool(pronoun_match or the_match or dot_match or cleaned.lower() in pronouns)

        if is_pronoun_target:
            constraint_ext = None
            for ext in [".pdf", ".docx", ".doc", ".txt", ".xlsx", ".pptx", ".csv"]:
                if ext.lstrip(".") in cleaned.lower():
                    constraint_ext = ext
                    break

            if chat_history:
                for m in reversed(chat_history):
                    content = m.get("content", "")
                    # Look for explicit Windows paths (e.g. C:\... or D:\...)
                    path_matches = re.findall(r"([a-zA-Z]:[\\/][^\s:*?\"<>|\r\n]+?\.[a-zA-Z0-9_\-]+)", content)
                    if path_matches:
                        if constraint_ext:
                            for p in path_matches:
                                clean_p = p.rstrip("`").rstrip(")").rstrip(".")
                                if clean_p.lower().endswith(constraint_ext):
                                    return clean_p
                        else:
                            return path_matches[0].rstrip("`").rstrip(")").rstrip(".")

                    # Look for markdown backtick filenames (e.g. `Resume.pdf`)
                    backtick_matches = re.findall(r"`([^`]+?\.[a-zA-Z0-9_\-]+)`", content)
                    if backtick_matches:
                        if constraint_ext:
                            for b in backtick_matches:
                                if b.lower().endswith(constraint_ext):
                                    return b.strip()
                        else:
                            return backtick_matches[0].strip()

                    # Look for numbered lines with filenames e.g. "• 1. Resume.pdf"
                    numbered_matches = re.findall(r"(?:•|\*|-)?\s*(?:\*\*)?\d+\.(?:\*\*)?\s*`?([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9_\-]+)`?", content)
                    if numbered_matches:
                        if constraint_ext:
                            for nm in numbered_matches:
                                clean_nm = nm.strip().strip("`")
                                if clean_nm.lower().endswith(constraint_ext):
                                    return clean_nm
                        else:
                            return numbered_matches[0].strip().strip("`")

            return constraint_ext if constraint_ext else "it"

        # Check if user spoke like "resume PDF" -> normalize to "resume.pdf"
        ext_suffix_match = re.search(r"^(.+?)\s+(pdf|docx?|txt|xlsx?|pptx?|csv|json)$", cleaned, re.IGNORECASE)
        normalized_stem = f"{ext_suffix_match.group(1).strip()}.{ext_suffix_match.group(2).lower()}" if ext_suffix_match else cleaned

        # Also check if chat history has a matching file path for tokens in cleaned
        if chat_history:
            tokens = [tok for tok in re.findall(r"[a-zA-Z0-9]+", cleaned.lower()) if tok not in ["the", "my", "open", "file", "doc", "document"]]
            req_ext = None
            for ext in [".pdf", ".docx", ".doc", ".txt", ".xlsx", ".pptx", ".csv"]:
                if ext.lstrip(".") in cleaned.lower():
                    req_ext = ext
                    break

            for m in reversed(chat_history):
                content = m.get("content", "")
                path_matches = re.findall(r"([a-zA-Z]:[\\/][^\s:*?\"<>|\r\n]+?\.[a-zA-Z0-9_\-]+)", content)
                for p in path_matches:
                    clean_p = p.rstrip("`").rstrip(")").rstrip(".")
                    if req_ext and not clean_p.lower().endswith(req_ext):
                        continue
                    norm_p = re.sub(r"[_\-\s\.]+", " ", clean_p.lower())
                    if tokens and all(tok in norm_p for tok in tokens):
                        return clean_p

        return normalized_stem if normalized_stem else None

    @classmethod
    def classify(cls, user_msg: str, chat_history: Optional[List[Dict[str, str]]] = None) -> AgentIntent:
        if not user_msg:
            return AgentIntent.CASUAL_CONVERSATION

        msg_lower = user_msg.lower().strip()

        # Check Recent Documents or Open File intent directly
        if cls.is_recent_documents_request(user_msg):
            return AgentIntent.FILE_OPERATION

        if cls.is_open_file_request(user_msg, chat_history):
            return AgentIntent.FILE_OPERATION

        # 1. Voice / Speech Controls
        voice_keywords = [
            "mute voice", "unmute voice", "mute tts", "unmute tts",
            "stop talking", "shut up", "be quiet", "silence voice",
            "change voice", "set voice", "voice speed", "speak faster", "speak slower"
        ]
        if any(vk in msg_lower for vk in voice_keywords):
            return AgentIntent.VOICE_OPERATION

        # 2. Screen / Visual Questions (Requires Vision Agent screenshot & element inspection)
        screen_keywords = [
            "what is on my screen", "what's on my screen", "look at my screen",
            "look at screen", "analyze my screen", "analyze screen", "describe my screen",
            "describe screen", "what do you see on my screen", "what's open on my screen",
            "look at what i have open", "inspect active window", "read current window"
        ]
        if any(sk in msg_lower for sk in screen_keywords):
            return AgentIntent.SCREEN_QUESTION

        # 3. Explicit Multi-Agent Tasks & Document Generation
        if msg_lower.startswith("task:") or msg_lower.startswith("plan:") or "run autonomous task" in msg_lower:
            return AgentIntent.MULTI_AGENT_TASK

        if cls.is_document_generation_request(user_msg):
            return AgentIntent.MULTI_AGENT_TASK

        if cls.is_app_control_and_typing_task(user_msg):
            return AgentIntent.MULTI_AGENT_TASK

        # Calculator and desktop computation tasks (e.g. "calculate 45*20", "what is 50*12 in calculator")
        if any(w in msg_lower for w in ["calculate", "compute", "calculator"]) and any(c in msg_lower for c in ["+", "-", "*", "/", "=", "x"]):
            return AgentIntent.MULTI_AGENT_TASK

        # Imperative user requests asking to perform desktop work (e.g. "i need you to generate...", "please create...")
        if re.search(r"^(?:i\s+need\s+you\s+to|please|can\s+you(?:\s+please)?|could\s+you(?:\s+please)?|i\s+want\s+you\s+to)\s+(?:generate|create|make|open|run|calculate|build|write|save|type|search|inspect|analyze)\b", msg_lower):
            return AgentIntent.MULTI_AGENT_TASK

        # Multi-step conjunction indicator: e.g. "open chrome AND search for X AND save to file"
        if (" and " in msg_lower or " then " in msg_lower) and any(w in msg_lower for w in ["open", "search", "save", "write", "kill", "copy", "generate", "create", "make", "type"]):
            action_verbs = sum(1 for verb in [
                "open", "search", "navigate", "read", "write", "kill", "launch", "download",
                "generate", "create", "make", "save", "calculate", "type", "run", "start"
            ] if f" {verb} " in f" {msg_lower} " or msg_lower.startswith(f"{verb} ") or f" {verb} " in f" {re.sub(r'^(?:i\s+need\s+you\s+to|please|can\s+you|could\s+you)\s+', '', msg_lower)} ")
            if action_verbs >= 2:
                return AgentIntent.MULTI_AGENT_TASK

        # Multi-turn Context Follow-Up: If prior turn was a file search query/prompt and user responds with a folder
        if chat_history:
            prev_assistant = ""
            prev_user = ""
            for m in reversed(chat_history):
                role = m.get("role")
                content = m.get("content", "").lower()
                if role == "assistant" and not prev_assistant:
                    prev_assistant = content
                elif role == "user" and not prev_user and content != msg_lower:
                    prev_user = content

            is_prev_file_context = False
            if prev_assistant and any(k in prev_assistant for k in [
                "search your pc", "scan your entire system", "focus on specific folders",
                "which folder", "search files", "scan specific", "search for a", "search for .", "scan your pc"
            ]):
                is_prev_file_context = True
            elif prev_user and any(k in prev_user for k in ["search for", "find", "look for", "locate"]) and any(f in prev_user for f in ["file", "files", "doc", "docx", "pdf", "txt", "xlsx"]):
                is_prev_file_context = True

            folder_triggers = ["desktop", "documents", "downloads", "pictures", "videos", "music", "folder", "directory", "dir", "entire system", "whole pc", "entire pc", "c:\\", "d:\\", "c:/", "d:/"]
            if is_prev_file_context and any(ft in msg_lower for ft in folder_triggers):
                return AgentIntent.FILE_OPERATION

        # 4. Browser / App Launch Shortcuts
        if msg_lower in ["chrome", "google chrome", "edge", "microsoft edge", "brave", "browser"]:
            return AgentIntent.BROWSER_OPERATION

        if re.search(r"^(?:open|launch|start|run)\s+(?:app\s+|program\s+)?(chrome|google\s+chrome|edge|brave|browser)$", msg_lower):
            return AgentIntent.BROWSER_OPERATION

        common_single_system_apps = {
            "notepad", "calc", "calculator", "terminal", "cmd",
            "powershell", "paint", "taskmgr", "task manager", "settings"
        }
        if msg_lower in common_single_system_apps:
            return AgentIntent.SYSTEM_OPERATION

        # 5. System Operations (Telemetry, Processes, App Launch, PowerShell)
        sys_telemetry = [
            "cpu usage", "ram usage", "memory usage", "check cpu", "check ram",
            "how much ram", "how much memory", "cpu percent", "system telemetry",
            "hardware specs", "specs of my pc", "computer specs", "system info", "system stats",
            "performance", "system performance", "pc performance", "computer performance",
            "system health", "system status", "how is my pc", "how is my system", "machine performance"
        ]
        sys_proc = [
            "running processes", "running apps", "active processes", "show processes",
            "list processes", "task manager", "taskmanager", "what is taking memory",
            "what is eating my ram", "high memory processes"
        ]
        if any(st in msg_lower for st in sys_telemetry) or any(sp in msg_lower for sp in sys_proc):
            return AgentIntent.SYSTEM_OPERATION

        if re.search(r"^(?:kill|terminate|stop|end)\s+(?:process\s+|task\s+|app\s+)?[a-zA-Z0-9_\-\.]+(?:\.exe)?$", msg_lower):
            return AgentIntent.SYSTEM_OPERATION

        if re.search(r"^(?:open|launch|start|run)\s+(?:app\s+|program\s+)?(calc|calculator|notepad|explorer|terminal|cmd|powershell|paint|taskmgr|settings)$", msg_lower):
            return AgentIntent.SYSTEM_OPERATION

        if msg_lower.startswith("powershell ") or msg_lower.startswith("cmd ") or msg_lower.startswith("run command "):
            return AgentIntent.SYSTEM_OPERATION

        # Check if query is file-related before checking browser search heuristics
        is_file_query = any(w in msg_lower for w in [
            "file", "files", "folder", "directory", ".docx", ".pdf", ".txt", ".csv", ".xlsx", ".py", ".zip", ".pptx",
            "docx", "desktop", "downloads", "documents"
        ])

        # 5. Browser Operations
        if cls.is_browser_tabs_request(user_msg):
            return AgentIntent.BROWSER_OPERATION

        if cls.is_browser_search_request(user_msg):
            return AgentIntent.BROWSER_OPERATION

        browser_keywords = [
            "open browser", "open chrome", "open edge", "open brave",
            "close tab", "new tab", "switch tab", "next tab", "prev tab",
            "scroll down", "scroll up", "open tabs", "all open tabs",
            "browser tabs", "tabs in my browser", "tabs in chrome",
            "tabs in edge", "tabs in brave", "summarize open tabs",
            "summarize tabs", "summarize all tabs", "summary of each tab",
            "list open tabs", "list tabs", "show open tabs", "what tabs are open",
            "what tabs do i have open", "go through all the open tabs", "go through open tabs",
            "go through tabs"
        ]
        if any(bk in msg_lower for bk in browser_keywords):
            return AgentIntent.BROWSER_OPERATION

        if re.search(r"^(?:navigate\s+to|go\s+to|open\s+url|browse\s+to)\s+\S+$", msg_lower) or msg_lower.startswith("https://") or msg_lower.startswith("http://"):
            return AgentIntent.BROWSER_OPERATION

        if re.search(r"^(?:google|search\s+on\s+google|search\s+the\s+web\s+for)\s+.+$", msg_lower):
            return AgentIntent.BROWSER_OPERATION

        if re.search(r"^search\s+for\s+.+$", msg_lower) and not is_file_query:
            return AgentIntent.BROWSER_OPERATION

        # 6. File Operations
        file_search_patterns = [
            r"(?:search|find|look|scan|locate)\s+(?:my\s+|the\s+)?(?:pc\s+|computer\s+|system\s+)?(?:for\s+)?(?:a\s+|an\s+|my\s+|the\s+)?(?:[a-zA-Z0-9_\-\.\*]+\s+)?(?:file|files|doc|document|docx|pdf|txt|csv|xlsx)",
            r"(?:search|find|scan)\s+(?:in\s+|on\s+)?(?:the\s+)?(?:desktop|documents|downloads|folder|directory)",
            r"^search\s+(?:for\s+)?\.?[a-zA-Z0-9_\-]+\s+files?$",
            r"search\s+for\s+(?:a\s+|an\s+)?(?:docx|\.docx)",
            r"need\s+to\s+search\s+for",
        ]
        if any(re.search(pat, msg_lower) for pat in file_search_patterns):
            return AgentIntent.FILE_OPERATION

        file_verbs = ["read file", "view file", "inspect file", "cat ", "show file", "write file", "edit file", "delete file", "find file", "search files"]
        if any(fv in msg_lower for fv in file_verbs):
            return AgentIntent.FILE_OPERATION

        if re.search(r"^(?:read|view|show|cat)\s+(?:file\s+)?(['\"]?)[a-zA-Z0-9_\-\.\/\\]+\.[a-zA-Z0-9_\-]+\1$", msg_lower):
            return AgentIntent.FILE_OPERATION

        # 7. Assistant Identity / Capabilities Questions
        identity_keywords = [
            "who are you", "what are you", "what can you do", "what are your capabilities",
            "what can you help me with", "introduce yourself", "tell me about yourself",
            "what is your name", "what are you capable of"
        ]
        clean_prompt = re.sub(r"[^\w\s]", "", msg_lower).strip()
        if clean_prompt in identity_keywords or any(ik == clean_prompt for ik in identity_keywords):
            return AgentIntent.ASSISTANT_IDENTITY

        # 8. Everything else defaults to Natural Casual Conversation
        return AgentIntent.CASUAL_CONVERSATION

    @classmethod
    def get_conversation_system_prompt(cls, is_identity_query: bool = False) -> str:
        """
        Tailored system instructions ensuring concise, natural, human-like dialogue.
        Prevents action hallucinations and repetitive template dumps.
        """
        if is_identity_query:
            return (
                "You are OS Agent, a modern multimodal multi-agent assistant for Windows. "
                "You operate the user's actual Windows computer through 4 specialized local agents:\n"
                "- 🌐 Browser Agent: Direct control of existing Chrome/Edge/Brave windows without Playwright.\n"
                "- 📁 File Agent: Safe local file inspection, editing with diffs, and verified opening.\n"
                "- ⚙️ System Agent: Hardware telemetry, process management, app launching, and safe PowerShell.\n"
                "- 👁️ Vision Agent: Real-time screen understanding and UI element localization.\n\n"
                "Explain your identity concisely and warmly in 2-3 sentences. "
                "Do NOT include JSON action blocks. Do NOT claim you performed any actions."
            )

        return (
            "You are OS Agent, a friendly, intelligent Windows desktop companion.\n\n"
            "Guidelines for natural conversation:\n"
            "1. Be natural, warm, and conversational without forcing slang, emojis, or excessive enthusiasm.\n"
            "2. For greetings ('hey', 'hello', 'hi bro', 'good morning'), respond with a single, natural, friendly sentence (e.g. 'Hey there! How can I help you today?').\n"
            "3. Do NOT dump generic introductory lists, welcome menus, or capabilities unless the user specifically asks who you are.\n"
            "4. Acknowledge previous context from the conversation history; do NOT repeat greeting introductions if you are already in conversation.\n"
            "5. Never output action JSON or ```action ... ``` code blocks during casual conversation.\n"
            "6. Never claim an operation was performed on the computer unless you actually executed it.\n"
            "7. Never claim you cannot access the filesystem or tell the user to use Windows Search. A working local File Agent exists to inspect, search, and open files on this computer.\n"
            "8. Never state or imply false desktop limitations such as 'I can't actually interact with the desktop beyond search' or 'I cannot open files'. The local File Agent and Windows execution toolkit can open files and launch applications directly on the user's desktop.\n"
            "9. Never state 'I don't have access to your live browser tabs' or ask the user to share links or paste content when browser inspection is requested. The local Browser Agent inspects and summarizes live open tabs in Chrome, Edge, and Brave."
        )

    @classmethod
    def extract_file_search_params(cls, user_msg: str, chat_history: Optional[List[Dict[str, str]]] = None) -> Tuple[Optional[str], Optional[str]]:
        """
        Extracts (query, folder) from current message and conversation history.
        Handles both specific document queries (e.g. 'Find my resume PDF', 'find resume.pdf')
        and extension searches (e.g. 'I need to search for a docx file.', 'search for docx').
        """
        msg_lower = user_msg.lower().strip()
        folder_candidates = ["desktop", "documents", "downloads", "pictures", "videos", "music", "workspace", "project"]

        # 1. Check if current message specifies folder
        detected_folder = None
        for fc in folder_candidates:
            if fc in msg_lower:
                detected_folder = f"{fc.capitalize()} folder"
                break

        # Also check path patterns like C:\... or D:\...
        path_match = re.search(r"([a-zA-Z]:[\\/][^:*?\"<>|\r\n]+)", user_msg)
        if path_match:
            detected_folder = path_match.group(1).strip()

        # 2. Check if current message specifies query / extension
        detected_query = None

        # A. Check for specific named document request: "Find my resume PDF", "Find resume.pdf", "find test.py"
        stripped = re.sub(r"^(?:search\s+for|find|locate|look\s+for|scan\s+for|search)\s+(?:me\s+)?(?:a\s+|an\s+|my\s+|the\s+)?", "", user_msg, flags=re.IGNORECASE).strip()
        stripped = stripped.rstrip(".").strip()
        # Clean folder reference from stripped text if present
        for fc in folder_candidates:
            stripped = re.sub(rf"\s+(?:in|on|under|inside)\s+(?:the\s+)?{fc}(?:\s+folder)?", "", stripped, flags=re.IGNORECASE).strip()

        # Check if stripped matches "resume.pdf" or "test.py"
        if re.search(r"^([a-zA-Z0-9_\-\s]+)\.([a-zA-Z0-9_\-]+)$", stripped):
            detected_query = stripped
        else:
            # Check for space-separated extension like "resume PDF" or "resume docx"
            words = stripped.split()
            if len(words) >= 2 and words[-1].lower() in ["pdf", "docx", "doc", "txt", "xlsx", "xls", "pptx", "ppt", "csv", "json"]:
                stem = " ".join(words[:-1]).strip()
                if stem.lower() not in ["a", "an", "the", "my", "any", "all"]:
                    detected_query = f"{stem}.{words[-1].lower()}"

        # B. Check for standard extension match: "search for a docx file", "search for *.pdf", etc.
        if not detected_query:
            ext_match = re.search(r"(?:for\s+)?(?:\*\.)?([a-zA-Z0-9_\-]+)\s+files?", msg_lower)
            if ext_match:
                detected_query = ext_match.group(1)
            else:
                ext_dot = re.search(r"\.([a-zA-Z0-9_\-]+)", msg_lower)
                if ext_dot:
                    detected_query = ext_dot.group(1)
                elif any(k in msg_lower for k in ["docx", "pdf", "pptx", "xlsx", "txt", "csv", "json"]):
                    for ext in ["docx", "pptx", "xlsx", "pdf", "txt", "csv", "json"]:
                        if ext in msg_lower:
                            detected_query = ext
                            break
                elif stripped and len(stripped.split()) <= 3 and not any(fc in stripped.lower() for fc in folder_candidates):
                    clean_s = re.sub(r"^(?:file|files|doc|document)\s*", "", stripped, flags=re.IGNORECASE).strip()
                    if clean_s:
                        detected_query = clean_s

        # If query or folder missing, check conversation history
        if chat_history and (not detected_query or not detected_folder):
            for m in reversed(chat_history):
                content = m.get("content", "").lower()
                if not detected_query:
                    ext_match_h = re.search(r"(?:for\s+)?(?:\*\.)?([a-zA-Z0-9_\-]+)\s+files?", content)
                    if ext_match_h:
                        detected_query = ext_match_h.group(1)
                    else:
                        ext_dot_h = re.search(r"\.([a-zA-Z0-9_\-]+)", content)
                        if ext_dot_h:
                            detected_query = ext_dot_h.group(1)
                        elif any(k in content for k in ["docx", "pdf", "pptx", "xlsx", "txt", "csv", "json"]):
                            for ext in ["docx", "pptx", "xlsx", "pdf", "txt", "csv", "json"]:
                                if ext in content:
                                    detected_query = ext
                                    break

                if not detected_folder and m.get("role") == "user":
                    for fc in folder_candidates:
                        if fc in content:
                            detected_folder = f"{fc.capitalize()} folder"
                            break

        return detected_query, detected_folder

intent_router = IntentRouter()
