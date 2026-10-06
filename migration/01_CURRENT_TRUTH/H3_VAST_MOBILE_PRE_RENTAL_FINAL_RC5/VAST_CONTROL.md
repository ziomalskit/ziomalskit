# Vast Control behavior

## Safe shutdown modes

### Stop after current
No new render job is started after arming the plan.
The current render is allowed to finish, then the instance is stopped.

### Stop after queue
Prompt and render queues finish.
`pending_review` and `rejected` jobs do not block shutdown.

### Finish queue & destroy compute
Same queue semantics as above, then destroys the Vast instance.
This action is available only when the controller verifies a separate persistent volume mount.

The controller does NOT delete the persistent volume.

## Idle stop
If there is no prompt/render work for N minutes, stop the instance.
Pending review does not count as busy.

## Cost guard
If the approximate session compute cost reaches the configured threshold,
the controller arms `stop_after_current`.

## Why there is no START button
Once the instance is stopped the controller is stopped too. A START button served by that same controller would be unreachable.
Use Vast console/CLI, or later add the optional external home control-plane.
