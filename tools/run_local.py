"""Start the compatible Ollama runtime and bridge using vlm.properties."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vlm_bridge


def runtime_for(model):
    # The current llama.cpp runner cannot load mllama; the older native engine can.
    if model.split(":", 1)[0] == "llama3.2-vision":
        return ROOT / ".local/ollama-0.24.0/ollama", "0.24.0"
    return ROOT / ".local/ollama/ollama", "0.40.16"


def server_version():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2) as response:
            return json.load(response)["version"]
    except OSError:
        return None


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--config", default=str(ROOT / "vlm.properties"))
    parser.add_argument("--backend", choices=("ollama", "openrouter"))
    parser.add_argument("--model")
    args, remaining = parser.parse_known_args()
    bridge_command = [sys.executable, "-u", str(ROOT / "vlm_bridge.py"), *sys.argv[1:]]
    if any(arg in remaining for arg in ("--help", "-h", "--selftest")):
        return subprocess.call(bridge_command)
    try:
        props = vlm_bridge.load_properties(args.config)
    except OSError as e:
        raise SystemExit(f"Cannot read settings file {args.config!r}: {e.strerror}") from e
    if args.backend:
        props["backend"] = args.backend
    if args.model:
        props[f"{props.get('backend', 'ollama').strip().lower()}.model"] = args.model
    backend = vlm_bridge.make_backend(props)
    if not isinstance(backend, vlm_bridge.OllamaBackend):
        return subprocess.call(bridge_command)
    if backend.url not in ("http://localhost:11434/api/chat", "http://127.0.0.1:11434/api/chat"):
        # Custom servers remain under the user's control.
        return subprocess.call(bridge_command)

    executable, required = runtime_for(backend.model)
    if not executable.is_file():
        raise SystemExit(f"Missing Ollama {required}: {executable}. See README.md for installation.")
    existing = server_version()
    if existing and existing != required:
        raise SystemExit(f"Port 11434 is running Ollama {existing}; {backend.model} needs {required}. "
                         "Stop that server, then run this launcher again.")

    server = bridge = None
    log = None
    try:
        if not existing:
            (ROOT / ".local").mkdir(exist_ok=True)
            log = (ROOT / ".local/ollama-server.log").open("a", encoding="utf-8")
            env = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434",
                       OLLAMA_MODELS=str(ROOT / ".local/ollama-models"), OLLAMA_NO_CLOUD="1",
                       OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_NOPRUNE="1")
            server = subprocess.Popen([str(executable), "serve"], env=env, stdout=log,
                                      stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 90
            while True:
                if server.poll() is not None:
                    raise SystemExit("Ollama could not start; see .local/ollama-server.log")
                version = server_version()
                if version:
                    if version != required:
                        raise SystemExit("Another Ollama server took port 11434; stop it and retry.")
                    break
                if time.monotonic() >= deadline:
                    raise SystemExit("Ollama startup timed out; see .local/ollama-server.log")
                time.sleep(0.5)
        print(f"Using Ollama {required} for {backend.model}. Ctrl+C stops this launcher.", flush=True)
        bridge = subprocess.Popen(bridge_command, start_new_session=True)
        return bridge.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        stop(bridge)
        stop(server)  # Never stop an existing server we did not start.
        if log is not None:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
