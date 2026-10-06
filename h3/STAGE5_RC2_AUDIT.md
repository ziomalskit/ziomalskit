# Stage 5 RC2 audit findings

The previous Stage 5 was not yet safe enough to call a release candidate.

Fixed in RC2:

1. **Critical security:** the public mobile panel had no built-in authentication while exposing STOP/DESTROY endpoints.
   RC2 adds fail-closed HTTP Basic auth with generated credentials.

2. **Critical persistence check:** detecting any separate volume did not prove that H3 state/models/outputs lived on it.
   RC2 verifies each protected path is under and mounted from the declared persistent volume.

3. **Shutdown race:** STOP AFTER CURRENT blocked new renders but allowed new prompt jobs.
   RC2 stops dispatching both prompt and render work after the current render.

4. **Lifecycle failure state:** failed stop/destroy could leave `executing_*` forever.
   RC2 records `action_failed`; stale executing state after controller restart becomes `action_interrupted`.

5. **Render waiting on LLM:** reviewed separately.
   The workflow's Resolve Final Prompt uses ComfyUI `ComfySwitchNode`; current ComfyUI declares both data branches lazy and requests only the selected branch.
   With non-empty Prompt Override, the unused LLM branch should therefore not execute at render time.

Offline tests pass. Full end-to-end status remains pending a live Vast instance.
