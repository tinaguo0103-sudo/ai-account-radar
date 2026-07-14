# Judgment And Style Reference Contract

This file is a public synchronization contract, not a case library and not candidate evidence.

At runtime, the current task receives only retrieved private examples that help with the needed judgment operation, such as:

- noticing a public contradiction;
- rejecting a familiar but shallow take;
- comparing evidence strength;
- explaining why an unfamiliar audience would care;
- writing a natural public judgment without copying the source.

Examples must remain reference-only. They cannot supply facts, claims, entities, outcomes, eligibility, source hooks, or proof for a candidate. Candidate-facing outputs must not contain case names, customer names, case IDs, citations, anchors, or claims of prior experience unless independently supported by the opened source and research dossier.

The Git-managed Skill and this contract are the release source. Private material stays outside Git. A production release must explicitly sync the Skill contract, then read back and compare its SHA256; it must never copy the private Word or generated private bundle into the repository.
