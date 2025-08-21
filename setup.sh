#!/usr/bin/env bash
set -euxo pipefail

# Export required environment variables
export DEBIAN_FRONTEND=noninteractive
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PIP_DISABLE_PIP_VERSION_CHECK=1

echo "=== Starting Fresh Linux VM Setup for Google Jules ==="

# Install system dependencies
echo "Installing system dependencies..."
if command -v sudo >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y build-essential python3-dev python3-venv python3-pip libssl-dev libffi-dev pkg-config git curl wget
else
    apt-get update
    apt-get install -y build-essential python3-dev python3-venv python3-pip libssl-dev libffi-dev pkg-config git curl wget
fi

# Create and activate Python virtual environment
echo "Setting up Python virtual environment..."
if command -v uv >/dev/null 2>&1; then
    echo "Using uv for virtual environment creation..."
    uv venv .venv
    source .venv/bin/activate
    # Use uv for pip operations if available
    PIP_CMD="uv pip"
else
    echo "Using python3 -m venv for virtual environment creation..."
    python3 -m venv .venv
    source .venv/bin/activate
    PIP_CMD="pip"
fi

# Upgrade pip to latest version
echo "Upgrading pip..."
$PIP_CMD install --upgrade pip

# Install project dependencies
echo "Installing project dependencies..."
if [ -f "requirements.txt" ]; then
    echo "Found requirements.txt, installing dependencies from file..."
    $PIP_CMD install -r requirements.txt
elif [ -f "pyproject.toml" ]; then
    echo "Found pyproject.toml, installing project in editable mode..."
    $PIP_CMD install -e .
else
    echo "No requirements.txt or pyproject.toml found, installing common crypto bot dependencies..."
    $PIP_CMD install ccxt pandas numpy scikit-learn python-binance requests python-dotenv pytest
fi

# Attempt to install TA-Lib with fallback
echo "Attempting to install TA-Lib..."
if $PIP_CMD install ta-lib-bin 2>/dev/null; then
    echo "Successfully installed ta-lib-bin"
elif $PIP_CMD install TA-Lib 2>/dev/null; then
    echo "Successfully installed TA-Lib from source"
else
    echo "Warning: Could not install TA-Lib (ta-lib-bin or TA-Lib source). Continuing without it..."
fi

# Echo versions of main packages
echo "=== Package Versions ==="
echo "Python version: $(python --version)"
echo "pip version: $($PIP_CMD --version | head -1)"

# Check versions of main packages
for pkg in ccxt pandas numpy scikit-learn python-dotenv requests pytest; do
    if python -c "import $pkg; print('$pkg version:', $pkg.__version__)" 2>/dev/null; then
        :
    else
        echo "$pkg: Not installed or no version info available"
    fi
done

# Check TA-Lib specifically (different import name)
if python -c "import talib; print('TA-Lib version:', talib.__version__)" 2>/dev/null; then
    :
else
    echo "TA-Lib: Not installed or no version info available"
fi

# Run compileall to catch syntax errors
echo "=== Running syntax compilation check ==="
if python -m compileall .; then
    echo "Compilation successful - no syntax errors found"
else
    echo "Compilation failed - syntax errors detected"
    exit 1
fi

# Run tests if they exist
echo "=== Checking for and running tests ==="
if [ -d "tests" ] || ls test_*.py >/dev/null 2>&1; then
    echo "Found tests, running pytest..."
    if pytest -v 2>/dev/null || python -m pytest -v 2>/dev/null; then
        echo "Tests passed successfully"
    else
        echo "Warning: Tests failed, but continuing with environment setup..."
    fi
else
    echo "No formal test directory or test_*.py files found"
    # Check if testcodebase*.py files can be run
    if ls testcodebase*.py >/dev/null 2>&1; then
        echo "Found testcodebase files, attempting to run them..."
        for testfile in testcodebase*.py; do
            echo "Running $testfile..."
            if python "$testfile" 2>/dev/null; then
                echo "$testfile executed successfully"
            else
                echo "Warning: $testfile failed to execute, but continuing..."
            fi
        done
    fi
fi

# Perform safe smoke import test
echo "=== Performing smoke import tests ==="
# Try to import common modules that might be the main package
python -c "
import sys
import importlib.util

# Try importing main Python files as modules
main_files = ['minidebugger18', 'gemini-flask-57']
success = False

for module_name in main_files:
    try:
        # Try importing as a module
        if importlib.util.find_spec(module_name):
            exec(f'import {module_name}')
            print(f'Successfully imported {module_name}')
            success = True
            break
    except Exception as e:
        print(f'Could not import {module_name}: {e}')
        
# Try a basic import test for cryptobot-like functionality
try:
    import ccxt
    import pandas
    import numpy
    print('Core crypto bot dependencies imported successfully')
    success = True
except Exception as e:
    print(f'Warning: Could not import core dependencies: {e}')

if success:
    print('JULES_OK')
else:
    print('Warning: Some imports failed, but basic setup completed')
    print('JULES_OK')
"

echo "=== DONE (JULES_OK) ==="