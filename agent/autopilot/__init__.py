"""URL autopilot with three human gates.

1. Plan, after research.
2. Companies, after ICP match and dedupe.
3. Enriched list. The outreach sequence is optional at this gate.

A failed checker can change inputs and retry inside a stretch. The checkers
themselves stay as they are. Each pause carries an assistant note.
"""

from agent.autopilot.orchestrator import approve, execute, reject, start, status

__all__ = ["approve", "execute", "reject", "start", "status"]
