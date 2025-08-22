#!/usr/bin/env bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

echo "=== Starting Fresh Linux VM Setup for Google Jules ==="

# Install minimal system dependencies
echo "Installing system dependencies..."
if command -v sudo >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y build-essential python3-dev libssl-dev libffi-dev pkg-config git ca-certificates
else
    apt-get update
    apt-get install -y build-essential python3-dev libssl-dev libffi-dev pkg-config git ca-certificates
fi

# Detect project stacks
echo "=== Detecting Project Stacks ==="
PYTHON_DETECTED=false
NODEJS_DETECTED=false  
DOTNET_DETECTED=false
JAVA_DETECTED=false
GO_DETECTED=false

# Python detection
if [ -f "requirements.txt" ] || [ -f "pyproject.toml" ] || ls *.py >/dev/null 2>&1; then
    echo "Python stack detected"
    PYTHON_DETECTED=true
fi

# Node.js detection  
if [ -f "package.json" ]; then
    echo "Node.js stack detected"
    NODEJS_DETECTED=true
fi

# .NET detection
if ls *.csproj >/dev/null 2>&1 || ls *.sln >/dev/null 2>&1; then
    echo ".NET stack detected"
    DOTNET_DETECTED=true
fi

# Java detection
if [ -f "gradlew" ] || [ -f "build.gradle" ] || [ -f "pom.xml" ]; then
    echo "Java stack detected"
    JAVA_DETECTED=true
fi

# Go detection
if [ -f "go.mod" ]; then
    echo "Go stack detected"  
    GO_DETECTED=true
fi

# Python Stack Setup
if [ "$PYTHON_DETECTED" = true ]; then
    echo "=== Setting up Python Stack ==="
    
    # Create and activate virtual environment  
    echo "Setting up Python virtual environment..."
    if command -v uv >/dev/null 2>&1; then
        echo "Using uv for virtual environment creation..."
        uv venv .venv
        source .venv/bin/activate
        PIP_CMD="uv pip"
    else
        echo "Using python3 -m venv for virtual environment creation..."
        python3 -m venv .venv
        source .venv/bin/activate
        PIP_CMD="pip"
    fi

    # Install dependencies
    echo "Installing Python dependencies..."
    if [ -f "requirements.txt" ]; then
        echo "Found requirements.txt, installing dependencies..."
        if ! $PIP_CMD install -r requirements.txt --timeout 30 2>/dev/null; then
            echo "Failed to install from requirements.txt, but continuing..."
        fi
    elif [ -f "pyproject.toml" ]; then
        echo "Found pyproject.toml, installing project..."
        if ! $PIP_CMD install -e . --timeout 30 2>/dev/null; then
            echo "Failed to install from pyproject.toml, but continuing..."
        fi
    else
        echo "No requirements.txt or pyproject.toml found, installing pytest only..."
        if ! $PIP_CMD install pytest --timeout 30 2>/dev/null; then
            echo "Failed to install pytest, but continuing..."
        fi
    fi

    # Compile all Python files
    echo "Compiling all Python files..."
    if python -m compileall . -q; then
        echo "Python compilation successful"
    else
        echo "Python compilation failed - syntax errors detected"
        exit 1
    fi

    # Run tests if they exist
    echo "Running Python tests..."
    if [ -d "tests" ] || ls test_*.py >/dev/null 2>&1 || ls *test*.py >/dev/null 2>&1; then
        echo "Found Python tests, running pytest..."
        if pytest -q 2>/dev/null || python -m pytest -q 2>/dev/null; then
            echo "Python tests passed"
        else
            echo "Python tests failed, but continuing..."
        fi
    else
        echo "No Python tests found, skipping test execution"
    fi
fi

# Node.js Stack Setup
if [ "$NODEJS_DETECTED" = true ]; then
    echo "=== Setting up Node.js Stack ==="
    
    # Install Node.js dependencies
    if [ -f "yarn.lock" ]; then
        echo "Found yarn.lock, using yarn..."
        yarn install --frozen-lockfile 2>/dev/null || echo "Yarn install failed, but continuing..."
    elif [ -f "pnpm-lock.yaml" ]; then
        echo "Found pnpm-lock.yaml, using pnpm..."
        pnpm install --frozen-lockfile 2>/dev/null || echo "pnpm install failed, but continuing..."
    else
        echo "Using npm..."
        if [ -f "package-lock.json" ] || [ -f "npm-shrinkwrap.json" ]; then
            npm ci 2>/dev/null || npm install 2>/dev/null || echo "npm install failed, but continuing..."
        else
            npm install 2>/dev/null || echo "npm install failed, but continuing..."
        fi
    fi

    # Run build if defined
    if npm run build --if-present >/dev/null 2>&1; then
        echo "Node.js build completed"
    else
        echo "No build script found or build failed, continuing..."
    fi

    # Run tests if defined
    if npm run test --if-present >/dev/null 2>&1; then
        echo "Node.js tests passed"
    else
        echo "No test script found or tests failed, continuing..."
    fi
fi

# .NET Stack Setup
if [ "$DOTNET_DETECTED" = true ]; then
    echo "=== Setting up .NET Stack ==="
    
    # Restore dependencies
    echo "Restoring .NET dependencies..."
    if dotnet restore >/dev/null 2>&1; then
        echo ".NET restore completed"
    else
        echo ".NET restore failed, but continuing..."
    fi

    # Build project
    echo "Building .NET project..."
    if dotnet build --no-restore >/dev/null 2>&1; then
        echo ".NET build completed"
    else
        echo ".NET build failed, but continuing..."
    fi

    # Run tests if any exist
    echo "Running .NET tests..."
    if dotnet test --no-build --verbosity quiet >/dev/null 2>&1; then
        echo ".NET tests passed"
    else
        echo "No .NET tests found or tests failed, continuing..."
    fi
fi

# Java Stack Setup
if [ "$JAVA_DETECTED" = true ]; then
    echo "=== Setting up Java Stack ==="
    
    if [ -f "gradlew" ]; then
        echo "Using Gradle wrapper..."
        ./gradlew clean build test --quiet 2>/dev/null || echo "Gradle build/test failed, but continuing..."
    elif command -v gradle >/dev/null 2>&1; then
        echo "Using system Gradle..."
        gradle clean build test --quiet 2>/dev/null || echo "Gradle build/test failed, but continuing..."
    elif [ -f "pom.xml" ]; then
        echo "Using Maven..."
        mvn clean compile test -q 2>/dev/null || echo "Maven build/test failed, but continuing..."
    else
        echo "No Java build tool found, skipping..."
    fi
fi

# Go Stack Setup
if [ "$GO_DETECTED" = true ]; then
    echo "=== Setting up Go Stack ==="
    
    # Tidy dependencies
    echo "Running go mod tidy..."
    if go mod tidy; then
        echo "Go mod tidy completed"
    else
        echo "Go mod tidy failed, but continuing..."
    fi

    # Build all packages
    echo "Building Go packages..."
    if go build ./... >/dev/null 2>&1; then
        echo "Go build completed"
    else
        echo "Go build failed, but continuing..."
    fi

    # Run tests
    echo "Running Go tests..."
    if go test ./... >/dev/null 2>&1; then
        echo "Go tests passed"
    else
        echo "No Go tests found or tests failed, continuing..."
    fi
fi

# Echo versions of relevant tools
echo "=== Tool Versions ==="
if [ "$PYTHON_DETECTED" = true ]; then
    echo "Python version: $(python --version 2>&1)"
    echo "pip version: $(pip --version 2>&1 | head -1)"
fi

if [ "$NODEJS_DETECTED" = true ]; then
    if command -v node >/dev/null 2>&1; then
        echo "Node version: $(node --version 2>&1)"
    fi
    if command -v npm >/dev/null 2>&1; then
        echo "npm version: $(npm --version 2>&1)"
    fi
fi

if [ "$DOTNET_DETECTED" = true ]; then
    if command -v dotnet >/dev/null 2>&1; then
        echo "dotnet version: $(dotnet --version 2>&1)"
    fi
fi

if [ "$JAVA_DETECTED" = true ]; then
    if command -v java >/dev/null 2>&1; then
        echo "Java version: $(java -version 2>&1 | head -1)"
    fi
fi

if [ "$GO_DETECTED" = true ]; then
    if command -v go >/dev/null 2>&1; then
        echo "Go version: $(go version 2>&1)"
    fi
fi

# Final success message
echo "JULES_OK"
exit 0
exit 0