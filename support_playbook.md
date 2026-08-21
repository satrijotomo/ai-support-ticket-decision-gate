# Support Playbook

Use these steps as guidance only when they apply to the submitted ticket. Do not invent observations or claim that a step was completed.

## Access and authorization failures

1. Confirm the affected users, services, environments, and the exact error code.
2. Check whether a deployment, identity policy, role assignment, group membership, or application registration changed near the start of the incident.
3. Compare one affected user with one unaffected user without exposing credentials or personal data.
4. Review authentication and authorization logs using approved administrative tools.
5. Prefer reversible configuration changes and require approval before changing production access.

## Service incidents

1. Establish scope, start time, customer impact, and whether a workaround exists.
2. Check service health, recent deployments, dependency health, and monitoring alerts.
3. Preserve relevant timestamps, correlation IDs, and sanitized error messages for escalation.
4. Communicate confirmed facts, current impact, next diagnostic action, and the next update time.

## Safety

- Never request passwords, access tokens, private keys, or other secrets.
- Do not recommend destructive data changes without backups and explicit approval.
- Escalate suspected security incidents to SecurityOperations.
- Clearly list missing information when the ticket does not support a conclusion.
