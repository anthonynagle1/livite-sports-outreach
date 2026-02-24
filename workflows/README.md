# Workflows Directory

This directory contains markdown SOPs (Standard Operating Procedures) that define what to do and how.

## What Belongs Here

Each workflow is a set of instructions for accomplishing a specific objective. Think of these as briefing documents you'd give to a teammate.

## Workflow Template

A good workflow should include:

1. **Objective**: What are we trying to accomplish?
2. **Required Inputs**: What information/data is needed to start?
3. **Tools Needed**: Which scripts in `tools/` will be used?
4. **Steps**: The sequence of actions to take
5. **Expected Outputs**: What should exist when complete?
6. **Edge Cases**: Known issues, rate limits, failure modes
7. **Success Criteria**: How to verify it worked

## Example Workflow Structure

```markdown
# Workflow: [Name]

## Objective
Brief description of what this accomplishes

## Required Inputs
- Input 1: Description
- Input 2: Description

## Tools
- `tools/script_name.py`

## Steps
1. First step
2. Second step
3. Third step

## Expected Outputs
- Output location and format

## Edge Cases
- Known limitation 1
- Known limitation 2

## Success Criteria
How to verify completion
```

## Living Documents

Workflows should evolve as you learn better approaches. When you discover constraints or better methods, update the workflow so future executions benefit from that knowledge.
