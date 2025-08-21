# minidebugger1

An autonomous Python debugger with optional LLM assistance for fixing code issues.

## Components

- **minidebugger18.py**: Main debugger that automatically fixes Python code issues
- **gemini-flask-57.py**: Flask proxy server for Gemini API with Tor support  
- **testcodebase*.py**: Test files with intentional issues for the debugger to fix

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Basic Smoke Test
```bash
python3 -c "import minidebugger18; print('SMOKE')"
```

### Run Debugger
```bash
export TARGET_FILE=testcodebase5.py
export LLM_DISABLED=1  # Disable LLM for local testing
python3 minidebugger18.py
```

### Run Flask Proxy (optional)
```bash
# Requires API keys configured
python3 gemini-flask-57.py
```

## Configuration

The debugger uses environment variables for configuration:

- `TARGET_FILE`: Path to Python file to debug
- `LLM_DISABLED=1`: Disable LLM features for testing
- `LLM_ENDPOINT`: HTTP endpoint for LLM service
- `MAX_ITERS`: Maximum debugging iterations
- `EXEC_TIMEOUT`: Timeout for code execution

## Dependencies

- Core: `requests` (for LLM communication)
- Flask proxy: `flask`, `psutil`, `stem` (Tor support)
- Test files: `aiohttp`, `asyncio`

All dependencies are optional except `requests`.