#!/usr/bin/env bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

# Prepare the workspace directory
APP_DIR="${APP_DIR:-/app}"
cd /
if [ -d "$APP_DIR" ] && [ "$(ls -A "$APP_DIR" 2>/dev/null)" ]; then
    rm -rf "$APP_DIR"
fi
mkdir -p "$APP_DIR"
chown 1001:1001 "$APP_DIR"

# Configure Git globally
git config --global core.hooksPath /dev/null

# Add https/ssh rewrite rules if provided by environment variables
if [ -n "${GIT_URL_REWRITE_HTTPS:-}" ]; then
    git config --global url."${GIT_URL_REWRITE_HTTPS}".insteadOf https://
fi
if [ -n "${GIT_URL_REWRITE_SSH:-}" ]; then
    git config --global url."${GIT_URL_REWRITE_SSH}".insteadOf ssh://
fi

# Clone the repository from $REPO_URL into $APP_DIR
if [ -n "${REPO_URL:-}" ]; then
    git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

# Install common system dependencies via apt-get
if command -v sudo >/dev/null 2>&1; then
    SUDO_CMD="sudo"
else
    SUDO_CMD=""
fi

$SUDO_CMD apt-get update
$SUDO_CMD apt-get install -y \
    build-essential \
    python3 \
    python3-dev \
    python3-venv \
    python3-pip \
    pkg-config \
    libssl-dev \
    libffi-dev \
    git \
    curl \
    ca-certificates \
    jq

# Detect stack automatically
PYTHON_DETECTED=false
NODE_DETECTED=false
DOTNET_DETECTED=false
JAVA_DETECTED=false
GO_DETECTED=false

# Python: if requirements.txt, pyproject.toml, or .py files exist
if [ -f "requirements.txt" ] || [ -f "pyproject.toml" ] || ls *.py >/dev/null 2>&1; then
    PYTHON_DETECTED=true
fi

# Node: if package.json exists
if [ -f "package.json" ]; then
    NODE_DETECTED=true
fi

# .NET: if *.csproj or *.sln files exist
if ls *.csproj >/dev/null 2>&1 || ls *.sln >/dev/null 2>&1; then
    DOTNET_DETECTED=true
fi

# Java: if gradlew/build.gradle/pom.xml exists
if [ -f "gradlew" ] || [ -f "build.gradle" ] || [ -f "pom.xml" ]; then
    JAVA_DETECTED=true
fi

# Go: if go.mod exists
if [ -f "go.mod" ]; then
    GO_DETECTED=true
fi

# For each detected stack
if [ "$PYTHON_DETECTED" = true ]; then
    echo "Python stack detected"
    
    # Create venv (use uv if present)
    if command -v uv >/dev/null 2>&1; then
        uv venv .venv || python3 -m venv .venv
        . .venv/bin/activate
        PIP_CMD="uv pip"
    else
        python3 -m venv .venv
        . .venv/bin/activate
        # Bootstrap pip with ensurepip
        python3 -m ensurepip --upgrade || true
        PIP_CMD="pip"
    fi
    
    # Install deps
    if [ -f "requirements.txt" ]; then
        $PIP_CMD install -r requirements.txt || true
    fi
    if [ -f "pyproject.toml" ]; then
        $PIP_CMD install -e . || true
    fi
    
    # Compile sources
    python3 -m compileall . || true
    
    # Run pytest if present
    if command -v pytest >/dev/null 2>&1; then
        pytest || true
    fi
fi

if [ "$NODE_DETECTED" = true ]; then
    echo "Node stack detected"
    
    # Install Node.js if not present
    if ! command -v node >/dev/null 2>&1; then
        curl -fsSL https://deb.nodesource.com/setup_lts.x | $SUDO_CMD -E bash - || true
        $SUDO_CMD apt-get install -y nodejs || true
    fi
    
    # Install with npm/yarn/pnpm
    if command -v pnpm >/dev/null 2>&1; then
        pnpm install || true
    elif command -v yarn >/dev/null 2>&1; then
        yarn install || true
    elif command -v npm >/dev/null 2>&1; then
        npm install || true
    fi
    
    # Run build/test if defined
    if [ -f "package.json" ]; then
        if grep -q '"build"' package.json 2>/dev/null; then
            npm run build || true
        fi
        if grep -q '"test"' package.json 2>/dev/null; then
            npm test || true
        fi
    fi
fi

if [ "$DOTNET_DETECTED" = true ]; then
    echo ".NET stack detected"
    
    # Install .NET if not present
    if ! command -v dotnet >/dev/null 2>&1; then
        wget -q https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/packages-microsoft-prod.deb -O packages-microsoft-prod.deb || true
        $SUDO_CMD dpkg -i packages-microsoft-prod.deb || true
        $SUDO_CMD apt-get update || true
        $SUDO_CMD apt-get install -y dotnet-sdk-8.0 || true
        rm -f packages-microsoft-prod.deb || true
    fi
    
    if command -v dotnet >/dev/null 2>&1; then
        dotnet restore || true
        dotnet build || true
        dotnet test || true
    fi
fi

if [ "$JAVA_DETECTED" = true ]; then
    echo "Java stack detected"
    
    # Install Java if not present
    if ! command -v java >/dev/null 2>&1; then
        $SUDO_CMD apt-get install -y default-jdk || true
    fi
    
    # Gradle or Maven build/test
    if [ -f "gradlew" ]; then
        chmod +x gradlew || true
        ./gradlew build || true
        ./gradlew test || true
    elif [ -f "build.gradle" ] && command -v gradle >/dev/null 2>&1; then
        gradle build || true
        gradle test || true
    elif [ -f "pom.xml" ] && command -v mvn >/dev/null 2>&1; then
        mvn compile || true
        mvn test || true
    fi
fi

if [ "$GO_DETECTED" = true ]; then
    echo "Go stack detected"
    
    # Install Go if not present
    if ! command -v go >/dev/null 2>&1; then
        GO_VERSION="1.21.5"
        wget -q "https://golang.org/dl/go${GO_VERSION}.linux-amd64.tar.gz" -O go.tar.gz || true
        $SUDO_CMD tar -C /usr/local -xzf go.tar.gz || true
        export PATH="/usr/local/go/bin:$PATH" || true
        rm -f go.tar.gz || true
    fi
    
    if command -v go >/dev/null 2>&1; then
        go mod tidy || true
        go build ./... || true
        go test ./... || true
    fi
fi

echo "JULES_OK"