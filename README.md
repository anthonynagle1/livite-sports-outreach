# Livite Sports Outreach

This project uses the **WAT framework** (Workflows, Agents, Tools) for reliable AI-assisted automation.

## Quick Start

1. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your actual API keys
   ```

2. **Install dependencies** (as needed for your tools)
   ```bash
   pip install -r requirements.txt  # Create this when you add Python dependencies
   ```

3. **Understand the architecture**
   - Read [CLAUDE.md](CLAUDE.md) for complete agent instructions
   - Workflows in `workflows/` define what to do
   - Tools in `tools/` execute the actual work
   - `.tmp/` holds temporary processing files

## Directory Structure

```
.tmp/           # Temporary files (regenerated as needed, gitignored)
tools/          # Python scripts for deterministic execution
workflows/      # Markdown SOPs defining processes
.env            # API keys and credentials (gitignored)
CLAUDE.md       # Agent operating instructions
```

## How It Works

The WAT framework separates concerns:
- **Workflows** provide instructions in plain language
- **Agents** (AI) make decisions and orchestrate
- **Tools** execute deterministic tasks reliably

This architecture keeps AI focused on reasoning while delegating execution to reliable scripts, maintaining high accuracy even in multi-step processes.

## Getting Started

Create your first workflow in `workflows/` and corresponding tool in `tools/`. See [CLAUDE.md](CLAUDE.md) for detailed operating principles.
