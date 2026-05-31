import os
import subprocess
import threading
import queue
import re
import tempfile
import requests
from dotenv import load_dotenv

load_dotenv()

class RadioVoice:
    def __init__(self):
        self._queue = queue.Queue()
        self._worker = threading.Thread(target=self._process_queue, daemon=True)
        self._worker.start()
        
        self.elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")
        self.use_elevenlabs = os.getenv("ELEVENLABS_ENABLED", "true").lower() == "true"
        # "onwK4e9ZLuTAKqWW03F9" (Daniel) is a Steady Broadcaster voice — ideal for F1 team radio narration.
        self.voice_id = "onwK4e9ZLuTAKqWW03F9"

    def toggle_elevenlabs(self):
        """Toggles the voice engine and returns the new state."""
        if not self.elevenlabs_key:
            self.use_elevenlabs = False
            return False
        self.use_elevenlabs = not self.use_elevenlabs
        return self.use_elevenlabs 

    def _process_queue(self):
        while True:
            text = self._queue.get()
            if text is None:
                break
            
            # Clean up text (remove markdown asterisks, hashtags)
            clean_text = re.sub(r'[*_#]', '', text)
            
            if self.elevenlabs_key and self.use_elevenlabs:
                self._speak_elevenlabs(clean_text)
            else:
                self._speak_mac_fallback(clean_text)
                
            self._queue.task_done()

    def _speak_elevenlabs(self, text: str):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.elevenlabs_key
        }
        data = {
            "text": text,
            "model_id": "eleven_turbo_v2_5", # Turbo model is incredibly fast and cheap
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": 1.2
            }
        }
        
        try:
            response = requests.post(url, json=data, headers=headers)
            response.raise_for_status()
            
            # Save the MP3 to a temporary file
            temp_path = os.path.join(tempfile.gettempdir(), "f1_radio.mp3")
            with open(temp_path, "wb") as f:
                f.write(response.content)
                
            # Play it asynchronously on Mac using the native afplay command
            subprocess.run(["afplay", "-r", "1.25", temp_path])
            
        except Exception as e:
            print(f"ElevenLabs TTS failed: {e}. Falling back to Mac TTS...")
            self._speak_mac_fallback(text)

    def _speak_mac_fallback(self, text: str):
        try:
            subprocess.run(["say", "-v", "Daniel", "-r", "180", text])
        except Exception as e:
            print(f"Mac RadioVoice error: {e}")

    def speak(self, text: str):
        """Adds a message to the queue to be spoken asynchronously."""
        if not text or "[Granite" in text or "[Analysis" in text:
            return
        self._queue.put(text)
