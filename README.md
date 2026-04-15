# April: A Local, Context-Aware AI Desktop Agent

April is an autonomous desktop companion that continuously interprets your workflow and system state in real-time. Designed with a strict privacy-first, 100% offline architecture, the application operates entirely on edge AI models (Llama 3.2-Vision via Ollama, Kokoro-ONNX).

Under the hood, April utilizes a thread-safe, dual-loop polling system to analyze screen data and system telemetry (CPU, RAM, active Win32 processes) without blocking the UI. By implementing heuristic frame-differencing ("Smart Skip") and intelligent context buffering, the application maximizes VRAM efficiency and prevents redundant inference calls, delivering a seamless, highly optimized interactive experience.

## Core Engineering Achievements:

* **Privacy-First Edge AI Inference:** Fully localized stack eliminating cloud dependencies. Uses llama3.2-vision for visual context and kokoro-onnx for ultra-low latency Text-to-Speech synthesis.
* **Concurrent System Architecture:** Implements a dual-thread design separating background context accumulation (screen capture, system telemetry) from the main reaction loop and TTS audio playback, ensuring zero UI freezing.
* **Resource Optimization & Smart Caching:** Custom screen-capture pipeline using mss and numpy. Implements matrix-diffing on downscaled frames to bypass inference when the screen is static, aggressively optimizing VRAM and CPU cycles.
* **Dynamic State Management:** A built-in state machine tracks session memory, user affection metrics, and anti-repetition buffers to dynamically prompt the LLM, ensuring contextual and non-repetitive interactions.
* **Deep OS Integration:** Uses psutil and ctypes.windll.user32 to actively map Win32 handles to human-readable application states, blending hard system data with visual VLM context.
* **Custom Borderless GUI:** A standalone, transparent UI overlay built in tkinter utilizing chroma-keying and threaded 150ms interval lip-syncing for the animated sprite.
