Phase: {{ phase }}

## Recent Experiment Log Summary
{{ experiment_log_summary }}

## Current Config Snapshot
{{ current_config_snapshot }}

Important:
- Use the exact active task file paths shown above.
- Do not propose edits to the bundle or any file under `configs/rubrics/`.

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
