#!/bin/bash

# Crypto Trading Bot - Otomatik Kurulum Script
# Ubuntu/Debian için optimize edilmiştir

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   error "This script should not be run as root"
   exit 1
fi

log "🚀 Starting Crypto Trading Bot Setup..."

# System info
log "📊 System Information:"
echo "OS: $(lsb_release -d | cut -f2)"
echo "Architecture: $(uname -m)"
echo "User: $(whoami)"
echo "Home: $HOME"

# Update system
log "🔄 Updating system packages..."
sudo apt-get update -y
sudo apt-get upgrade -y

# Install system dependencies
log "📦 Installing system dependencies..."
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    wget \
    curl \
    git \
    unzip \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release

# Check Python version
PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1-2)
log "🐍 Python version: $PYTHON_VERSION"

if [[ $(echo "$PYTHON_VERSION >= 3.8" | bc -l) -eq 0 ]]; then
    error "Python 3.8+ required, found $PYTHON_VERSION"
    exit 1
fi

# Install Docker (optional)
read -p "Install Docker? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    log "🐳 Installing Docker..."
    
    # Remove old versions
    sudo apt-get remove docker docker-engine docker.io containerd runc 2>/dev/null || true
    
    # Install Docker
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    
    # Add user to docker group
    sudo usermod -aG docker $USER
    
    log "✅ Docker installed successfully"
    warn "You need to logout and login again to use Docker without sudo"
fi

# Create project directory
PROJECT_DIR="$HOME/crypto-trading-bot"
log "📁 Creating project directory: $PROJECT_DIR"

if [[ -d "$PROJECT_DIR" ]]; then
    warn "Directory already exists, backing up..."
    mv "$PROJECT_DIR" "${PROJECT_DIR}.backup.$(date +%s)"
fi

mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

# Create Python virtual environment
log "🐍 Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
log "⬆️ Upgrading pip..."
pip install --upgrade pip setuptools wheel

# Install TA-Lib
log "📊 Installing TA-Lib..."

# Download and compile TA-Lib
wget -q http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz
tar -xzf ta-lib-0.4.0-src.tar.gz
cd ta-lib/

log "🔨 Compiling TA-Lib (this may take a few minutes)..."
./configure --prefix=/usr
make
sudo make install
cd ..

# Clean up
rm -rf ta-lib/ ta-lib-0.4.0-src.tar.gz

# Install Python packages
log "📦 Installing Python dependencies..."
pip install ccxt==4.1.45
pip install pandas==2.1.3
pip install TA-Lib==0.4.28
pip install numpy==1.25.2
pip install python-dotenv==1.0.0
pip install schedule==1.2.0

# Create requirements.txt
cat > requirements.txt << EOF
ccxt==4.1.45
pandas==2.1.3
TA-Lib==0.4.28
numpy==1.25.2
python-dotenv==1.0.0
schedule==1.2.0
EOF

# Create directory structure
log "📁 Creating directory structure..."
mkdir -p logs data

# Create .env file
log "⚙️ Creating configuration files..."
cat > .env.example << 'EOF'
# Bybit API credentials
BYBIT_API_KEY=your_api_key_here
BYBIT_API_SECRET=your_api_secret_here

# Trading configuration
TESTNET=true
SYMBOLS=BTC/USDT:USDT,ETH/USDT:USDT
LEVERAGE=3
RISK_PER_TRADE=0.01

# Strategy parameters
RSI_OVERSOLD=30
RSI_OVERBOUGHT=60
VOLUME_MULTIPLIER=1.8
TAKE_PROFIT=0.06
STOP_LOSS=0.03

# Risk management
MAX_CONSECUTIVE_LOSSES=3
PAUSE_HOURS=24
EOF

# Copy .env.example to .env if not exists
if [[ ! -f .env ]]; then
    cp .env.example .env
    log "📝 Created .env file from template"
fi

# Create Dockerfile
cat > Dockerfile << 'EOF'
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    wget build-essential && \
    rm -rf /var/lib/apt/lists/*

RUN wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz && \
    tar -xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib/ && \
    ./configure --prefix=/usr && \
    make && make install && \
    cd .. && rm -rf ta-lib/ ta-lib-0.4.0-src.tar.gz

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p logs

CMD ["python", "bot.py"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import ccxt; print('Bot is healthy')" || exit 1
EOF

# Create docker-compose.yml
cat > docker-compose.yml << 'EOF'
version: '3.8'

services:
  crypto-bot:
    build: .
    container_name: crypto-trading-bot
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    environment:
      - TZ=UTC
    healthcheck:
      test: ["CMD", "python", "-c", "import os; exit(0 if os.path.exists('logs/system.log') else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
EOF

# Create systemd service file template
cat > crypto-bot.service << EOF
[Unit]
Description=Crypto Trading Bot
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
Restart=always
RestartSec=30
User=$USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/bot.py
Environment=PYTHONPATH=$PROJECT_DIR

StandardOutput=append:/var/log/crypto-bot.log
StandardError=append:/var/log/crypto-bot-error.log

LimitNOFILE=65536
MemoryLimit=512M

[Install]
WantedBy=multi-user.target
EOF

# Create .gitignore
cat > .gitignore << 'EOF'
# Environment files
.env
.env.local
.env.production

# Logs
logs/
*.log

# Python cache
__pycache__/
*.py[cod]
*$py.class

# Virtual environments
venv/
env/

# Data files
data/
*.csv
*.json

# OS
.DS_Store
Thumbs.db
EOF

# Create startup script
cat > start_bot.sh << 'EOF'
#!/bin/bash

cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate

# Check if .env exists
if [[ ! -f .env ]]; then
    echo "❌ .env file not found. Copy .env.example to .env and configure it."
    exit 1
fi

# Check if API keys are configured
if grep -q "your_api_key_here" .env; then
    echo "❌ Please configure your API keys in .env file"
    exit 1
fi

# Start bot
echo "🚀 Starting Crypto Trading Bot..."
python bot.py
EOF

chmod +x start_bot.sh

# Create stop script
cat > stop_bot.sh << 'EOF'
#!/bin/bash

echo "🛑 Stopping Crypto Trading Bot..."

# Find and kill bot processes
pkill -f "python.*bot.py"

# Wait a moment
sleep 2

# Force kill if still running
pkill -9 -f "python.*bot.py" 2>/dev/null || true

echo "✅ Bot stopped"
EOF

chmod +x stop_bot.sh

# Set permissions
log "🔐 Setting permissions..."
chmod 755 logs/
chmod 644 .env.example
chmod 600 .env 2>/dev/null || true

# Verify installation
log "✅ Verifying installation..."

# Test Python imports
python -c "import ccxt, pandas, talib, numpy, dotenv; print('✅ All Python packages installed successfully')" || {
    error "Python package verification failed"
    exit 1
}

# Final message
log "🎉 Installation completed successfully!"
echo
echo "📋 Next steps:"
echo "1. Configure your API keys in .env file:"
echo "   nano .env"
echo
echo "2. Test the installation:"
echo "   ./start_bot.sh"
echo
echo "3. Run backtest:"
echo "   source venv/bin/activate"
echo "   python backtest.py"
echo
echo "4. For production deployment:"
echo "   sudo cp crypto-bot.service /etc/systemd/system/"
echo "   sudo systemctl enable crypto-bot"
echo "   sudo systemctl start crypto-bot"
echo
echo "📁 Project directory: $PROJECT_DIR"
echo "🔧 Virtual environment: $PROJECT_DIR/venv"
echo "📊 Logs directory: $PROJECT_DIR/logs"
echo
warn "⚠️  Don't forget to:"
warn "   - Configure .env with your Bybit testnet API keys"
warn "   - Test thoroughly before using real money"
warn "   - Always start with testnet mode"
echo
log "🚀 Happy trading!"