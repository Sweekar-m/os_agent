import re
import urllib.parse
from typing import Dict, Any, Optional

# Emoji replacement table for semantic context
SEMANTIC_EMOJI_PHRASES: Dict[str, str] = {
    # Status / Success
    "✅": "successfully",
    "✔️": "successfully",
    "☑️": "successfully",
    "🎉": "",
    # Alerts / Failures
    "⚠️": "warning:",
    "🚨": "alert:",
    "❌": "error:",
    "🛑": "stopped:",
    "⛔": "blocked:",
    # Greetings & Gestures (omit purely decorative salutations)
    "👋": "",
    "🤝": "",
    "👍": "",
    "👎": "",
    "🙏": "thank you",
    # Reactions & Emotions (preserve sentence, drop purely decorative emoji)
    "😂": "",
    "🤣": "",
    "😊": "",
    "😀": "",
    "😃": "",
    "😄": "",
    "😁": "",
    "🙂": "",
    "😉": "",
    "😎": "",
    "🤔": "",
    "😢": "",
    "😭": "",
    # Objects & Tech (drop decoration before nouns)
    "🚀": "",
    "💻": "",
    "🖥️": "",
    "📱": "",
    "🌐": "",
    "📁": "",
    "📄": "",
    "📊": "",
    "⚙️": "",
    "⚡": "",
    "💡": "",
    "🔍": "",
    "🔎": "",
    "📋": "",
    "🤖": "",
    "🎨": "",
    "🔊": "",
    "🔇": "",
    "🎤": "",
    "🔒": "",
    "🔓": "",
}

# Common abbreviations to expand for speech synthesis
ABBREVIATIONS: Dict[str, str] = {
    r"\be\.g\.\b": "for example",
    r"\bi\.e\.\b": "that is",
    r"\bvs\.\b": "versus",
    r"\betc\.\b": "etcetera",
    r"\bapprox\.\b": "approximately",
    r"\bGB\b": "gigabytes",
    r"\bMB\b": "megabytes",
    r"\bKB\b": "kilobytes",
    r"\bGHz\b": "gigahertz",
    r"\bMHz\b": "megahertz",
    r"\bOS\b": "operating system",
    r"\bUI\b": "U I",
    r"\bCPU\b": "C P U",
    r"\bRAM\b": "RAM",
    r"\bPID\b": "P I D",
    r"\bIP\b": "I P",
    r"\bTTS\b": "T T S",
    r"\bSTT\b": "S T T",
    r"\bPC\b": "P C",
}

# Known emoji explanations for user inquiries
EMOJI_EXPLANATIONS: Dict[str, str] = {
    "😂": "The face with tears of joy emoji, commonly used to show intense amusement or laughter.",
    "🤣": "The rolling on the floor laughing emoji, expressing hysterical laughter.",
    "⚠️": "The warning sign emoji, alerting someone to pay attention to a potential issue or hazard.",
    "✅": "The check mark emoji, indicating success, completion, or correctness.",
    "❌": "The cross mark emoji, representing failure, cancellation, or error.",
    "🛑": "The octagonal stop sign emoji, indicating an emergency halt or complete stop.",
    "👋": "The waving hand emoji, used as a friendly greeting or farewell.",
    "🚀": "The rocket emoji, representing a launch, rapid growth, or speedy delivery.",
    "🤖": "The robot emoji, symbolizing artificial intelligence or automated agents.",
    "💡": "The lightbulb emoji, representing an idea, suggestion, or helpful insight.",
    "❤️": "The red heart emoji, expressing warmth, love, or appreciation.",
    "👍": "The thumbs up emoji, expressing approval, agreement, or confirmation.",
}

class SpeechNormalizer:
    """
    Transforms assistant text responses into natural, human-like spoken speech.
    Strips raw markdown, code blocks, raw JSON, and URLs.
    Handles emojis contextually: omits decorative emojis, conveys status emojis,
    and explains emoji meanings when the user explicitly asks.
    """

    @classmethod
    def is_emoji_inquiry(cls, user_prompt: str) -> bool:
        """Determines if the user explicitly asked about the meaning of an emoji."""
        if not user_prompt:
            return False
        p_lower = user_prompt.lower()
        triggers = [
            "what does", "meaning of", "explain this emoji", "what is this emoji",
            "emoji mean", "what emoji", "why the emoji"
        ]
        return any(t in p_lower for t in triggers) and any(e in user_prompt for e in EMOJI_EXPLANATIONS)

    @classmethod
    def explain_emoji_if_requested(cls, user_prompt: str) -> Optional[str]:
        """Returns an explanation if the user asked about an emoji."""
        for emoji_char, explanation in EMOJI_EXPLANATIONS.items():
            if emoji_char in user_prompt:
                return explanation
        return None

    @classmethod
    def normalize_for_speech(cls, text: str, user_prompt: Optional[str] = None, max_length: int = 450) -> str:
        """
        Main pipeline converting visual text to clean, natural speech audio text.
        Preserves numbers, technical words, and negations without robotic artifacting.
        """
        if not text:
            return ""

        # Check if the user was explicitly asking what an emoji means
        if user_prompt and cls.is_emoji_inquiry(user_prompt):
            explanation = cls.explain_emoji_if_requested(user_prompt)
            if explanation:
                return explanation

        speech = text

        # 1. Remove fenced code blocks completely
        speech = re.sub(r"```[\w\-]*[\r\n]+[\s\S]*?```", "", speech)
        speech = re.sub(r"```[\s\S]*?```", "", speech)

        # 2. Remove inline code backticks while preserving the code word itself
        speech = re.sub(r"`([^`]+)`", r"\1", speech)

        # 3. Clean markdown tables: remove header dividers and pipes
        speech = re.sub(r"\|?\s*:?-+:?\s*\|?", " ", speech)
        speech = re.sub(r"\|", " ", speech)

        # 4. Clean markdown links: [Title](url) -> Title
        speech = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", speech)

        # 5. Clean raw URLs: https://example.com/docs -> "example.com" or "link"
        def simplify_url(match):
            raw = match.group(0)
            try:
                parsed = urllib.parse.urlparse(raw)
                domain = parsed.netloc or "link"
                domain = domain.replace("www.", "")
                return domain if domain else "link"
            except Exception:
                return "link"

        speech = re.sub(r"https?://\S+", simplify_url, speech)

        # 6. Remove markdown headers, bold, italics, strikethroughs, blockquotes
        speech = re.sub(r"^#{1,6}\s+", "", speech, flags=re.MULTILINE)
        speech = re.sub(r"\*\*([^*]+)\*\*", r"\1", speech)
        speech = re.sub(r"\*([^*]+)\*", r"\1", speech)
        speech = re.sub(r"__([^_]+)__", r"\1", speech)
        speech = re.sub(r"~~([^~]+)~~", r"\1", speech)
        speech = re.sub(r"^>\s*", "", speech, flags=re.MULTILINE)

        # 7. Remove list bullet artifacts at start of lines
        speech = re.sub(r"^\s*[-*•]\s+", "", speech, flags=re.MULTILINE)
        speech = re.sub(r"^\s*\d+\.\s+", "", speech, flags=re.MULTILINE)

        # 8. Handle Contextual Emojis
        # Handle "Task completed ✅" -> "Task completed successfully."
        speech = re.sub(r"(completed|finished|done|succeeded)\s*✅", r"\1 successfully.", speech, flags=re.IGNORECASE)
        # Handle "Warning ⚠️" or "I got a warning ⚠️" -> "I got a warning."
        speech = re.sub(r"(warning|alert|caution)\s*⚠️", r"\1.", speech, flags=re.IGNORECASE)

        # Apply specific semantic emoji rules
        for emoji_char, replacement in SEMANTIC_EMOJI_PHRASES.items():
            if emoji_char in speech:
                speech = speech.replace(emoji_char, replacement)

        # Remove any remaining Unicode emojis and emoji sequences
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # flags (iOS)
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "\U0001F900-\U0001F9FF"  # supplemental symbols
            "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-a
            "\U00002600-\U000026FF"  # misc symbols
            "]+",
            flags=re.UNICODE
        )
        speech = emoji_pattern.sub(" ", speech)

        # 9. Expand common abbreviations
        for abbr_pattern, expansion in ABBREVIATIONS.items():
            speech = re.sub(abbr_pattern, expansion, speech)

        # 10. Clean punctuation and whitespace (preserving decimal numbers like 15.4 and domains)
        speech = re.sub(r"\s*([!?,;])\s*", r"\1 ", speech)
        speech = re.sub(r"!+", "!", speech)
        speech = re.sub(r"\?+", "?", speech)
        speech = re.sub(r"\.{3,}", "...", speech)
        # Colons not between digits (times like 12:30 preserved)
        speech = re.sub(r"(?<!\d)\s*:\s*(?!\d)", ": ", speech)
        # Periods: add space after period if followed by word, but not between digits (15.4) or in domain (example.com)
        speech = re.sub(r"(?<=[a-zA-Z0-9])\.\s+(?=[a-zA-Z])", ". ", speech)
        speech = re.sub(r"\s+", " ", speech).strip()

        # 11. Normalize leading colons/dashes/periods from previous removals
        speech = re.sub(r"^[:\-,.]\s*", "", speech).strip()

        # 12. Ensure natural ending punctuation if the text ends abruptly
        if speech and speech[-1] not in ".!?":
            speech += "."

        # 13. Length capping with clean word boundary truncation
        if len(speech) > max_length:
            truncated = speech[:max_length]
            last_period = truncated.rfind(".")
            last_comma = truncated.rfind(",")
            cutoff = max(last_period, last_comma)
            if cutoff > max_length * 0.7:
                speech = truncated[:cutoff + 1]
            else:
                last_space = truncated.rfind(" ")
                if last_space > 0:
                    speech = truncated[:last_space] + "..."
                else:
                    speech = truncated + "..."

        return speech

normalizer = SpeechNormalizer()
normalize_for_speech = SpeechNormalizer.normalize_for_speech
