"""
RVC Voice Conversion Sidecar — FastAPI server.
Runs in a separate Python 3.10 venv to avoid fairseq/Python 3.14 conflicts.

Exposes:
  POST /convert  — accepts base64 WAV, returns converted WAV
  GET  /health   — health check

Pre-loads the RVC model on startup for fast inference.
"""
import base64
import os
import tempfile
import time

# PyTorch 2.6 breaking change workaround for fairseq
import torch
_original_load = torch.load
def _patched_load(*args, **kwargs):
    kwargs["weights_only"] = False
    return _original_load(*args, **kwargs)
torch.load = _patched_load

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="April RVC Sidecar", version="1.0")

# ─── Global RVC State ────────────────────────────────────────
_rvc = None
_model_loaded = False
_model_name = "ironmouse"

# Paths (relative to this script)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(SCRIPT_DIR, "models")

# RVC inference settings
RVC_DEVICE = os.getenv("RVC_DEVICE", "cuda:0")
RVC_F0_METHOD = os.getenv("RVC_F0_METHOD", "rmvpe")
RVC_F0_CHANGE = int(os.getenv("RVC_F0_CHANGE", "0"))  # pitch shift in semitones
RVC_INDEX_RATE = float(os.getenv("RVC_INDEX_RATE", "0.75"))
RVC_PROTECT = float(os.getenv("RVC_PROTECT", "0.33"))
RVC_RMS_MIX = float(os.getenv("RVC_RMS_MIX", "0.25"))
RVC_FILTER_RADIUS = int(os.getenv("RVC_FILTER_RADIUS", "3"))


class ConvertRequest(BaseModel):
    audio_b64: str        # base64-encoded WAV audio
    f0_change: int = 0    # optional per-request pitch override


def _find_model_files():
    """Scan models dir for .pth and .index files."""
    pth_file = None
    index_file = None

    for dirpath, dirnames, filenames in os.walk(MODELS_DIR):
        for f in filenames:
            if f.endswith(".pth") and pth_file is None:
                pth_file = os.path.join(dirpath, f)
            elif f.endswith(".index") and index_file is None:
                index_file = os.path.join(dirpath, f)

    return pth_file, index_file


def _init_rvc():
    """Initialize RVC inference engine and load model."""
    global _rvc, _model_loaded

    try:
        from rvc_python.infer import RVCInference

        pth_file, index_file = _find_model_files()
        if not pth_file:
            print(f"[RVC] [X] No .pth model found in {MODELS_DIR}")
            print("[RVC]   Run setup_rvc.ps1 to download the Ironmouse model")
            return False

        print(f"[RVC] Loading model: {os.path.basename(pth_file)}")
        print(f"[RVC] Device: {RVC_DEVICE} | F0: {RVC_F0_METHOD} | Pitch: {RVC_F0_CHANGE}")

        _rvc = RVCInference(device=RVC_DEVICE)
        _rvc.load_model(pth_file, index_path=index_file or "")

        # Configure inference params
        _rvc.set_params(
            f0method=RVC_F0_METHOD,
            f0up_key=RVC_F0_CHANGE,
            index_rate=RVC_INDEX_RATE if index_file else 0.0,
            protect=RVC_PROTECT,
            rms_mix_rate=RVC_RMS_MIX,
            filter_radius=RVC_FILTER_RADIUS,
        )

        _model_loaded = True
        print("[RVC] [OK] Model loaded successfully")
        if index_file:
            print(f"[RVC] [OK] Index file: {os.path.basename(index_file)}")
        else:
            print("[RVC] [!] No .index file found (quality may be reduced)")
        return True

    except Exception as e:
        print(f"[RVC] [X] Failed to initialize: {e}")
        import traceback
        traceback.print_exc()
        return False


@app.on_event("startup")
async def startup():
    """Load RVC model when server starts."""
    print("[RVC] --- April RVC Sidecar Starting ---")
    print(f"[RVC] Models dir: {MODELS_DIR}")
    success = _init_rvc()
    if not success:
        print("[RVC] [!] Server running but no model loaded — /convert will fail")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok" if _model_loaded else "no_model",
        "model": _model_name if _model_loaded else None,
        "device": RVC_DEVICE,
    }


@app.post("/convert")
def convert(req: ConvertRequest):
    """Convert base64-encoded WAV audio through the loaded RVC model."""
    if not _model_loaded or _rvc is None:
        raise HTTPException(status_code=503, detail="RVC model not loaded")

    try:
        # Decode base64 audio
        audio_bytes = base64.b64decode(req.audio_b64)
        if len(audio_bytes) < 100:
            raise HTTPException(status_code=400, detail="Audio too small")

        # Write input to temp file (rvc-python requires file paths)
        tmp_in = tempfile.NamedTemporaryFile(
            delete=False, suffix=".wav", prefix="rvc_in_"
        )
        tmp_in.write(audio_bytes)
        tmp_in.close()

        tmp_out = tempfile.NamedTemporaryFile(
            delete=False, suffix=".wav", prefix="rvc_out_"
        )
        tmp_out.close()

        try:
            # Apply pitch override if specified
            if req.f0_change != 0:
                _rvc.set_params(f0up_key=req.f0_change)

            start = time.perf_counter()

            _rvc.infer_file(
                input_path=tmp_in.name,
                output_path=tmp_out.name,
            )

            elapsed = time.perf_counter() - start
            print(f"[RVC] ✓ Converted in {elapsed:.2f}s")

            # Restore default pitch
            if req.f0_change != 0:
                _rvc.set_params(f0up_key=RVC_F0_CHANGE)

            # Read output
            with open(tmp_out.name, "rb") as f:
                result_bytes = f.read()

            if len(result_bytes) < 100:
                raise HTTPException(
                    status_code=500,
                    detail="RVC produced empty/tiny output"
                )

            # Return as base64
            result_b64 = base64.b64encode(result_bytes).decode("ascii")
            return {"audio_b64": result_b64, "elapsed_ms": int(elapsed * 1000)}

        finally:
            # Cleanup temp files
            for p in (tmp_in.name, tmp_out.name):
                try:
                    os.remove(p)
                except OSError:
                    pass

    except HTTPException:
        raise
    except Exception as e:
        print(f"[RVC] ✗ Conversion failed: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    port = int(os.getenv("RVC_PORT", "5055"))
    print(f"[RVC] Starting sidecar on port {port}...")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
