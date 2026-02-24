# Tools Directory

This directory contains Python scripts that perform deterministic execution tasks.

## Principles

- Each tool does one thing well
- Tools are called by the AI agent based on workflow requirements
- Keep tools focused, testable, and reusable
- Use `.env` for all credentials and API keys

## Structure

Each tool should:
- Have clear input/output expectations
- Handle errors gracefully with informative messages
- Log important actions for debugging
- Be documented with usage examples

## Example Tool Template

```python
#!/usr/bin/env python3
"""
Tool Name: example_tool.py
Purpose: Brief description of what this tool does
Usage: python tools/example_tool.py --input "value"
"""

import os
from dotenv import load_dotenv

load_dotenv()

def main():
    # Your tool logic here
    pass

if __name__ == "__main__":
    main()
```

## Common Dependencies

Most tools will need:
```bash
pip install python-dotenv requests
```

Add specific requirements as needed for your tools.
