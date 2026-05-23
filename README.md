# 🏎️ F1 AI Race Engineer

An autonomous, AI-driven Race Engineer simulation. 

This project takes live telemetry from a Formula 1 race and processes it through a custom **AI Translation Layer**. Using an Event State Machine and IBM Granite, it acts as a personalized broadcast team, identifying overtakes, pit stops, and weather changes in real-time and narrating them with broadcast-quality voice.

### 🌟 Key Features
- **Autonomous Event Detection**: A custom state machine (`RaceEventDetector`) parses 25 FPS F1 telemetry to detect overtakes, close battles, pit stops, and weather transitions.
- **Anti-Hallucination Pipeline**: A 6-stage post-processing pipeline designed to mathematically ensure the IBM Granite model never hallucinates track layouts or invents driver names.
- **Broadcast Voice Integration**: Real-time ElevenLabs TTS integration providing ultra-low-latency, realistic "team radio" voice updates.
- **Interactive AI HUD**: A glassmorphic HUD that lets users manually ask the AI tactical questions during the race simulation.

## 🛠️ Setup
1. Create a `.env` file and add your credentials:
   ```env
   WATSONX_API_KEY=your_key_here
   WATSONX_PROJECT_ID=your_id_here
   ELEVENLABS_API_KEY=your_key_here
   ELEVENLABS_ENABLED=true
   ```
2. Install dependencies: `pip install -r requirements.txt`
3. Run the simulation: `python main.py`

---

## 👏 Credits & Foundation

This project's AI layer was built on top of the incredible [f1-race-replay](https://github.com/IAmTomShaw/f1-race-replay) open-source telemetry engine created by [Tom Shaw](https://tomshaw.dev). 

The base repository provided the foundational FastF1 data extraction, Arcade 25-FPS rendering pipeline, and Bayesian tyre degradation models. 

The **AI Narrator & Team Radio** functionality (including IBM Granite Watsonx integration, ElevenLabs TTS, and the Factual Post-Processing Pipeline) was designed and built as an independent intelligence layer on top of this powerful rendering engine.
