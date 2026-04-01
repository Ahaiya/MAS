Phase: {{ phase }}

## Recent Experiment Log Summary
{{ experiment_log_summary }}

## Current Config Snapshot
{{ current_config_snapshot }}

## Available Probes
{{ available_probes }}

## Last Iteration Verdict
{{ last_verdict }}

## Last Iteration Next Hypothesis
{{ next_hypothesis }}

## Probe Results This Round
{{ probe_results_this_round }}

Instruction:
- If `phase == decision`, output the decision-phase YAML block defined by system prompt.
- If `phase == review`, output the review-phase YAML block defined by system prompt.
- Use Simplified Chinese for user-visible natural-language string values.
