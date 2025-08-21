PROJECT NAME: minidebugger1
REPO: https://github.com/perlman-izzy/minidebugger1 (public)

LANGUAGES/TOOLS (check all that apply):
- [x] Python  (version: 3.12)
- [ ] Node/PNPM/NPM (node version: <e.g., 20>)
- [ ] Java/Gradle (jdk: <e.g., 21>)
- [ ] .NET (sdk: <e.g., 8.0>)
- [ ] Android SDK (yes/no)
- [ ] Other: Flask web server

PYTHON PACKAGING (pick one):
- [x] requirements.txt
- [ ] pyproject.toml (backend: <e.g., hatch/poetry/setuptools>)
- [ ] none (infer packages from imports)

TEST COMMANDS (write exact commands; I'll wire them):
- Unit tests: python3 -m py_compile *.py
- Lint (optional): python3 -m py_compile *.py
- Build (optional): N/A

ENTRY/SMOKE (short, non-blocking check):
- Python: python3 -c "import minidebugger18; print('SMOKE')"

SYSTEM PACKAGES NEEDED (apt): 
build-essential, python3-dev, tor (optional for gemini-flask-57.py)

NATIVE/LIB EXTRAS:
- TA-Lib needed? no
- OpenCV? no  FFMPEG? no
- CUDA/GPU? no   (Jules VMs are CPU-only—confirm)

EXTERNAL SERVICES NEEDED AT TEST TIME (prefer NONE):
None (LLM endpoint is optional, Tor is optional)

ENV VARS REQUIRED TO IMPORT/RUN TESTS (safe dummy values ok):
TARGET_FILE=/tmp/test.py, LLM_DISABLED=1, LLM_ENDPOINT=http://localhost:8000/mock

TIME/LONG-RUNNING GUARDRAILS:
- Max setup time: 5 min
- Do NOT start servers, websockets, infinite loops (agree: yes)

SPECIAL NOTES:
- minidebugger18.py: Autonomous Python debugger that fixes code using optional LLM
- gemini-flask-57.py: Flask proxy for Gemini API with Tor support (optional dependencies)
- testcodebase*.py: Test files for the debugger to analyze and fix
- Main dependencies: requests, flask, psutil, stem (optional), aiohttp (test files)
- Project is designed to work with minimal dependencies, LLM and Tor features are optional