## Architecture Boundary Checklist

- [ ] Orchestrator changes contain coordination only: no configuration reads, parsing, I/O, side effects, or business execution.
- [ ] Workflow database writes occur only in activities; HTTP handlers use persistence only for API reads and permitted request lookup.
- [ ] Foundry invocation and response validation remain inside an agent activity boundary.
- [ ] Approval APIs raise `ApprovalDecision` without mutating workflow or approval state.
- [ ] Assignment remains reachable only through `AssignTicketActivity` with idempotency key `assign:<ticket_id>`.
- [ ] Every UI action uses a documented `/api/...` endpoint.
- [ ] Architecture tests, phase tests, and the full regression suite pass.