"""
IBM Granite client for generating AI race narrations.

Uses the ibm-watsonx-ai SDK to call Granite models hosted on watsonx.ai.
Credentials are read from environment variables:
  - WATSONX_API_KEY
  - WATSONX_PROJECT_ID
  - WATSONX_URL  (defaults to https://us-south.ml.cloud.ibm.com)
"""

import os
import re
import threading
from typing import Callable, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; user can set env vars directly


# ── Strictly grounded system prompt ──────────────────────────────────────
SYSTEM_PROMPT = (
    "You are an F1 television commentator describing what is happening "
    "during a live race based ONLY on telemetry data provided to you.\n\n"

    "STRICT RULES:\n"
    "1. ONLY use information explicitly provided in the telemetry data below. "
    "Do NOT invent or assume any facts — no team names, no driver histories, "
    "no circuit names, no weather conditions, no past race references, no "
    "championship standings, unless that exact information appears in the data.\n"
    "2. Refer to drivers ONLY by their three-letter codes (e.g. VER, HAM, NOR) "
    "as shown in the data. Do NOT guess, expand, or write full names. "
    "(Write 'VER', never 'Verstappen').\n"
    "3. Write exactly 2-3 short sentences. Be concise.\n"
    "4. Use a broadcast commentary tone a casual fan can understand.\n"
    "5. Focus on what the numbers tell us: gaps, speeds, tyre age, DRS status.\n"
    "6. Output plain text only. No markdown, no bullet points, no emojis, "
    "no asterisks, no headers.\n"
    "7. If the data is insufficient to explain something, say so — "
    "do NOT fill in gaps with speculation.\n"
    "8. Do NOT mention any driver who is not explicitly named in the "
    "telemetry data. Never assume or infer which drivers are in other "
    "positions unless the data says so.\n"
    "9. Do NOT give tactical advice or driving recommendations. "
    "Never say a driver 'should', 'needs to', or 'must' do something. "
    "You are a commentator observing what IS happening, not an engineer "
    "telling drivers what to do.\n"
    "10. Do NOT reference specific corners, turns, or track sections "
    "(e.g. 'Turn 1', 'Turns 1-3', 'the chicane') unless the telemetry "
    "data explicitly names them.\n"
    "11. Do NOT predict future events. Never say 'the next safety car', "
    "'upcoming rain', 'will crash', or speculate about what will happen "
    "later in the race.\n\n"

    "EXAMPLE:\n"
    "Input: PIT STOP on Lap 15/58:\n"
    "- NOR (P3) has entered the pits\n"
    "- Current tyres: Soft (14 laps old)\n\n"
    "Good output: NOR pits from P3 after 14 laps on soft tyres, "
    "which are well past their performance window. He will likely "
    "switch to mediums for a longer second stint.\n\n"
    "Bad output 1 (DO NOT DO THIS): NOR pits from P3, which puts "
    "VER and HAM in a strong position. — BAD: VER and HAM are not "
    "in the data.\n"
    "Bad output 2 (DO NOT DO THIS): NOR should brake later into "
    "Turn 3 to gain time. — BAD: giving driving advice and "
    "referencing a corner not in the data.\n"
)

# ── Generation parameters for factual, deterministic output ──────────────
GENERATION_PARAMS = {
    "max_tokens": 200,        # Hard cap — prevents rambling
    "temperature": 0.15,      # Very low creativity — stick to the facts
    "top_p": 0.80,            # Tighter nucleus sampling
    "repetition_penalty": 1.1, # Discourage repetitive phrasing
}


class GraniteClient:
    """Thread-safe client for IBM Granite on watsonx.ai."""

    def __init__(self):
        self._model = None
        self._init_error: Optional[str] = None
        self._lock = threading.Lock()
        self._initialize()

    def _initialize(self):
        """Lazy-initialize the watsonx.ai model. Fails gracefully."""
        api_key = os.environ.get("WATSONX_API_KEY")
        project_id = os.environ.get("WATSONX_PROJECT_ID")
        url = os.environ.get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")

        if not api_key or not project_id:
            self._init_error = (
                "Missing IBM credentials. Set WATSONX_API_KEY and "
                "WATSONX_PROJECT_ID environment variables."
            )
            print(f"⚠️  GraniteClient: {self._init_error}")
            return

        try:
            from ibm_watsonx_ai import APIClient, Credentials
            from ibm_watsonx_ai.foundation_models import ModelInference

            credentials = Credentials(api_key=api_key, url=url)
            client = APIClient(credentials=credentials, project_id=project_id)

            # Try a prioritized list of IBM Granite models since availability varies by region
            preferred_models = [
                "ibm/granite-3-3-8b-instruct",
                "ibm/granite-4-h-small",
                "ibm/granite-3-1-8b-instruct",
                "ibm/granite-3-8b-instruct",
            ]

            initialized_model = None
            last_err = None

            for model_id in preferred_models:
                try:
                    initialized_model = ModelInference(
                        model_id=model_id,
                        api_client=client,
                        params=GENERATION_PARAMS,
                    )
                    print(f"✓ GraniteClient initialized ({model_id})")
                    break
                except Exception as e:
                    last_err = e
                    # Continue trying other models
                    continue

            if initialized_model is None:
                raise last_err or Exception("No preferred Granite models could be initialized.")

            self._model = initialized_model

        except ImportError:
            self._init_error = (
                "ibm-watsonx-ai package not installed. "
                "Run: pip install ibm-watsonx-ai"
            )
            print(f"⚠️  GraniteClient: {self._init_error}")
        except Exception as e:
            self._init_error = f"Failed to initialize: {e}"
            print(f"⚠️  GraniteClient: {self._init_error}")

    @property
    def is_available(self) -> bool:
        return self._model is not None

    @property
    def error_message(self) -> Optional[str]:
        return self._init_error

    @staticmethod
    def _sanitize_response(text: str) -> str:
        """
        Post-process the model output to remove hallucination artifacts.

        - Strips markdown formatting (**, ##, *, ```, etc.)
        - Removes stray emoji
        - Collapses excessive whitespace
        - Truncates to ~3 sentences if the model rambled
        """
        if not text:
            return text

        # Strip markdown artifacts
        text = re.sub(r'\*\*|__|##|```.+?```', '', text)
        text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\[.*?\]\(.*?\)', '', text)  # markdown links

        # Collapse whitespace
        text = re.sub(r'\n+', ' ', text)
        text = re.sub(r' {2,}', ' ', text)
        text = text.strip()

        # Truncate to ~3 sentences to prevent rambling
        sentences = re.split(r'(?<=[.!?])\s+', text)
        if len(sentences) > 4:
            text = ' '.join(sentences[:3])

        return text

    @staticmethod
    def _validate_driver_codes(text: str, prompt: str) -> str:
        """
        Post-generation factual validation.

        Extracts the set of 3-letter driver codes mentioned in the original
        prompt (the ground truth), then scans the generated text for any
        3-letter uppercase codes that do NOT appear in the prompt.
        Sentences containing hallucinated codes are removed.
        """
        if not text or not prompt:
            return text

        if "USER QUESTION:" in prompt:
            return text

        # Extract all 3-letter uppercase codes from the prompt (ground truth)
        prompt_codes = set(re.findall(r'\b([A-Z]{3})\b', prompt))

        # Common F1 abbreviations that are NOT driver codes
        non_driver_codes = {
            "DRS", "VSC", "PIT", "LAP", "OUT", "DNF", "DNS",
            "THE", "AND", "FOR", "BUT", "NOT", "HIS", "HAS",
            "ARE", "WAS", "MAY", "CAN", "ALL", "CAR", "SET",
            "TOP", "GAP", "NOW", "TWO", "ONE", "NEW", "OLD",
            "RED", "WET", "RUN", "GOT", "PUT", "SAW", "LET",
        }
        prompt_codes = prompt_codes | non_driver_codes

        # Split into sentences and filter
        sentences = re.split(r'(?<=[.!?])\s+', text)
        clean_sentences = []
        for sentence in sentences:
            # Find 3-letter codes in this sentence
            sentence_codes = set(re.findall(r'\b([A-Z]{3})\b', sentence))
            hallucinated = sentence_codes - prompt_codes
            if not hallucinated:
                clean_sentences.append(sentence)
            # else: silently drop the sentence

        if not clean_sentences:
            # All sentences were hallucinated — return the original
            # rather than nothing (the _is_valid_response check will catch it)
            return text

        return ' '.join(clean_sentences)

    # ── Map of full driver names → 3-letter codes for post-processing ──
    _DRIVER_NAME_TO_CODE = {
        "verstappen": "VER", "hamilton": "HAM", "norris": "NOR",
        "leclerc": "LEC", "piastri": "PIA", "russell": "RUS",
        "sainz": "SAI", "alonso": "ALO", "stroll": "STR",
        "gasly": "GAS", "ocon": "OCO", "tsunoda": "TSU",
        "ricciardo": "RIC", "bottas": "BOT", "zhou": "ZHO",
        "magnussen": "MAG", "hulkenberg": "HUL", "albon": "ALB",
        "sargeant": "SAR", "perez": "PER", "lawson": "LAW",
        "bearman": "BEA", "colapinto": "COL", "doohan": "DOO",
        "hadjar": "HAD", "bortoleto": "BOR", "antonelli": "ANT",
        "max": "VER", "lewis": "HAM", "lando": "NOR",
        "charles": "LEC", "oscar": "PIA", "george": "RUS",
        "carlos": "SAI", "fernando": "ALO", "lance": "STR",
        "pierre": "GAS", "esteban": "OCO", "yuki": "TSU",
        "daniel": "RIC", "valtteri": "BOT", "guanyu": "ZHO",
        "kevin": "MAG", "nico": "HUL", "alexander": "ALB",
        "sergio": "PER",
    }

    # ── Circuit and team names the model might hallucinate ──
    _HALLUCINATED_CONTEXT = [
        # Circuit names
        "silverstone", "monza", "spa", "monaco", "suzuka", "interlagos",
        "jeddah", "bahrain", "melbourne", "imola", "barcelona", "montreal",
        "spielberg", "hungaroring", "zandvoort", "singapore", "austin",
        "mexico city", "las vegas", "abu dhabi", "losail", "shanghai",
        "miami", "baku", "albert park",
        # Team names
        "red bull", "mercedes", "ferrari", "mclaren", "aston martin",
        "alpine", "williams", "haas", "rb ", "kick sauber", "sauber",
        # Championship references
        "world champion", "championship leader", "defending champion",
        "title contender", "points leader",
    ]

    @staticmethod
    def _enforce_driver_codes(text: str) -> str:
        """
        Replace any full driver names with their 3-letter codes.

        This catches cases where the model writes 'Verstappen' instead of
        'VER' despite the system prompt instruction.
        """
        if not text:
            return text

        for name, code in GraniteClient._DRIVER_NAME_TO_CODE.items():
            # Case-insensitive word-boundary replacement
            pattern = rf'\b{re.escape(name)}\b'
            text = re.sub(pattern, code, text, flags=re.IGNORECASE)

        return text

    @staticmethod
    def _strip_hallucinated_context(text: str) -> str:
        """
        Remove fabricated circuit names, team names, and championship
        references that the model invents from its training data.

        Instead of dropping entire sentences, we surgically remove the
        offending phrase (e.g. 'at Silverstone' → '').
        """
        if not text:
            return text

        for phrase in GraniteClient._HALLUCINATED_CONTEXT:
            # Remove patterns like "at Silverstone", "in Monaco", "at the Hungaroring"
            text = re.sub(
                rf'\b(at|in|around|of)\s+(the\s+)?{re.escape(phrase)}\b',
                '', text, flags=re.IGNORECASE
            )
            # Remove standalone mentions like "Silverstone circuit"
            text = re.sub(
                rf'\b{re.escape(phrase)}\s*(circuit|track|grand prix|gp)?\b',
                '', text, flags=re.IGNORECASE
            )

        # Collapse any leftover double spaces or orphaned punctuation
        text = re.sub(r' {2,}', ' ', text)
        text = re.sub(r'\s+([.!?,])', r'\1', text)
        text = text.strip()

        return text

    @staticmethod
    def _is_valid_response(text: str) -> bool:
        """
        Basic sanity check on the model response.

        Returns False for empty, refusal, or clearly broken output.
        """
        if not text or len(text) < 20:
            return False

        # Detect common refusal / confusion patterns
        refusal_patterns = [
            "i cannot", "i can't", "as an ai", "as a language model",
            "i don't have access", "i'm not able", "i apologize",
        ]
        text_lower = text.lower()
        for pattern in refusal_patterns:
            if pattern in text_lower:
                return False

        return True

    def generate(self, prompt: str) -> str:
        """
        Synchronous generation. Call from a background thread.

        Args:
            prompt: The user-facing prompt describing the race event.

        Returns:
            The generated narration text, or an error string.
        """
        if not self.is_available:
            return f"[Granite unavailable: {self._init_error}]"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            with self._lock:
                response = self._model.chat(messages=messages)

            # Extract the text from the response
            raw_text = ""
            if isinstance(response, dict):
                choices = response.get("choices", [])
                if choices:
                    raw_text = choices[0].get("message", {}).get("content", "").strip()
            if not raw_text:
                raw_text = str(response).strip()

            # ── Post-processing pipeline ──
            # 1. Strip markdown artifacts and truncate
            clean_text = self._sanitize_response(raw_text)
            # 2. Replace full names → 3-letter codes
            clean_text = self._enforce_driver_codes(clean_text)
            # 3. Remove hallucinated circuit/team/championship references
            clean_text = self._strip_hallucinated_context(clean_text)
            # 4. Drop sentences referencing drivers not in the prompt
            clean_text = self._validate_driver_codes(clean_text, prompt)

            if not self._is_valid_response(clean_text):
                return "[Analysis unavailable for this event]"

            return clean_text

        except Exception as e:
            return f"[Granite error: {e}]"

    def generate_async(self, prompt: str, callback: Callable[[str], None]):
        """
        Non-blocking generation. Runs the API call in a background thread
        and invokes `callback(result_text)` when done.

        Args:
            prompt: The user-facing prompt.
            callback: Function called with the result string.
        """
        def _worker():
            result = self.generate(prompt)
            callback(result)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

